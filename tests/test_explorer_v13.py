import json
from pathlib import Path

import pytest
from test_engine import sample

from truth_seeker.explorer import DATA_TAG, check_data
from truth_seeker.layout_quality import layout_metrics
from truth_seeker.model import LayoutError, ValidationError
from truth_seeker.render import (
    check_saved_artifacts,
    fingerprint,
    render_html,
    render_svg,
)
from truth_seeker.render_run import render_run
from truth_seeker.scene import check_scene, layout
from truth_seeker.status import status
from truth_seeker.store import (
    add_task,
    atomic_json,
    commit_generation,
    create_run,
    resume,
    update_task,
)


def graph():
    data = sample()
    data["nodes"][0]["aliases"] = ["Fixture ２", "</script><script>throw 1</script>"]
    data["evidence"][0]["source_url"] = "https://example.org/fixture"
    return data


def test_explorer_payload_matches_scene_and_preserves_sources():
    data = graph()
    scene = layout(data, "fixture", "回帰テスト")
    svg = render_svg(scene)
    document = render_html(scene, svg)
    check_data(scene, document)
    embedded = document.split(DATA_TAG, 1)[1].split("</script>", 1)[0]
    assert "<" not in embedded
    payload = json.loads(embedded)
    assert payload["nodes"][0]["aliases"] == data["nodes"][0]["aliases"]
    assert payload["edges"][0]["source_node_id"] == "node_old"
    assert payload["edges"][0]["target_node_id"] == "node_new"
    assert payload["edges"][0]["direction_meaning"] == "架空の仮説接続"
    assert payload["sources"][0]["source_url"] == "https://example.org/fixture"
    assert payload["research"]["claims"][0]["assessment"] == "hypothetical"
    assert svg in document
    with pytest.raises(LayoutError, match="differs"):
        check_data(scene, document.replace("Fixture ２", "tampered", 1))
    scene["nodes"][0]["aliases"] = ["tampered"]
    with pytest.raises(LayoutError, match="aliases"):
        check_scene(scene, data)


def bundle(run):
    return {file.name: fingerprint(file) for file in (run / "render").glob("map.*")}


def test_render_reuse_preserves_graph_siblings_and_failed_output(tmp_path):
    run = create_run(tmp_path, "部分再生成のテスト")
    generation = commit_generation(run, graph())
    snapshot_path = run / "snapshots" / generation / "index.json"
    snapshot = fingerprint(snapshot_path)
    result = render_run(run, "distribution", "all")
    assert set(result["formal_formats"]) == {"html", "pdf", "svg"}
    old = bundle(run)
    progress = fingerprint(run / "progress.json")

    draft = render_run(run, "distribution", "layout")
    assert draft["status"] == "layout_saved"
    assert bundle(run) == old
    render_run(run, "distribution", "all", from_scene=True)
    assert not Path(draft["scene_path"]).exists()
    old = bundle(run)
    (run / "render" / "map.html").unlink()
    render_run(run, "distribution", "html")
    assert bundle(run) == old
    render_run(run, "distribution", "svg")
    assert bundle(run) == old
    with pytest.raises(LayoutError, match="existing artifacts preserved") as exc:
        render_run(run, "distribution", "pdf", force_pdf_error=True)
    assert "injected PDF failure" in str(exc.value.issues)
    assert bundle(run) == old
    result = render_run(run, "distribution", "pdf")
    assert result["primary_format"] == "pdf"
    scene = json.loads((run / "render" / "scene.json").read_text())
    assert set(check_saved_artifacts(scene, run / "render")) == {"html", "pdf", "svg"}
    assert fingerprint(snapshot_path) == snapshot
    assert fingerprint(run / "progress.json") == progress


def test_reject_stale_scene_profile_and_dependencies(tmp_path):
    from shutil import copytree

    run = create_run(tmp_path, "依存関係のテスト")
    commit_generation(run, graph())
    render_run(run, "distribution", "all")
    copytree(run / "render", run / "render-local")
    with pytest.raises(ValidationError, match="different image profile"):
        render_run(run, "local", "html")
    directory = run / "render"
    scene_file = directory / "scene.json"
    scene = json.loads(scene_file.read_text())
    scene["render_inputs"]["raster_sha256"] = {"asset_fixture": "stale"}
    atomic_json(scene_file, scene)
    with pytest.raises(ValidationError, match="dependencies changed"):
        render_run(run, "distribution", "html")
    data = graph()
    data["nodes"][1]["summary"] = "次の確定済みスナップショット"
    commit_generation(run, data)
    with pytest.raises(ValidationError, match="different generation"):
        render_run(run, "distribution", "html")
    assert status(run)["summary"]["outputs"]["distribution"]["state"] == "stale"


