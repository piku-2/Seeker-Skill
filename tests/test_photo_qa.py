"""Documentary image coverage tests using fictional fixtures."""

import hashlib
from copy import deepcopy

import pytest
from PIL import Image
from test_dynamic_layout import figure_data
from test_engine import sample
from test_illustration_qa import (
    RIGHTS,
    SOURCE,
    assess,
    render_fixture,
    representative_fixture,
    review_fixture,
    search_fixture,
)
from test_layout import add_node

from truth_seeker.cli import main
from truth_seeker.model import LayoutError, ValidationError, load_generation
from truth_seeker.photos import opportunities, validate_photo_search
from truth_seeker.render import check_saved_artifacts, emit, layout
from truth_seeker.store import (
    commit_generation,
    create_run,
    illustration_task,
    read_progress,
    resume,
    save_illustrations,
    save_representatives,
)


def add_photo(run, data, key, color, *, owner="node_old"):
    path = run / "staging" / (key + ".jpg")
    Image.new("RGB", (135, 90), color).save(path)
    asset = {
        "id": "asset_" + key,
        "schema_version": "0.5",
        "asset_type": "photo",
        "node_id": owner,
        "local_path": str(path.relative_to(run)),
        "caption": "架空の写真",
        "source_page_url": SOURCE,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "license": "CC0",
    }
    data["assets"].append(asset)
    return asset


def adopted_candidate(report, asset, key):
    return {
        **report["candidates"][0],
        "id": "candidate_" + key,
        "identifier": key,
        "decision": "adopted",
        "rights": "cleared",
        "rights_note": "CC0",
        "rights_url": RIGHTS,
        "acquisition": "verified",
        "asset_id": asset["id"],
    }


def test_generated_and_external_rasters_do_not_cover_representative_photos(tmp_path):
    run = create_run(tmp_path, "架空の図表のみ")
    data = figure_data("table")
    asset = data["assets"][0]
    external = add_photo(run, data, "external", "navy")
    external["asset_type"] = "diagram"
    report = search_fixture()
    report["candidates"].append(adopted_candidate(report, external, "external"))
    report["candidates"][-1]["media_kind"] = "diagram"
    source = data["evidence"][0]["source_url"]
    report["searches"].append(
        {
            "id": "table",
            "query": "架空資料の表",
            "checked_urls": [source],
            "result": "比較可能",
        }
    )
    report["targets"][0]["search_ids"].append("table")
    report["targets"].append(
        {
            "node_id": "node_new",
            "search_ids": ["table"],
            "reason": "架空表の関連対象",
            "outcome": "照合",
        }
    )
    report["candidates"].append(
        {
            "id": "table",
            "search_id": "table",
            "node_ids": ["node_old", "node_new"],
            "source_url": source,
            "decision": "adopted",
            "rights": "original",
            "rights_note": "自作",
            "reason": "比較表を作成",
            "asset_id": asset["id"],
        }
    )
    report["figure_plan"]["decisions"].append(
        {
            "id": "table",
            "node_ids": ["node_old", "node_new"],
            "kind": "table",
            "value": "年代と場所の照合",
            "decision": "adopted",
            "reason": "比較",
            "asset_id": asset["id"],
        }
    )
    gen = commit_generation(run, data)
    save_illustrations(run, report, complete=True)
    reps = representative_fixture()
    reps["candidates"][0]["node_ids"] = ["node_new"]
    save_representatives(run, reps, complete=True)
    scene = render_fixture(run, data)
    review = review_fixture(gen, [asset["id"], external["id"]])
    review["figure_review"]["assets"][asset["id"]] = {
        "data_matches": True,
        "uncertainty_clear": True,
        "findings": "架空データ照合済み",
    }
    result = assess(run, data, scene, review)
    assert result["figures"]["status"] == "pass"
    assert result["photos"]["photo_count"] == 0
    assert (
        result["photos"]["external_raster_count"]
        == result["photos"]["generated_figure_count"]
        == 1
    )
    assert "missing required target" in result["photos"]["reason"]
    assert result["status"] == "incomplete"


