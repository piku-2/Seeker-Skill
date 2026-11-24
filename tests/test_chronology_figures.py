"""Chronology and figure decisions tested with explicitly fictional records."""

from copy import deepcopy

import pytest
from test_dynamic_layout import figure_data
from test_engine import sample
from test_illustration_qa import (
    assess,
    render_fixture,
    representative_fixture,
    review_fixture,
    search_fixture,
)
from test_layout import add_edge, add_node

from truth_seeker.figure_plan import opportunities
from truth_seeker.model import ValidationError, load_generation, validate
from truth_seeker.render import check_saved_artifacts, emit, layout
from truth_seeker.store import (
    commit_generation,
    create_run,
    illustration_task,
    resume,
    save_illustrations,
    save_representatives,
)


def chronology_data():
    data = sample()
    data["claims"][0]["assessment"] = "favored"
    data["evidence"][0].update(
        access_status="accessible",
        source_url="https://example.org/fictional-chronology",
    )
    return data


def estimate(lo, hi):
    return {
        "mode": "estimated",
        "kind": "editing",
        "earliest": lo,
        "latest": hi,
        "uncertainty": "編集層の推定。物語の舞台とは別。",
        "anchor_reason": "年代範囲の中央か、確認できる境界を目安に選ぶ。",
        "basis_claim_ids": ["claim_origin"],
    }


@pytest.mark.parametrize(
    ("lo", "hi", "display", "year"),
    [
        (-200, -100, "紀元前201年〜紀元前101年", -150),
        (-20, 20, "紀元前21年〜20年", 0),
        (None, 15, "15年以前", 15),
        (-10, None, "紀元前11年以降", -10),
    ],
)
def test_estimates_draw_range_and_phase_not_representative_year(lo, hi, display, year):
    data = chronology_data()
    data["nodes"][0]["dates"] = [
        {"kind": "narrative", "earliest": -900, "latest": -899},
    ]
    data["nodes"][0]["display_date"] = estimate(lo, hi)
    scene = layout(data)
    card = next(n for n in scene["nodes"] if n["node_id"] == "node_old")
    assert card["anchor_year"] == year
    assert card["date_label"] == "推定（編集） " + display
    assert scene["time_axis"]["labels"][0]["text"] == "推定年代帯"
    assert scene["time_axis"]["labels"][-1]["text"] == "年代不明"
    assert any(line["role"] == "date_note" for line in card["text_lines"])


def test_invalid_bounds_and_out_of_range_anchor_are_rejected():
    data = chronology_data()
    data["nodes"][0]["display_date"] = estimate(40, 10)
    with pytest.raises(ValidationError, match="invalid estimated range"):
        validate(data)
    data["nodes"][0]["display_date"] = {**estimate(10, 40), "anchor_year": 50}
    with pytest.raises(ValidationError, match="outside its range"):
        validate(data)
    data["nodes"][0]["display_date"] = estimate(10, 40)
    data["claims"][0]["assessment"] = "hypothetical"
    with pytest.raises(ValidationError, match="lacks accessible adopted support"):
        validate(data)


def test_sequence_requires_explicit_evidenced_precedence_and_rejects_cycles(tmp_path):
    data = chronology_data()
    for node in data["nodes"][:2]:
        node["display_date"]["kind"] = "event"
    data["nodes"][2]["display_date"] = {
        "mode": "sequence",
        "kind": "editing",
        "edge_ids": ["edge_prior", "edge_after"],
        "anchor_reason": "二つの既知の事象の間に置く。",
        "uncertainty": "先後関係のみを記録した架空の編集層。",
    }
    add_edge(data, "edge_prior", "node_old", "node_unknown")
    add_edge(data, "edge_after", "node_unknown", "node_new")
    with pytest.raises(ValidationError, match="explicit temporal edges"):
        validate(data)
    for edge, kinds in zip(
        data["edges"][1:], [("event", "editing"), ("editing", "event")]
    ):
        edge["claim_ids"] = ["claim_origin"]
        edge["chronology"] = {
            "source_kind": kinds[0],
            "target_kind": kinds[1],
            "basis_claim_ids": ["claim_origin"],
        }
    scene = layout(data)
    node = next(n for n in scene["nodes"] if n["node_id"] == "node_unknown")
    assert node["date_label"] == "配置目安（編集） 紀元前101年〜1950年"
    assert node["anchor_year"] == 925
    assert any(
        line["text"] == "先後関係の配置帯" for line in scene["time_axis"]["labels"]
    )
    emit(scene, tmp_path)
    assert check_saved_artifacts(scene, tmp_path) == {"svg": 0, "html": 0, "pdf": 0}
    cycle = deepcopy(data)
    add_edge(cycle, "edge_cycle", "node_unknown", "node_old")
    cycle["edges"][-1].update(
        claim_ids=["claim_origin"],
        chronology={
            "source_kind": "editing",
            "target_kind": "event",
            "basis_claim_ids": ["claim_origin"],
        },
    )
    with pytest.raises(ValidationError, match="cyclic precedence"):
        validate(cycle)
    data["nodes"][1]["display_date"]["anchor_year"] = -200
    with pytest.raises(
        ValidationError, match="incompatible sequence bounds|contradict"
    ):
        validate(data)