def test_status_summarizes_tasks_counts_and_interruption(tmp_path):
    run = create_run(tmp_path, "状態のテスト")
    gen = commit_generation(run, graph())
    interrupted = add_task(run, "検証の続き", priority=10)
    blocked = add_task(run, "出典の再確認", priority=5)
    update_task(run, interrupted["task_id"], "running")
    update_task(run, blocked["task_id"], "blocked", last_error="fixture")
    assert status(run)["summary"]["tasks"]["running"] == 1
    resume(run)
    summary = status(run)["summary"]
    assert summary["tasks"]["retryable"] == 1
    assert summary["tasks"]["blocked"] == 1
    assert [task["topic"] for task in summary["unresolved_tasks"]][:2] == [
        "検証の続き",
        "出典の再確認",
    ]
    assert summary["unresolved_tasks"][1]["last_error"] == "fixture"
    assert summary["image_candidates"] == summary["acquired_images"] == 0
    assert summary["distinct_source_urls"] == summary["evidence_records"] == 1
    assert (summary["adopted_nodes"], summary["adopted_edges"]) == (3, 1)
    assert summary["resume_from_generation"] == gen


def test_metrics_distinguish_crossings_endpoint_contacts_and_coverage():
    def edge(id, a, d):
        return {
            "edge_id": id,
            "source_node_id": id,
            "target_node_id": id,
            "path": [a, a, d, d],
        }

    scene = {
        "canvas_width": 100,
        "canvas_height": 100,
        "nodes": [],
        "expected_node_ids": [],
        "edges": [
            edge("a", (0, 0), (10, 10)),
            edge("b", (0, 10), (10, 0)),
            edge("c", (10, 0), (15, -3)),
        ],
        "expected_edge_ids": ["a", "b", "c"],
    }
    metric = layout_metrics(scene)
    assert metric["crossing_pairs"] == 1
    assert metric["total_edge_length_pt"] == 34.12
    assert metric["edges"] == {"drawn": 3, "expected": 3}
    assert metric["edge_node_collisions"] == metric["card_overlaps"] == 0


def test_image_counts_and_changed_files_invalidate_saved_scene(tmp_path):
    from PIL import Image
    from test_image_catalog import image_file, item
    from test_photo_qa import add_photo

    from truth_seeker.images import save_catalog

    run = create_run(tmp_path, "画像のテスト")
    data = graph()
    photo = add_photo(run, data, "fixture", "navy")
    gen = commit_generation(run, data)
    save_catalog(
        run,
        {
            "candidates": [
                item("cover", image_file(run, "cover.png", "orange")),
                item("missing"),
            ]
        },
    )
    render_run(run, "distribution", "all")
    summary = status(run)["summary"]
    assert (summary["image_candidates"], summary["acquired_images"]) == (2, 1)
    assert summary["outputs"]["distribution"]["placed_images"] == 1
    assert summary["outputs"]["local"]["state"] == "missing"
    qa = {"status": "complete_limited_review", "generation": gen}
    atomic_json(run / "qa" / "illustration-result.json", qa)
    output = status(run)["summary"]["outputs"]["distribution"]
    assert output["qa"] == qa and output["qa_matches_active_generation"]
    Image.new("RGB", (135, 90), "green").save(run / photo["local_path"])
    with pytest.raises(ValidationError, match="dependencies changed"):
        render_run(run, "distribution", "html")
    assert status(run)["summary"]["outputs"]["distribution"]["state"] == "stale"


def test_full_pdf_failure_cannot_publish_into_a_different_generation(tmp_path):
    run = create_run(tmp_path, "世代のテスト")
    commit_generation(run, graph())
    render_run(run, "distribution", "all")
    original = bundle(run)
    old_receipt = fingerprint(run / "render" / "render_result.json")
    data = graph()
    # The drawn content is identical, but the records are from a different generation.
    data["nodes"][-1]["summary"] = "保留中の対象だけを更新"
    commit_generation(run, data)
    with pytest.raises(LayoutError, match="existing bundle preserved"):
        render_run(run, "distribution", "all", force_pdf_error=True)
    assert bundle(run) == original
    assert fingerprint(run / "render" / "render_result.json") == old_receipt


def write_browser_fixtures(directory):
    from copy import deepcopy

    directory.mkdir(parents=True, exist_ok=True)
    for count in (3, 1200):
        data = graph()
        previous = data["nodes"][1]["id"]
        for index in range(count - 3):
            node = deepcopy(data["nodes"][1])
            node.update(id=f"node_fixture_{index}", label=f"Fixture {index}")
            node["display_date"]["anchor_year"] = 1951 + index
            data["nodes"].append(node)
            edge = deepcopy(data["edges"][0])
            edge.update(
                id=f"edge_fixture_{index}",
                source_id=previous,
                target_id=node["id"],
                relation_type="dependency",
            )
            data["edges"].append(edge)
            previous = node["id"]
        scene = layout(data, "fixture", "テスト用の架空地図")
        (directory / f"map-{count}.html").write_text(
            render_html(scene, render_svg(scene)), encoding="utf-8"
        )


if __name__ == "__main__":
    import sys

    write_browser_fixtures(Path(sys.argv[1]))