def test_low_importance_artifact_and_representative_each_need_assessment():
    data = sample()
    data["nodes"][1]["type"] = "person"
    reps = representative_fixture()
    assert {r["node_id"] for r in opportunities(data, reps)} == {"node_old", "node_new"}
    report = search_fixture()
    with pytest.raises(ValidationError, match="missing required target"):
        validate_photo_search(report, data, reps)
    data["nodes"][1]["type"] = "concept"
    report["searches"].append(
        {**report["searches"][0], "id": "s_blocked", "execution": "blocked"}
    )
    report["photo_plan"]["targets"][0]["search_ids"].append("s_blocked")
    assert validate_photo_search(report, data, reps)["blocked_search_count"] == 1
    report["photo_plan"]["targets"][0]["search_ids"] = ["s_blocked"]
    with pytest.raises(ValidationError, match="no verifiable search"):
        validate_photo_search(report, data, reps)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"decision": "pending"}, "unresolved candidate"),
        (
            {"rights": "cleared", "rights_url": RIGHTS, "rights_note": "CC0"},
            "without a justified decision",
        ),
        (
            {
                "rights": "cleared",
                "rights_url": SOURCE + "/missing",
                "rights_note": "CC0",
            },
            "lacks reviewed reuse",
        ),
        (
            {"acquisition": "failed", "rejection": "acquisition_failed"},
            "no recorded acquisition attempt",
        ),
        ({"depicted_node_ids": ["node_new"]}, "lacks classification, subject"),
        (
            {"rejection": "redundant", "replacement_asset_id": "asset_missing"},
            "without a justified decision",
        ),
    ],
)
def test_documentary_candidate_cannot_be_abandoned_without_evidence(patch, message):
    report = search_fixture()
    candidate = report["candidates"][0]
    candidate.update(
        acquisition="not_attempted",
        rights="unknown",
        useful=True,
        rejection="rights_unavailable",
        decision="rejected",
    )
    for field in (
        "attempts",
        "alternatives_considered",
        "acquisition_block",
        "acquisition_block_note",
        "sha256",
        "local_path",
    ):
        candidate.pop(field, None)
    candidate.update(patch)
    with pytest.raises(ValidationError, match=message):
        validate_photo_search(report, sample(), representative_fixture())


def test_zero_photos_requires_follow_up_and_a_distinct_executed_search(tmp_path):
    run = create_run(tmp_path, "架空の写真が利用できない調査")
    data = sample()
    gen = commit_generation(run, data)
    report = search_fixture()
    report["photo_plan"]["targets"][0]["search_ids"] = ["s1"]
    with pytest.raises(ValidationError, match="additional search"):
        save_illustrations(run, report, complete=True)
    report["photo_plan"]["targets"][0]["search_ids"].append("s2")
    save_illustrations(run, report)
    assert illustration_task(resume(run))["illustration_report"] == report
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    scene = render_fixture(run, data)
    review = review_fixture(gen)
    assert assess(run, data, scene, review)["status"] == "complete"
    review["photo_review"]["checks"][0]["query"] = report["searches"][0]["query"]
    assert assess(run, data, scene, review)["status"] == "incomplete"
    commit_generation(run, data)
    assert illustration_task(resume(run))["status"] == "retryable"


