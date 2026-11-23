"""Regression coverage for Truth Seeker v1.1 layout and artifact QA."""

import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from itertools import pairwise
from pathlib import Path

import pytest
from test_engine import sample

from truth_seeker.model import LayoutError, ValidationError, load_generation, validate
from truth_seeker.render import emit, layout
from truth_seeker.routing import cubics, curve_hits, lerp, padded
from truth_seeker.scene import check_scene
from truth_seeker.typography import display_text


def node(scene, identifier="node_old"):
    return next(n for n in scene["nodes"] if n["node_id"] == identifier)


def add_node(data, identifier, year, **updates):
    record = {
        **data["nodes"][0],
        "id": identifier,
        "label": identifier,
        "claim_ids": [],
    }
    record["display_date"] = {"mode": "anchored", "anchor_year": year}
    record.update(updates)
    data["nodes"].append(record)
    return record


def add_edge(data, identifier, source, target):
    edge = {
        **data["edges"][0],
        "id": identifier,
        "source_id": source,
        "target_id": target,
    }
    data["edges"].append(edge)
    return edge


def reconstructed(lines):
    return "".join(
        line["text"] + ("\n" if line["break_after"] else "") for line in lines
    )


def role_lines(card, role):
    return [line for line in card["text_lines"] if line["role"] == role]


def assert_card_text(card):
    for role, field in (("title", "label"), ("body", "summary")):
        assert reconstructed(role_lines(card, role)) == display_text(card[field])
    x, y, w, h = card["bbox"]
    prev_bottom = y
    for line in card["text_lines"]:
        xx, yy, ww, hh = line["bbox"]
        assert x <= xx <= xx + ww <= x + w
        assert prev_bottom <= yy <= yy + hh <= y + h
        prev_bottom = yy + hh


def test_importance_changes_card_style_independent_of_assessment():
    data = sample()
    original = deepcopy(data)
    entry = {"reason": "テストで指定した歴史的影響", "source": "research"}
    cards = [
        node(layout(data, importance={"node_old": {**entry, "level": level}}))
        for level in range(1, 5)
    ]
    assert data == original
    assert node(layout(data))["importance"]["level"] == 2
    assert [card["importance"]["level"] for card in cards] == [1, 2, 3, 4]
    assert len({card["color"] for card in cards}) == 1
    assert all(card["assessments"] == ["hypothetical"] for card in cards)
    for a, b in pairwise(cards):
        assert a["bbox"][2] < b["bbox"][2]
        assert role_lines(a, "title")[0]["size"] < role_lines(b, "title")[0]["size"]
        assert a["style"]["width"] <= b["style"]["width"]
    data["nodes"][0]["importance"] = {**entry, "level": 4}
    scene = layout(data)
    assert node(scene)["importance"] == data["nodes"][0]["importance"]
    assert [edge["edge_id"] for edge in scene["edges"]] == ["edge_link"]
    data["nodes"][0]["importance"]["level"] = True
    with pytest.raises(ValidationError, match="importance level"):
        validate(data)


def test_long_japanese_title_wraps_without_loss():
    data = sample()
    data["nodes"][0]["label"] = (
        "日本語の長い概念名（技術的な説明を含む）、" * 9
    ) + "最後。"
    card = node(layout(data))
    lines = role_lines(card, "title")
    assert len(lines) > 4
    assert all(line["size"] >= 12 for line in lines)
    assert all(not line["text"].startswith(("）", "、", "。")) for line in lines)
    assert all(not line["text"].endswith("（") for line in lines)
    assert_card_text(card)


def test_long_body_grows_card_and_preserves_paragraph_breaks():
    data = sample()
    short = node(layout(data))
    data["nodes"][0]["summary"] = (
        "非常に長い説明文です。\n\n内容と改行を保持する。\n" * 35
    )
    scene = layout(data)
    card = node(scene)
    assert card["bbox"][3] > short["bbox"][3] * 10
    assert card["bbox"][1] + card["bbox"][3] < node(scene, "node_new")["bbox"][1]
    assert all(line["size"] >= 9 for line in role_lines(card, "body"))
    assert_card_text(card)


def test_mixed_text_long_identifier_and_complete_footer():
    data = sample()
    text = "𠮷野の﨑（C++、Rust 1.0）a\u0301 ← CPU。 " + "raw_identifier_71629" * 15
    data["nodes"][0]["summary"] = text
    for index in range(45):
        ev = deepcopy(data["evidence"][0])
        ev["id"] = f"evi_extra{index}"
        data["evidence"].append(ev)
        data["claims"][0]["supporting_evidence_ids"].append(ev["id"])
    card = node(layout(data))
    assert_card_text(card)
    footer = role_lines(card, "footer")
    assert len(footer) > 1 and all(line["size"] >= 8 for line in footer)
    assert (
        reconstructed(footer)
        == "紀元前101年 " + " ".join(f"[{i}]" for i in range(1, 47)) + " 仮説"
    )


