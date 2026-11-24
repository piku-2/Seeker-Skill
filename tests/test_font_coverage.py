"""Regression: characters that previously failed the map font gate."""

import json
from copy import deepcopy
from xml.etree import ElementTree

import pymupdf
import pytest
from test_engine import sample

from truth_seeker.cli import main
from truth_seeker.model import LayoutError, ValidationError, dump_records, validate
from truth_seeker.render import emit, layout
from truth_seeker.scene import check_scene
from truth_seeker.typography import missing_glyphs

SCHOLARLY = "ꜣꜥ⁰⁵⁶⁷⁸⁹"


def test_map_font_covers_observed_missing_glyphs():
    assert missing_glyphs(SCHOLARLY) == []
    assert missing_glyphs("ファラオ𠮷") == []


def test_pdf_extracts_scholarly_glyphs(tmp_path):
    data = sample()
    data["nodes"][0]["label"] = "音価ꜣꜥ⁸"
    data["nodes"][0]["summary"] = "上付き⁸と𠮷"
    scene = layout(data, "run_glyph", "字形")
    result = emit(scene, tmp_path)
    assert result["primary_format"] == "pdf"
    assert result["scene_check"]["status"] == "pass"
    with pymupdf.open(tmp_path / "map.pdf") as doc:
        text = doc[0].get_text()
    for char in "ꜣꜥ⁸𠮷":
        assert char in text


def _multilingual_sample():
    data = sample()
    data["nodes"][0]["label"] = "写本 1QIsaᵃ"
    data["nodes"][0]["summary"] = "校訂者 Popović\r\n写本の説明"
    data["nodes"][-1]["label"] = "未採用の写本 מ"
    evidence = data["evidence"][0]
    evidence.update(
        access_status="accessible",
        source_url="https://example.org/scroll",
        locator="מגילת התרי־עשר",
    )
    data["assets"].append(
        {
            "id": "asset_comparison",
            "schema_version": "0.5",
            "asset_type": "generated",
            "node_id": "node_old",
            "title": "写本比較 1QIsaᵃ",
            "creator": "Popović",
            "rights_note": "原資料をもとに作成",
            "evidence_ids": ["evi_first"],
            "figure": {
                "kind": "table",
                "columns": ["資料", "読み"],
                "rows": [
                    {
                        "cells": ["1QIsaᵃ", "関連する記述"],
                        "evidence_ids": ["evi_first"],
                    },
                ],
            },
        }
    )
    return data


def _assert_font_diagnostics(issues):
    issue = issues[0]
    assert issue["type"] == "font_coverage"
    fields = {(f["record_id"], f["field"]): f["glyphs"] for f in issue["fields"]}
    assert fields["node_old", "label"] == ["ᵃ"]
    assert fields["node_old", "summary"] == ["ć"]
    assert "מ" in fields["evi_first", "locator"]
    assert fields["asset_comparison", "title"] == ["ᵃ"]
    assert fields["asset_comparison", "creator"] == ["ć"]
    assert fields["asset_comparison", "figure.rows.0.cells.0"] == ["ᵃ"]
    assert "node_pending" not in {record_id for record_id, _ in fields}


def test_font_check_reports_fields_before_layout(tmp_path, capsys, monkeypatch):
    data = _multilingual_sample()
    for kind, records in data.items():
        dump_records(tmp_path / f"{kind}.jsonl", records)

    assert main(["font-check", str(tmp_path)]) == 1
    _assert_font_diagnostics(json.loads(capsys.readouterr().err)["issues"])

    def unexpected_layout(*args, **kwargs):
        pytest.fail("layout proceeded after the font check failed")

    monkeypatch.setattr("truth_seeker.scene._prepare_assets", unexpected_layout)
    with pytest.raises(LayoutError) as exc:
        layout(data)
    _assert_font_diagnostics(exc.value.issues)

    data["nodes"][0]["display"] = {"label": "写本 1QIsaᵃ"}
    with pytest.raises(LayoutError) as exc:
        layout(data)
    assert {"record_id": "node_old", "field": "display.label", "glyphs": ["ᵃ"]} in (
        exc.value.issues[0]["fields"]
    )


def test_display_overrides_preserve_originals_and_match_all_formats(tmp_path, capsys):
    data = _multilingual_sample()
    data["nodes"][0]["display"] = {
        "label": "大イザヤ書写本",
        "summary": "校訂者 Popovic\r\n写本の説明",
    }
    data["evidence"][0]["display"] = {"locator": "十二小預言書の巻物"}
    data["assets"][0]["display"] = {
        "caption": "写本比較表",
        "creator": "Popovic",
        "figure.rows.0.cells.0": "1QIsa-a",
    }
    original = deepcopy(data)
    for kind, records in data.items():
        dump_records(tmp_path / f"{kind}.jsonl", records)

    assert main(["font-check", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"
    scene = layout(data)
    assert check_scene(scene, data)["status"] == "pass"
    assert emit(scene, tmp_path)["primary_format"] == "pdf"
    assert data == original
    assert scene["assets"][0]["record"] == original["assets"][0]

    with pymupdf.open(tmp_path / "map.pdf") as doc:
        assert len(doc) == 1
        pdf = doc[0].get_text()
        assert "https://example.org/scroll" in {
            link["uri"] for link in doc[0].get_links()
        }
    root = ElementTree.parse(tmp_path / "map.svg").getroot()
    svg = "".join(root.itertext())
    html = (tmp_path / "map.html").read_text()
    for label in (
        "大イザヤ書写本",
        "十二小預言書の巻物",
        "写本比較表",
        "Popovic",
        "1QIsa-a",
    ):
        assert label in svg
        assert label in html
        assert label in pdf
    assert "מגילת התרי־עשר" not in svg
    assert 'data-target="node_old">大イザヤ書写本</a>' in html


@pytest.mark.parametrize(
    ("kind", "display", "message"),
    [
        ("nodes", [], "display must be an object"),
        ("nodes", {"label": "  "}, "display.label must be nonblank text"),
        (
            "evidence",
            {"source_url": "https://example.org/replacement"},
            "unsupported display field",
        ),
        ("assets", {"figure.rows.9.cells.0": "無効"}, "unsupported display field"),
    ],
)
def test_display_overrides_validate_allowed_fields(kind, display, message):
    data = _multilingual_sample()
    data[kind][0]["display"] = display
    with pytest.raises(ValidationError, match=message):
        validate(data)
