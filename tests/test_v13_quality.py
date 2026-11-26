"""Fictional fixtures for dense bands, figure meanings and run metadata."""

import json
import xml.etree.ElementTree as ET
from copy import deepcopy

import pymupdf
import pytest
from test_dynamic_layout import figure_data
from test_engine import sample
from test_layout import add_edge, add_node

from truth_seeker.cli import main
from truth_seeker.figure_plan import opportunities, validate_review
from truth_seeker.layout_quality import layout_metrics
from truth_seeker.model import ValidationError, validate
from truth_seeker.render import check_saved_artifacts
from truth_seeker.render_run import render_run
from truth_seeker.scene import check_scene, layout
from truth_seeker.status import status
from truth_seeker.store import (
    add_execution,
    add_task,
    atomic_json,
    commit_generation,
    create_run,
    read_manifest,
    resume,
)


def test_dense_year_packs_cards_and_panels_with_one_date_anchor():
    data = figure_data("bar")
    data["nodes"][0]["display_date"]["anchor_year"] = 1992
    ids = ["node_old"]
    for index in range(7):
        key = f"node_era_{index}"
        add_node(data, key, 1992, summary="テスト用カード。")
        add_edge(data, f"edge_era_{index}", "node_old", key)
        ids.append(key)
    template = data["assets"][0]
    data["assets"] = []
    for index in range(6):
        panel = deepcopy(template)
        panel.update(
            id=f"asset_era_{index}",
            related_node_ids=ids[index : index + 2],
            placement="band",
        )
        panel["figure"]["rows"][0]["value"] *= index + 1
        data["assets"].append(panel)
    scene = layout(data)
    check_scene(scene, data)
    dated = [n for n in scene["nodes"] if n["anchor_year"] == 1992]
    band = [a for a in scene["assets"] if a.get("band_year") == 1992]
    assert len(dated) == 8 and len(band) == 6
    assert len({n["anchor"][1] for n in dated}) == 1
    assert any("anchor_leader" in n for n in dated)
    assert len({round(a["reserved_bbox"][1]) for a in band}) > 1
    assert scene["canvas_width"] < sum(n["bbox"][2] for n in dated) + 460 * len(band)
    metric = layout_metrics(scene)
    assert (
        metric["edge_node_collisions"]
        == metric["edge_asset_collisions"]
        == metric["card_overlaps"]
        == 0
    )
    assert metric["coverage_ratio"] == {"nodes": 1, "edges": 1}


def test_figure_uses_recorded_title_role_and_direction():
    data = figure_data("relations")
    edge = data["edges"][0]
    edge.update(
        relation_type="influence",
        semantic_role="product_development",
        label="架空製品の後継",
    )
    asset = data["assets"][0]
    asset["figure"].update(
        kind="lineage", title="製品世代の関係", meaning="product_development"
    )
    rendered = layout(data)["assets"][0]
    labels = [line["text"] for line in rendered["text_lines"]]
    assert labels[0] == "製品世代の関係"
    relation = next(label for label in labels if "架空製品の後継" in label)
    assert "影響" in relation and "仮説" in relation
    assert "本文の伝承" not in "".join(labels)
    asset["figure"]["meaning"] = "textual_revision"
    with pytest.raises(ValidationError, match="differs from edge"):
        validate(data)


def test_figure_candidates_and_untyped_legacy_figures():
    data = figure_data("relations")
    data["edges"][0]["relation_type"] = "dependency"
    candidate = next(
        item for item in opportunities(data) if item["id"] == "graph-composition"
    )
    assert (
        candidate["category"] == "dependencies"
        and candidate["suggested_kind"] == "relations"
    )
    asset = data["assets"][0]
    asset["figure"]["kind"] = "composition"
    text = "".join(line["text"] for line in layout(data)["assets"][0]["text_lines"])
    assert "全体と構成要素" not in text
    assert "依存" in text
    data["edges"][0]["semantic_role"] = "component_of"
    asset["figure"].update(title="試作機の部品", meaning="component_of")
    assert validate(data)


def test_duplicate_figure_content_fails_figure_review():
    data = figure_data("relations")
    another = deepcopy(data["assets"][0])
    another.update(id="asset_duplicate", caption="題名だけ変えた図")
    another["figure"]["kind"] = "lineage"
    data["assets"].append(another)
    review = {"coverage_adequate": True, "findings": "テスト", "assets": {}}
    with pytest.raises(ValidationError, match="duplicates the content"):
        validate_review(review, data)