def test_bezier_routes_around_intervening_card_and_qa_rejects_collision():
    data = sample()
    add_node(data, "node_blocker", 1000, summary="迂回するノード")
    scene = layout(data)
    blocker = node(scene, "node_blocker")
    edge = scene["edges"][0]
    assert len(edge["path"]) > 4
    assert edge["target_node_id"] == "node_new"
    assert not any(
        curve_hits(c, padded(blocker["bbox"], 5)) for c in cubics(edge["path"])
    )
    a, b = edge["path"][0], edge["path"][-1]
    edge["path"] = [a, lerp(a, b, 1 / 3), lerp(a, b, 2 / 3), b]
    with pytest.raises(LayoutError) as exc:
        check_scene(scene, data)
    issue = exc.value.issues[0]
    assert (issue["type"], issue["edge_id"], issue["node_id"]) == (
        "edge_node_collision",
        "edge_link",
        "node_blocker",
    )


def test_multiple_edges_use_distinct_ports_including_parallel_and_self_edges():
    data = sample()
    for i in range(8):
        target = f"node_target_{i}"
        add_node(data, target, (i + 1) * 100)
        add_edge(data, f"edge_fan_{i}", "node_old", target)
    add_edge(data, "edge_parallel", "node_old", "node_new")
    add_edge(data, "edge_self", "node_old", "node_old")
    scene = layout(data)
    assert len(scene["edges"]) == len(data["edges"])
    assert len({tuple(edge["path"][0]) for edge in scene["edges"]}) == len(
        data["edges"]
    )
    for edge in scene["edges"]:
        assert edge["path"][-1] == edge["target_port"]["point"]
        assert edge["path"][0] == edge["source_port"]["point"]
    scene["edges"][0]["target_node_id"] = "node_unknown"
    with pytest.raises(LayoutError, match="incorrect endpoints"):
        check_scene(scene, data)


def test_dense_band_grows_lanes_and_does_not_invent_unknown_anchors():
    data = sample()
    for i in range(28):
        add_node(
            data,
            f"node_dense_{i}",
            -100,
            summary="長文 " * (i + 1),
            type="event" if i % 3 else "concept",
            importance={"level": i % 4 + 1, "reason": "同年テスト", "source": "test"},
        )
    scene = layout(data)
    past, future = scene["time_axis"]["anchors"]
    assert past[0] == -100 and future[0] == 1950
    same_band = [n for n in scene["nodes"] if n["anchor_year"] == past[0]]
    assert len(same_band) == 29
    assert all(n["anchor"][1] == past[1] for n in same_band)
    assert all(n["bbox"][1] + n["bbox"][3] < future[1] for n in same_band)
    assert node(scene, "node_unknown")["anchor_year"] is None
    assert node(scene, "node_unknown")["anchor"][1] > future[1]
    assert scene["lane_definitions"][3]["width"] > scene["lane_definitions"][1]["width"]
    assert check_scene(scene, data)["status"] == "pass"


def test_all_adopted_ids_and_scene_content_checked_against_input():
    data = sample()
    for i in range(25):
        target = f"node_full_{i}"
        add_node(data, target, 1950 + i)
        add_edge(data, f"edge_full_{i}", "node_new", target)
    data["edges"][-1]["adoption_status"] = "pending"
    scene = layout(data)
    graph = validate(data)
    assert {n["node_id"] for n in scene["nodes"]} == set(graph["adopted_node_ids"])
    assert {e["edge_id"] for e in scene["edges"]} == set(graph["adopted_edge_ids"])
    scene["edges"].pop()
    with pytest.raises(LayoutError, match="invalid drawn edge IDs"):
        check_scene(scene, data)


