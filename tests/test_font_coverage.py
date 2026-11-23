"""Regression: characters that previously failed the map font gate."""

import pymupdf

from test_engine import sample
from truth_seeker.render import emit, layout
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