def test_execution_collects_confirmed_sessions_and_matches_three_formats(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRUTH_SEEKER_MODEL", "環境モデル")
    monkeypatch.setenv("TRUTH_SEEKER_HARNESS", "環境ハーネス")
    run = create_run(tmp_path, "実行情報", model="初代")
    generation = commit_generation(run, sample())
    task = add_task(run, "資料照合")
    add_execution(
        run,
        model="補助",
        role="subagent",
        task_id=task["task_id"],
        generation=generation,
    )
    resume(run, model="次世代</script>")
    history = read_manifest(run)["execution_history"]
    assert [(r["model"], r["phase"], r["role"]) for r in history] == [
        ("初代", "init", "main"),
        ("補助", "research", "subagent"),
        ("次世代</script>", "resume", "main"),
    ]
    assert all(r["harness_source"] == "environment" for r in history)
    assert (
        history[2]["model_source"] == "explicit"
        and history[2]["generation"] == generation
    )
    render_run(run, "distribution", "all")
    folder = run / "render"
    scene = json.loads((folder / "scene.json").read_text())
    assert scene["execution"]["history"] == history
    svg = ET.fromstring((folder / "map.svg").read_text())
    svg_meta = svg.find("{http://www.w3.org/2000/svg}metadata")
    assert json.loads(svg_meta.text) == scene["execution"]
    html = (folder / "map.html").read_text()
    assert "次世代&lt;/script&gt;" in html and "次世代\\u003c/script>" in html
    assert "次世代</script>" not in html
    with pymupdf.open(folder / "map.pdf") as pdf:
        pdf_text = pdf[0].get_text()
    assert "Model: 次世代</script>" in pdf_text
    assert "Harness: 環境ハーネス" in pdf_text
    assert set(check_saved_artifacts(scene, folder)) == {"svg", "pdf", "html"}
    assert status(run)["summary"]["execution_history"] == history
    add_execution(run, model="補記", phase="review", role="reviewer")
    with pytest.raises(ValidationError, match="dependencies changed"):
        render_run(run, "distribution", "html")


def test_unknown_legacy_and_generation_dates(tmp_path, monkeypatch):
    monkeypatch.delenv("TRUTH_SEEKER_MODEL", raising=False)
    monkeypatch.delenv("TRUTH_SEEKER_HARNESS", raising=False)
    run = create_run(tmp_path, "古い実行")
    history = read_manifest(run)["execution_history"]
    assert history[0]["model"] == history[0]["harness"] == "unknown"
    manifest = read_manifest(run)
    start = manifest["created_at"]
    del manifest["execution_history"]
    atomic_json(run / "manifest.json", manifest)
    commit_generation(run, sample())
    folder = run / "render"
    render_run(run, "distribution", "all")
    first = json.loads((folder / "scene.json").read_text())["execution"]
    assert first["history"] == [] and first["started_at"] == start
    render_run(run, "distribution", "html")
    assert json.loads((folder / "scene.json").read_text())["execution"] == first
    render_run(run, "distribution", "all")
    second = json.loads((folder / "scene.json").read_text())["execution"]
    assert (
        second["started_at"] == start < first["generated_at"] < second["generated_at"]
    )


@pytest.mark.parametrize("bad", ["x" * 97, "bad\nlabel", "bad\u202elabel", "   "])
def test_invalid_execution_identifier_rejected_before_writing(tmp_path, bad):
    with pytest.raises(ValidationError, match="printable identifier"):
        create_run(tmp_path, "fixture", model=bad)
    assert list(tmp_path.iterdir()) == []


def test_model_only_cli_init(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TRUTH_SEEKER_HARNESS", raising=False)
    assert main(["init", "前提", "--runs", str(tmp_path), "--model", "確認済み"]) == 0
    run = json.loads(capsys.readouterr().out)["run"]
    item = read_manifest(run)["execution_history"][0]
    assert item["model"] == "確認済み" and item["harness_source"] == "unknown"


def test_lower_row_date_guide_is_clipped_at_an_intervening_panel():
    from truth_seeker.scene import _visible_leader

    result = _visible_leader([[80, 0], [80, 120], [88, 120]], [[60, 40, 50, 45]])
    assert result == [[[80, 0], [80, 38]], [[80, 87], [80, 120], [88, 120]]]


def test_old_figure_presentation_can_still_be_checked(monkeypatch):
    import truth_seeker.scene as scene_module

    draw = scene_module.draw_figure

    def old_draw(*args, **kwargs):
        return draw(*args, **{**kwargs, "legacy": True})

    data = figure_data("relations")
    data["assets"][0]["figure"]["kind"] = "lineage"
    with monkeypatch.context() as patch:
        patch.setattr(scene_module, "draw_figure", old_draw)
        old = layout(data)
    old.pop("figure_presentation")
    assert any("本文の伝承" in line["text"] for line in old["assets"][0]["text_lines"])
    assert check_scene(old, data)