def test_shared_scene_artifact_text_connections_and_full_url(tmp_path):
    data = sample()
    long_url = "https://example.org/" + "source-0123_456789/" * 32 + "?a=1&b=2"
    data["evidence"][0]["source_url"] = long_url
    data["nodes"][0]["summary"] = "𠮷野の﨑と Rust 1.0（長い本文）\n" * 7 + "おしまい。"
    scene = layout(data)
    result = emit(scene, tmp_path)
    assert result["primary_format"] == "pdf", result
    assert (tmp_path / "map.pdf").read_bytes().startswith(b"%PDF")
    svg = (tmp_path / "map.svg").read_text()
    assert svg in (tmp_path / "map.html").read_text()
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    assert root.attrib["width"].endswith("pt")
    assert "data:font/ttf;base64," in root.find(f"{ns}defs/{ns}style").text
    link = root.find(f"{ns}a")
    assert link.get("href") == long_url
    assert "".join(t.text or "" for t in link.findall(f"{ns}text")).endswith(long_url)
    group = root.find(f"{ns}g[@data-edge-id='edge_link']")
    assert (group.get("data-source-id"), group.get("data-target-id")) == (
        "node_old",
        "node_new",
    )
    assert group.find(f"{ns}path").attrib["stroke-dasharray"] == "5,5"
    assert len(re.findall(r"\bC", group.find(f"{ns}path").attrib["d"])) == len(
        list(cubics(scene["edges"][0]["path"]))
    )
    fitz = pytest.importorskip("pymupdf")
    with fitz.open(tmp_path / "map.pdf") as pdf:
        assert len(pdf) == 1
        page = pdf[0]
        assert "𠮷野の﨑" in page.get_text()
        assert "おしまい。" in page.get_text()
        assert [link["uri"] for link in page.get_links()] == [long_url] * len(
            scene["sources"][0]["text_lines"]
        )


