"""Regression tests for relationship placement, edge encoding and inline figures."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from test_engine import sample
from test_layout import add_edge, add_node, node

from truth_seeker.model import LayoutError, ValidationError, load_generation
from truth_seeker.render import emit, layout
from truth_seeker.routing import first_collision, padded
from truth_seeker.scene import check_scene


def test_same_year_cards_share_a_row_and_keep_authored_date_precision():
    data = sample()
    first = data["nodes"][0]
    first["display_date"]["anchor_year"] = 1944
    first["dates"] = [{"earliest": 1944, "latest": 1944, "label": "1944年2月"}]
    add_node(data, "node_second", 1944, label="同年の計算機")
    add_node(data, "node_third", 1944, label="他の同年ノード")
    scene = layout(data)
    assert layout(data) == scene
    same = [n for n in scene["nodes"] if n["anchor_year"] == 1944]
    assert len({n["lane_id"] for n in same}) == 3
    assert max(n["bbox"][1] for n in same) - min(n["bbox"][1] for n in same) <= 14
    assert (
        max(n["bbox"][1] + n["bbox"][3] for n in same)
        < node(scene, "node_new")["bbox"][1]
    )
    assert node(scene)["date_label"] == "1944年2月"
    assert node(scene, "node_unknown")["anchor_year"] is None


def test_relationship_outweighs_category_and_keeps_uncertain_interval():
    data = sample()
    source, target = data["nodes"][:2]
    source["type"], target["type"] = "concept", "event"
    target["dates"] = [{"earliest": 1947, "latest": 1953}]
    base = layout({**data, "edges": []})
    linked = layout(
        data,
        importance={
            "edge_link": {"level": 4, "reason": "技術の成立条件", "source": "test"},
        },
    )

    def horizontal_span(scene):
        return abs(node(scene)["anchor"][0] - node(scene, "node_new")["anchor"][0])

    assert horizontal_span(linked) < horizontal_span(base)
    assert node(linked)["color"] == node(base)["color"]
    assert node(linked, "node_new")["date_label"] == "1947年〜1953年"
    assert check_scene(linked, data)["edge_count"] == 1


def test_edge_encoding_keeps_relation_certainty_and_importance_independent():
    data = sample()
    data["claims"][0]["assessment"] = "contested"
    old = data["edges"][0]
    old["relation_type"], old["claim_ids"] = "influence", ["claim_origin"]
    for i in (1, 3, 4):
        edge = add_edge(data, f"edge_{i}", "node_old", "node_new")
        edge["importance"] = {"level": i, "reason": "関係の役割", "source": "test"}
    data["edges"][-1]["relation_type"] = "dependency"
    scene = layout(data)
    edges = {e["edge_id"]: e for e in scene["edges"]}
    widths = [
        edges[key]["style"]["width"]
        for key in ("edge_1", "edge_link", "edge_3", "edge_4")
    ]
    assert widths == sorted(set(widths))
    assert edges["edge_link"]["style"]["dash"] != ""
    assert edges["edge_link"]["style"]["stroke"] == edges["edge_1"]["style"]["stroke"]
    assert edges["edge_4"]["style"]["stroke"] != edges["edge_link"]["style"]["stroke"]
    assert {e["target_node_id"] for e in edges.values()} == {"node_new"}
    edges["edge_4"]["style"]["width"] = widths[0]
    with pytest.raises(LayoutError, match="relation styling"):
        check_scene(scene, data)


def test_registered_chart_and_photo_reserve_space_before_routing(tmp_path):
    from pymupdf import open as open_pdf

    data = sample()
    middle = add_node(data, "node_figures", 195, label="図版付き")
    middle["importance"] = {"level": 4, "reason": "主題", "source": "test"}
    chart = Image.new("RGB", (200, 120), "white")
    # Two explicit fixture observations, 20 and 45, on a scale of two pixels per unit.
    draw = ImageDraw.Draw(chart)
    for x, value in zip((40, 100), (20, 45)):
        draw.rectangle((x, 105 - value * 2, x + 30, 105), fill="#24588A")
    chart.save(tmp_path / "chart.png")
    Image.new("RGB", (160, 100), "#9B6756").save(tmp_path / "photo.jpg")
    for kind, file, title in (
        ("chart", "chart.png", "比較図"),
        ("photo", "photo.jpg", "写真"),
    ):
        data["assets"].append(
            {
                "id": f"asset_{kind}",
                "schema_version": "0.5",
                "asset_type": kind,
                "node_id": "node_figures",
                "local_path": file,
                "caption": title,
                "license": "CC0",
                "source_url": f"https://example.org/{kind}",
            }
        )
    scene = layout(data, run_dir=tmp_path)
    box = node(scene, "node_figures")["bbox"]
    assert (
        scene["assets"][0]["bbox"][1] < scene["assets"][1]["bbox"][1] < box[1] + box[3]
    )
    assert scene["assets"][0]["bbox"][2] > 260
    assert (
        first_collision(scene["edges"][0]["path"], [("card", padded(box, 5))]) is None
    )
    assert emit(scene, tmp_path)["primary_format"] == "pdf"
    svg = (tmp_path / "map.svg").read_text()
    assert svg in (tmp_path / "map.html").read_text()
    assert len(ET.fromstring(svg).findall(".//{*}image")) == 2
    with open_pdf(tmp_path / "map.pdf") as pdf:
        assert len(pdf) == 1 and len(pdf[0].get_image_info()) == 2
        assert {"比較図", "写真"} <= set(
            pdf[0].get_text().replace("　CC0", "").splitlines()
        )
    del data["assets"][0]["license"]
    with pytest.raises(ValidationError, match="rights metadata"):
        layout(data, run_dir=tmp_path)


@pytest.mark.parametrize(
    "topic,count", [("コンピュータ", (124, 66)), ("通信技術", (152, 171))]
)
def test_saved_research_run_rerenders_with_inline_asset(tmp_path, topic, count):
    paths = sorted((Path(__file__).resolve().parents[1] / "runs").glob(f"*_{topic}_*"))
    if not paths:
        pytest.skip("saved research run is local")
    run = paths[-1]
    progress = json.loads((run / "progress.json").read_text())
    data = load_generation(run / "snapshots" / progress["active_generation"])
    scene = layout(data, progress["run_id"], topic, run_dir=run)
    assert (len(scene["nodes"]), len(scene["edges"])) == count
    assert all(asset["owner_id"] for asset in scene["assets"])
    result = emit(scene, tmp_path)
    assert result["primary_format"] == "pdf"
    assert 0 < result["pdf_scale"] < 1
    assert result["pdf_check"] == "page_and_text"