def test_multiple_photos_on_same_subject_and_duplicate_tracking(tmp_path):
    run = create_run(tmp_path, "架空の写真2枚")
    data = sample()
    assets = [
        add_photo(run, data, key, color)
        for key, color in (("red", "red"), ("blue", "blue"))
    ]
    gen = commit_generation(run, data)
    report = search_fixture()
    report["candidates"] = [
        adopted_candidate(report, asset, asset["id"]) for asset in assets
    ]
    malformed = deepcopy(report)
    malformed["candidates"][1]["identifier"] = malformed["candidates"][0]["identifier"]
    with pytest.raises(ValidationError, match="duplicate catalog image"):
        save_illustrations(run, malformed, complete=True)
    malformed = deepcopy(report)
    malformed["candidates"][0]["source_url"] = RIGHTS
    with pytest.raises(ValidationError, match="matching provenance"):
        validate_photo_search(malformed, data)
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    scene = render_fixture(run, data)
    review = review_fixture(gen, [a["id"] for a in assets])
    result = assess(run, data, scene, review)
    assert result["status"] == "complete"
    assert result["photos"]["formats"] == {"svg": 2, "html": 2, "pdf": 2}
    assert result["photos"]["depicted_representatives"] == 1
    assert len({a["bbox"][1] for a in scene["assets"]}) == 2
    assert all(a["owner_id"] == "node_old" for a in scene["assets"])
    same = run / assets[0]["local_path"]
    (run / assets[1]["local_path"]).write_bytes(same.read_bytes())
    assets[1]["sha256"] = assets[0]["sha256"]
    updated = commit_generation(run, data)
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    scene = render_fixture(run, data)
    review["generation"] = updated
    assert (
        "identical image payloads"
        in assess(run, data, scene, review)["photos"]["reason"]
    )


def test_corrupt_and_wrong_checksum_are_reported(tmp_path, capsys):
    run = create_run(tmp_path, "架空の取得検証")
    data = sample()
    asset = add_photo(run, data, "valid", "maroon")
    path = run / asset["local_path"]
    assert main(["image-inspect", str(path)]) == 0
    assert asset["sha256"] in capsys.readouterr().out
    path.write_text("<html>no image</html>")
    with pytest.raises(LayoutError, match="checksum differs"):
        layout(data, run_dir=run)
    del asset["sha256"]
    with pytest.raises(LayoutError, match="cannot identify image"):
        layout(data, run_dir=run)
    assert main(["image-inspect", str(path)]) == 1


def test_rasters_scale_on_large_graph_and_legacy_data_still_loads(tmp_path):
    run = create_run(tmp_path, "架空の多数の写真")
    data = sample()
    for i in range(120):
        add_node(data, f"node_many_{i}", 300 + i)
        if i % 3 == 0:
            add_photo(run, data, f"photo_{i}", (i, 42, 75), owner=f"node_many_{i}")
    generation = commit_generation(run, data)
    scene = layout(load_generation(run / "snapshots" / generation), run_dir=run)
    assert len(scene["nodes"]) == 123 and len(scene["edges"]) == 1
    assert emit(scene, run / "render")["coverage"]["images"] == 40
    assert set(check_saved_artifacts(scene, run / "render").values()) == {40}
    assert all(abs(a["bbox"][2] / a["bbox"][3] - 1.5) < 1e-6 for a in scene["assets"])
    progress = read_progress(run)
    assert progress["active_generation"] == generation


def test_representative_context_does_not_count_as_the_person_depicted():
    data = sample()
    reports = representative_fixture()
    rep = reports["candidates"][0]
    rep["node_ids"].append("node_new")
    suggested = {
        item["node_id"]: item["representative_ids"]
        for item in opportunities(data, reports)
    }
    assert suggested == {"node_old": ["rep1"], "node_new": []}
    rep["subject_node_ids"] = ["node_new"]
    suggested = {
        item["node_id"]: item["representative_ids"]
        for item in opportunities(data, reports)
    }
    assert suggested == {"node_old": [], "node_new": ["rep1"]}


def test_legacy_numeric_ruler_is_accepted_without_changing_current_labels():
    from truth_seeker.scene import check_scene, date_text

    data = sample()
    data["nodes"][0]["dates"] = [
        {"earliest": -110, "latest": -90, "label": "前1世紀末"}
    ]
    scene = layout(data)
    assert scene["time_axis"]["labels"][0]["text"] == "前1世紀末"
    old = deepcopy(scene)
    ruler = old["time_axis"]
    ruler["labels"][0]["text"] = date_text(ruler["anchors"][0][0])
    with pytest.raises(LayoutError, match="axis labels"):
        check_scene(old, data)
    del ruler["calendar_labels"]
    assert check_scene(old, data)["status"] == "pass"