def test_existing_rust_map(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runs = sorted((root / "runs").glob("*_プログラミング言語Rust_*"))
    if not runs:
        pytest.skip("the local Rust research run is not checked into Git")
    import json

    run = runs[-1]
    progress = json.loads((run / "progress.json").read_text())
    data = load_generation(run / "snapshots" / progress["active_generation"])
    sidecar = run / "staging" / "layout-importance.json"
    weights = (
        json.loads(sidecar.read_text())
        if sidecar.is_file()
        else {
            "node_rust_one_zero": {
                "level": 4,
                "reason": "Rust 1.0の正式リリース",
                "source": "research",
            },
        }
    )
    scene = layout(data, progress["run_id"], progress["query"], importance=weights)
    assert check_scene(scene, data) == {
        "status": "pass",
        "node_count": 28,
        "edge_count": 31,
    }
    assert (
        node(scene, "node_rust_one_zero")["bbox"][2]
        > node(scene, "node_mod_team_crisis")["bbox"][2]
    )
    assert any(len(edge["path"]) > 4 for edge in scene["edges"])
    for card in scene["nodes"]:
        assert_card_text(card)
    assert emit(scene, tmp_path)["primary_format"] == "pdf"


@pytest.mark.parametrize("axis,extent,primary", [
    ("canvas_height", 17500, "pdf"),
    ("canvas_width", 17500, "pdf"),
    ("canvas_height", 28000, "svg"),
])
def test_pdf_scales_geometry_text_and_links(tmp_path, axis, extent, primary):
    import pymupdf

    data = sample()
    data["evidence"][0]["source_url"] = "https://example.org/scaling"
    scene = layout(data)
    scene[axis] = extent
    result = emit(scene, tmp_path)
    scale = 14000 / extent
    assert result["pdf_scale"] == pytest.approx(scale)
    assert result["pdf_min_text_pt"] == pytest.approx(8.5 * scale)
    assert result["primary_format"] == primary
    assert bool(result["pdf_warning"]) == (primary == "svg")
    with pymupdf.open(tmp_path / "map.pdf") as doc:
        page = doc[0]
        assert len(doc) == 1 and max(page.rect.width, page.rect.height) == 14000
        for link, line in zip(page.get_links(), scene["sources"][0]["text_lines"], strict=True):
            x, y, w, h = line["bbox"]
            assert tuple(link["from"]) == pytest.approx(
                [v * scale for v in (x, y, x+w, y+h)], abs=.02)
        drawings = page.get_drawings()
        edge = scene["edges"][0]
        path = next(d for d in drawings if any(item[0] == "c" for item in d["items"])
                    and d["width"] == pytest.approx(edge["style"]["width"] * scale))
        assert tuple(path["items"][0][1]) == pytest.approx(
            [v * scale for v in edge["path"][0]], abs=.02)
        arrow = next(d for d in drawings if d["type"] == "f" and d["fill"] == pytest.approx((112/255,131/255,152/255)))
        assert tuple(arrow["items"][0][1]) == pytest.approx(
            [v * scale for v in edge["arrow"][0]], abs=.02)
        assert scene["nodes"][0]["label"] in page.get_text()


def test_pdf_verification_detects_moved_link_and_missing_text(tmp_path):
    import pymupdf
    from truth_seeker.render import _check_pdf, render_pdf

    data = sample()
    data["evidence"][0]["source_url"] = "https://example.org/scaling"
    scene = layout(data)
    scene["canvas_height"] = 16500
    pdf = tmp_path / "map.pdf"
    render_pdf(scene, pdf)
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        link = page.get_links()[0]
        link["from"] = pymupdf.Rect(10, 10, 20, 20)
        page.update_link(link)
        doc.saveIncr()
    with pytest.raises(ValueError, match="link"):
        _check_pdf(scene, pdf)
    render_pdf(scene, pdf)
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        rect = page.search_for(scene["title"])[0]
        page.add_redact_annot(rect)
        page.apply_redactions()
        doc.saveIncr()
    with pytest.raises(ValueError, match="text differs"):
        _check_pdf(scene, pdf)


def test_registered_illustration_visibility_credit_and_placement(tmp_path):
    import base64
    import io
    import pymupdf
    from PIL import Image

    data = sample()
    image = Image.new("RGB", (30, 20), "#d51c56")
    image.save(tmp_path / "plate.png")
    asset = dict(id="asset_plate", schema_version="0.5", local_path="plate.png",
                 related_node_ids=["node_old"], caption="図版", rights_note="CC0",
                 source_url="https://example.org/plate")
    data["assets"] = [asset]
    scene = layout(data, run_dir=tmp_path)
    plate = scene["assets"][0]
    assert node(scene)["asset_refs"] == ["asset_plate"]
    assert plate["record"] == asset
    result = emit(scene, tmp_path)
    assert result["pdf_scale"] == 1 and result["primary_format"] == "pdf"
    x, y, w, h = plate["bbox"]
    svg = (tmp_path / "map.svg").read_text()
    assert svg in (tmp_path / "map.html").read_text()
    group = ET.fromstring(svg).find("{*}g[@data-asset-id='asset_plate']")
    encoded = group.find("{*}image").get("href").split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as decoded:
        assert decoded.getpixel((15, 10)) == image.getpixel((15, 10))
    with pymupdf.open(tmp_path / "map.pdf") as doc:
        page = doc[0]
        pix = page.get_pixmap(clip=pymupdf.Rect(x, y, x+w, y+h))
        assert pix.pixel(pix.width//2, pix.height//2) == image.getpixel((15, 10))
        assert "CC0" in page.get_text()
        assert asset["source_url"] in [link["uri"] for link in page.get_links()]
    plate["bbox"] = node(scene)["bbox"]
    with pytest.raises(LayoutError, match="reserved area"):
        check_scene(scene, data)


def test_reference_only_and_missing_registered_file(tmp_path):
    data = sample()
    entry = dict(id="asset_ref", schema_version="0.5", asset_type="external_reference",
                 source_url="https://example.org/source", rights_note="参照のみ")
    data["assets"] = [entry]
    ref = layout(data)["assets"][0]
    assert "image_data" not in ref and ref["record"]["rights_note"] == "参照のみ"
    entry.pop("asset_type")
    entry["local_path"] = "absent.jpg"
    with pytest.raises(LayoutError, match="absent.jpg"):
        layout(data, run_dir=tmp_path)


def test_recover_and_rerender_registered_embedded_figure(tmp_path):
    import json
    import pymupdf
    from PIL import Image
    import base64
    import io

    old = tmp_path / "render"
    old.mkdir()
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=200, height=150)
        page.draw_rect((12, 12, 23, 23), fill=(1, 0, 0))
        pdf.save(old / "map.pdf")
    (old / "scene.json").write_text(json.dumps({
        "canvas_width": 400, "canvas_height": 300,
        "figure": {"asset_id": "asset_diagram", "node_id": "node_old",
                   "bbox": [20, 20, 30, 30]},
    }))
    data = sample()
    data["assets"] = [dict(id="asset_diagram", schema_version="0.5", node_id="node_old",
                           type="diagram", title="模式図", rights_note="自作")]
    scene = layout(data, run_dir=tmp_path)
    payload = scene["assets"][0]["image_data"]
    with Image.open(io.BytesIO(base64.b64decode(payload))) as decoded:
        assert decoded.getpixel((decoded.width//2, decoded.height//2)) == (255, 0, 0)
    assert emit(scene, old)["primary_format"] == "pdf"
    assert layout(data, run_dir=tmp_path)["assets"][0]["image_data"] == payload
