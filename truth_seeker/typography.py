"""Shared TrueType metrics and line breaking for the map renderers."""

import base64
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_NAME = "TruthSeekerJP"
FONT_PATH = Path(__file__).with_name("fonts") / "NotoSansJP-Regular.ttf"
# Small kana, iteration marks and punctuation that should stay with the previous character.
NO_LINE_START = "、。，．・：；？！ー々ゝゞヽヾぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ…‥％%°,.;:!?"
LINE_SEPARATORS = str.maketrans({char: "\n" for char in "\r\v\f\x85\u2028\u2029"})
METRIC_SCALE = 1000
INK_MARGIN = 1.0


class _MapFont(TTFont):
    def addObjects(self, doc):
        # ReportLab versions using %04X for ToUnicode need UTF-16 surrogate
        # pairs for supplementary-plane glyphs (e.g. 𠮷).
        state = self.state.get(doc)
        subsets = () if state is None else state.subsets
        corrections = {
            self.getSubsetInternalName(index, doc)[1:]: {
                f"<{i:02X}> <{code:04X}>": f"<{i:02X}> <{chr(code).encode('utf-16-be').hex().upper()}>"
                for i, code in enumerate(subset)
                if code > 0xFFFF
            }
            for index, subset in enumerate(subsets)
            if any(code > 0xFFFF for code in subset)
        }
        super().addObjects(doc)
        for name, replacements in corrections.items():
            stream = doc.idToObject[doc.idToObject[name].ToUnicode.name]
            stream.content = "\n".join(
                replacements.get(line, line) for line in stream.content.splitlines()
            )


@lru_cache(maxsize=1)
def font():
    face = _MapFont(FONT_NAME, str(FONT_PATH))
    pdfmetrics.registerFont(face)
    return face


@lru_cache(maxsize=1)
def svg_font_data():
    return base64.b64encode(FONT_PATH.read_bytes()).decode("ascii")


@lru_cache(maxsize=1)
def _metric_font():
    basic = (
        ImageFont.Layout.BASIC
        if hasattr(ImageFont, "Layout")
        else ImageFont.LAYOUT_BASIC
    )
    return ImageFont.truetype(str(FONT_PATH), METRIC_SCALE, layout_engine=basic)


@lru_cache(maxsize=8192)
def _glyph(char):
    face = font().face
    advance = face.charWidths.get(ord(char), face.defaultWidth)
    return advance, _metric_font().getbbox(char, anchor="ls")


def missing_glyphs(text):
    coverage = font().face.charToGlyph
    return sorted({char for char in text if ord(char) not in coverage})


def _clusters(text):
    """Keep combining marks, variation selectors and joiner sequences together."""
    cluster = ""
    for char in text:
        if cluster and not (
            unicodedata.category(char) in {"Mn", "Mc", "Me"}
            or char == "\u200d"
            or cluster.endswith("\u200d")
        ):
            yield cluster
            cluster = ""
        cluster += char
    if cluster:
        yield cluster


def _natural_break(left, right):
    a, b = left[0], right[0]
    if (
        unicodedata.category(a) in {"Ps", "Pi"}
        or unicodedata.category(b) in {"Pe", "Pf"}
        or b in NO_LINE_START
        or b.isspace()
    ):
        return False
    return (
        a.isspace()
        or a in "/-‐–"
        or any(unicodedata.east_asian_width(c) in {"F", "W"} for c in (a, b))
    )


def display_text(text):
    return (
        str(text or "").replace("\r\n", "\n").translate(LINE_SEPARATORS).expandtabs(4)
    )


def missing_display_glyphs(text):
    """Check the characters that remain after the line wrapper normalizes text."""
    return missing_glyphs(display_text(text).replace("\n", ""))


def measure(text, size):
    """Return ink and advance bounds relative to the baseline origin, in points."""
    x = left = right = 0
    top, bottom = -font().face.ascent, -font().face.descent
    for char in text:
        advance, (x0, y0, x1, y1) = _glyph(char)
        left, right = min(left, x + x0), max(right, x + x1)
        top, bottom = min(top, y0), max(bottom, y1)
        x += advance
    scale = size / METRIC_SCALE
    return left * scale, top * scale, max(right, x) * scale, bottom * scale


def wrap(text, size, width):
    """Return complete lines with explicit line breaks separated from soft wraps."""
    paragraphs = display_text(text).split("\n")
    for index, paragraph in enumerate(paragraphs):
        clusters = list(_clusters(paragraph))
        advances = [
            sum(_glyph(char)[0] for char in part) * size / METRIC_SCALE
            for part in clusters
        ]
        start = 0
        while start < len(clusters):
            end, advance, natural = start, 0, start
            while end < len(clusters) and advance + advances[end] <= width:
                advance += advances[end]
                end += 1
                if end < len(clusters) and _natural_break(
                    clusters[end - 1], clusters[end]
                ):
                    natural = end
            if end < len(clusters) and natural > start:
                end = natural
            end = max(start + 1, end)
            value = "".join(clusters[start:end])
            left, _, right, _ = measure(value, size)
            while right - left > width and end > start + 1:
                end -= 1
                value = "".join(clusters[start:end])
                left, _, right, _ = measure(value, size)
            yield value, end == len(clusters) and index < len(paragraphs) - 1
            start = end
        if not clusters:
            yield "", index < len(paragraphs) - 1


def text_lines(text, x, top, width, size, fill, role):
    lines = []
    for value, line_break in wrap(text, size, width - 2 * INK_MARGIN):
        left, ascent, right, descent = measure(value, size)
        baseline = top - ascent
        origin = x + INK_MARGIN - left
        height = descent - ascent
        lines.append(
            {
                "text": value,
                "break_after": line_break,
                "role": role,
                "x": origin,
                "y": baseline,
                "size": size,
                "fill": fill,
                "bbox": [origin + left, top, right - left, height],
            }
        )
        top += height + size * 0.22
    return lines
