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