@pytest.mark.parametrize("kind", ["timeline", "lineage", "composition"])
def test_related_band_figures_render_beside_their_nodes(tmp_path, kind):
    data = figure_data("relations")
    record = data["assets"][0]
    record["placement"] = "band"
    record["figure"]["kind"] = kind
    if kind == "timeline":
        data["nodes"][1]["claim_ids"] = ["claim_origin"]
        data["claims"][0]["assessment"] = "verified"
        data["nodes"][0]["display_date"] = estimate(-150, -50)
        record["figure"] = {"kind": "timeline", "node_ids": ["node_new", "node_old"]}
    scene = layout(data)
    asset = scene["assets"][0]
    assert asset["owner_id"] is None
    assert asset["band_year"] in (-100, 1950)
    assert asset["reserved_bbox"][1] < scene["sources_y"]
    assert all(
        record["id"] in node["asset_refs"]
        for node in scene["nodes"]
        if node["node_id"] in record["related_node_ids"]
    )
    if kind == "timeline":
        assert any("推定（編集）" in line["text"] for line in asset["text_lines"])
    emit(scene, tmp_path)
    assert check_saved_artifacts(scene, tmp_path) == {"svg": 1, "html": 1, "pdf": 1}


def test_many_band_figures_keep_all_cards_and_edges(tmp_path):
    data = figure_data("relations")
    for i in range(85):
        add_node(data, f"node_extra_{i}", i + 1)
        add_edge(data, f"edge_extra_{i}", "node_old", f"node_extra_{i}")
    data["assets"] = [
        {**data["assets"][0], "id": f"asset_panel_{i}", "placement": "band"}
        for i in range(26)
    ]
    scene = layout(data)
    assert len(scene["nodes"]) == 88 and len(scene["edges"]) == 86
    result = emit(scene, tmp_path)
    assert (
        result["primary_format"] == "pdf"
        and result["coverage"]["generated_figures"] == 26
    )


def test_candidate_checkpoint_reopens_and_quality_requires_both_reviews(tmp_path):
    data = chronology_data()
    run = create_run(tmp_path, "架空の年代と図表")
    generation = commit_generation(run, data)
    report = search_fixture()
    plan = report.pop("figure_plan")
    assert {x["id"] for x in opportunities(data)} == {
        "graph-chronology",
        "graph-hypotheses",
    }
    with pytest.raises(ValidationError, match="missing opportunity review"):
        save_illustrations(run, report, complete=True)
    report["figure_plan"] = plan
    plan["decisions"][0]["decision"] = "pending"
    save_illustrations(run, report)
    assert illustration_task(resume(run))["illustration_report"] == report
    with pytest.raises(ValidationError, match="unresolved"):
        save_illustrations(run, report, complete=True)
    plan["decisions"][0]["decision"] = "rejected"
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    scene = render_fixture(run, data)
    review = review_fixture(generation)
    del review["chronology_review"]["node_notes"]["node_unknown"]
    assert assess(run, data, scene, review)["chronology"]["status"] == "fail"
    review = review_fixture(generation)
    review["figure_review"]["coverage_adequate"] = False
    assert assess(run, data, scene, review)["figures"]["status"] == "fail"
    assert validate(load_generation(run / "snapshots" / generation))
    assert assess(run, data, scene, review_fixture(generation))["status"] == "complete"


def test_typed_anchored_date_checks_confirmed_range_and_keeps_narrative_separate():
    data = chronology_data()
    node = data["nodes"][0]
    node["display_date"].update(kind="editing", anchor_year=12)
    node["dates"] = [
        {"kind": "narrative", "earliest": 10, "latest": 40},
        {"kind": "editing", "earliest": None, "latest": 5},
    ]
    with pytest.raises(ValidationError, match="contradicts its date assertions"):
        validate(data)
    node["display_date"]["anchor_year"] = 2
    scene = layout(data)
    card = next(n for n in scene["nodes"] if n["node_id"] == "node_old")
    assert card["date_label"] == "編集 5年以前"
    assert card["date_placement"]["earliest"] is None
    node["display_date"]["kind"] = "narrative"
    with pytest.raises(ValidationError, match="narrative dates belong"):
        validate(data)
