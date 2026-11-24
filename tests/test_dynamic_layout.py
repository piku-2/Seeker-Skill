"""Spatial, generated-figure and representative-subject regressions."""

from copy import deepcopy

import pytest
from test_engine import sample
from test_illustration_qa import representative_fixture, review_fixture, search_fixture
from test_layout import add_edge, add_node

from truth_seeker.model import ValidationError, validate
from truth_seeker.qa import judge
from truth_seeker.render import emit, layout
from truth_seeker.representatives import KIND
from truth_seeker.store import (
    commit_generation,
    create_run,
    read_progress,
    research_task,
    resume,
    save_illustrations,
    save_representatives,
)


def test_sparse_map_does_not_reserve_empty_tracks():
    data = sample()
    scene = layout(data)
    widest = max(n["bbox"][2] for n in scene["nodes"])
    assert scene["canvas_width"] < widest * 2.2
    assert "lane_definitions" not in scene
    assert len({n["bbox"][0] for n in scene["nodes"]}) == 1


def test_large_band_has_more_than_four_real_slots_with_a_bounded_width():
    data = sample()
    for i in range(1200):
        add_node(data, f"node_dense_{i:04}", -100 + i // 100)
    for i in range(0, 1200, 5):
        add_edge(data, f"edge_dense_{i:04}", f"node_dense_{i:04}", "node_new")
    scene = layout(data)
    assert scene == layout(data)
    assert len(scene["nodes"]) == 1203
    assert len(scene["edges"]) == 241
    same_year = [v for v in scene["nodes"] if v["anchor_year"] == -100]
    first_row = [v for v in same_year if v["bbox"][1] - v["anchor"][1] <= 14]
    assert len(first_row) > 4
    assert scene["canvas_width"] < sum(n["bbox"][2] for n in same_year) / 2


def figure_data(kind):
    data = sample()
    data["evidence"][0].update(
        access_status="accessible", source_url="https://example.org/figure-fixture"
    )
    recorded = ["evi_first"]
    data["edges"][0]["claim_ids"] = ["claim_origin"]
    figures = {
        "table": {
            "columns": ["場所", "時期"],
            "rows": [{"cells": ["架空の説明", "前100年"], "evidence_ids": recorded}],
        },
        "bar": {
            "unit": "m",
            "rows": [
                {"label": "架空の測定値", "value": 12.5, "evidence_ids": recorded},
                {"label": "架空のゼロ値", "value": 0, "evidence_ids": recorded},
            ],
        },
        "relations": {"edge_ids": ["edge_link"]},
        "distribution": {
            "rows": [
                {
                    "label": "架空の位置",
                    "latitude": 12.5,
                    "longitude": -2,
                    "evidence_ids": recorded,
                }
            ]
        },
    }
    data["assets"] = [
        dict(
            id=f"asset_fixture_{kind}",
            schema_version="0.5",
            asset_type="generated",
            related_node_ids=["node_old", "node_new"],
            rights_note="テスト用に新規作成",
            caption="架空の図表",
            evidence_ids=recorded,
            placement="gallery",
            figure={"kind": kind, **figures[kind]},
        )
    ]
    return data


@pytest.mark.parametrize("kind", ["table", "bar", "relations", "distribution"])
def test_generated_vectors_are_sourced_and_render_in_three_formats(tmp_path, kind):
    data = figure_data(kind)
    scene = layout(data)
    asset = scene["assets"][0]
    assert asset["shapes"] and asset["owner_id"] is None
    assert "bbox" not in asset
    assert asset["source_urls"] == ["https://example.org/figure-fixture"]
    assert emit(scene, tmp_path)["primary_format"] == "pdf"
    svg = (tmp_path / "map.svg").read_text()
    assert svg in (tmp_path / "map.html").read_text()
    assert svg.count('data-figure-shape="1"') == len(asset["shapes"])
    if kind == "bar":
        assert any("12.5 m" in l["text"] for l in asset["text_lines"])
        bad = deepcopy(data)
        bad["assets"][0]["figure"]["rows"][0]["evidence_ids"] = []
        with pytest.raises(ValidationError, match="row must cite"):
            validate(bad)


def test_many_figures_are_preserved_and_may_attach_to_same_node(tmp_path):
    data = figure_data("bar")
    record = data["assets"][0]
    data["assets"] = [
        {**record, "id": f"asset_chart_{i}", "placement": "inline"} for i in range(25)
    ]
    scene = layout(data)
    assert len(scene["assets"]) == 25
    assert len({a["reserved_bbox"][1] for a in scene["assets"]}) == 25
    assert emit(scene, tmp_path)["coverage"]["generated_figures"] == 25


def test_representative_report_is_a_separate_resumable_completion_gate(tmp_path):
    run = create_run(tmp_path, "架空の調査")
    data = sample()
    gen = commit_generation(run, data)
    save_illustrations(run, search_fixture(), complete=True)
    scene = layout(data)
    scene["graph_generation"] = gen
    emit(scene, run / "render")
    review = review_fixture(gen)

    def status():
        return judge(data, scene, review, progress=read_progress(run), run_dir=run)

    assert status()["representatives"]["status"] == "fail"
    report = representative_fixture()
    report["candidates"][0]["decision"] = "pending"
    started = save_representatives(run, report)
    with pytest.raises(ValidationError, match="unresolved candidate"):
        save_representatives(run, report, complete=True)
    task = research_task(resume(run), KIND)
    assert task["task_id"] == started["task_id"] and task["status"] == "retryable"
    assert task["representative_report"] == report
    report["candidates"][0]["decision"] = "adopted"
    save_representatives(run, report, complete=True)
    assert status()["status"] == "complete"
    commit_generation(run, data)
    assert status()["representatives"]["status"] == "fail"
