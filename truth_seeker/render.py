"""Render a common drawing scene as one-page PDF, standalone SVG and HTML."""

import base64
import hashlib
import html
import io
import json
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

from .model import LayoutError
from .routing import cubics
from .scene import all_text_lines, check_scene, layout
from .typography import FONT_NAME, font, svg_font_data

SVG_NS = "{http://www.w3.org/2000/svg}"
__all__ = ["check_scene", "emit", "layout", "render_html", "render_pdf", "render_svg"]


def svg_text(line):
    return (
        f'<text x="{line["x"]:.4f}" y="{line["y"]:.4f}" fill="{line["fill"]}" '
        f'font-size="{line["size"]}" data-role="{line["role"]}" xml:space="preserve">'
        f"{html.escape(line['text'])}</text>"
    )


def _svg_points(points):
    return " ".join(f"{x:.4f},{y:.4f}" for x, y in points)


def _source_url(ref):
    url = ref["source_url"]
    return (
        url
        if isinstance(url, str) and url.startswith(("https://", "http://"))
        else None
    )


def _edge_tooltip(edge):
    label = edge["label"] or edge["relation_type"]
    if "importance" in edge:
        reason = edge["importance"]["reason"]
        label += f"（L{edge['importance']['level']}・{reason}）"
    return label


def _background_bands(scene):
    if scene["layout_revision"] == 2:
        top = scene["lane_top"]
        for lane in scene["lane_definitions"]:
            yield lane["x"], top, lane["width"] - 4, scene["sources_y"] - top
    else:
        anchors = [y for _, y in scene["time_axis"]["anchors"]]
        anchors.extend((scene["time_axis"]["unknown_y"], scene["sources_y"] - 26))
        for i in range(0, len(anchors) - 1, 2):
            yield (
                96,
                anchors[i],
                scene["canvas_width"] - 131,
                anchors[i + 1] - anchors[i],
            )


def render_svg(scene):
    w, h = scene["canvas_width"], scene["canvas_height"]
    grid = scene["styles"]["grid"]
    background = scene["styles"]["background"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}pt" height="{h}pt" viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(scene["title"])}">',
        "<defs>",
        (
            f'<style>@font-face{{font-family:{FONT_NAME};src:url(data:font/ttf;base64,{svg_font_data()}) format("truetype")}}'
            f"text{{font-family:{FONT_NAME},sans-serif;font-kerning:none;font-variant-ligatures:none;white-space:pre}}</style>"
        ),
        f'<pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0 L0 0 0 20" fill="none" stroke="{grid}" stroke-width="0.65"/></pattern>',
        "</defs>",
        f'<rect width="100%" height="100%" fill="{background}"/><rect width="100%" height="100%" fill="url(#grid)"/>',
    ]
    if "execution" in scene:
        metadata = html.escape(json.dumps(scene["execution"], ensure_ascii=False))
        parts.append(f'<metadata id="execution">{metadata}</metadata>')
    for x, y, width, height in _background_bands(scene):
        parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="#FFFFFF" fill-opacity="0.42"/>'
        )
    for _, y in scene["time_axis"]["anchors"]:
        parts.append(
            f'<line x1="96" y1="{y}" x2="{w - 35}" y2="{y}" stroke="#A7BBC4" stroke-width="0.8" stroke-dasharray="4,5"/>'
        )
    for node in scene["nodes"]:
        if "anchor_leader_segments" in node:
            segments = "".join(
                f'<polyline points="{_svg_points(points)}"/>'
                for points in node["anchor_leader_segments"]
            )
            parts.append(
                f'<g data-anchor-leader="{html.escape(node["node_id"])}" fill="none" '
                f'stroke="{node["color"]}" stroke-dasharray="2,3">{segments}</g>'
            )
        elif "anchor_leader" in node:
            parts.append(
                f'<polyline data-anchor-leader="{html.escape(node["node_id"])}" points="{_svg_points(node["anchor_leader"])}" fill="none" stroke="{node["color"]}" stroke-dasharray="2,3"/>'
            )
    for edge in scene["edges"]:
        style = edge["style"]
        path = "M" + _svg_points(edge["path"][:1])
        path += " " + " ".join(
            "C" + _svg_points(curve[1:]) for curve in cubics(edge["path"])
        )
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        parts.append(
            f'<g data-edge-id="{html.escape(edge["edge_id"])}" data-source-id="{html.escape(edge["source_node_id"])}" '
            f'data-target-id="{html.escape(edge["target_node_id"])}">'
            f"<title>{html.escape(_edge_tooltip(edge))}</title>"
            f'<path d="{path}" fill="none" stroke="{style["stroke"]}" stroke-width="{style["width"]}"{dash}/>'
            f'<polygon points="{_svg_points(edge["arrow"])}" fill="{style["stroke"]}"/></g>'
        )
    for node in scene["nodes"]:
        x, y, ww, hh = node["bbox"]
        style = node["style"]
        parts.append(
            f'<g data-node-id="{html.escape(node["node_id"])}" id="n-{html.escape(node["node_id"])}" data-importance="{node["importance"]["level"]}">'
            f'<rect x="{x}" y="{y}" width="{ww}" height="{hh}" rx="9" fill="{style["fill"]}" '
            f'stroke="{style["stroke"]}" stroke-width="{style["width"]}"/>'
        )
        parts.extend(svg_text(line) for line in node["text_lines"])
        parts.append("</g>")
    parts.extend(
        svg_text(line)
        for line in scene["header_lines"]
        + scene["time_axis"]["labels"]
        + scene["sources_heading"]
    )
    for ref in scene["sources"]:
        url = _source_url(ref)
        if url:
            parts.append(f'<a href="{html.escape(url)}">')
        parts.extend(svg_text(line) for line in ref["text_lines"])
        if url:
            parts.append("</a>")
    for asset in scene.get("assets", []):
        parts.append(f'<g data-asset-id="{html.escape(asset["id"])}">')
        if asset.get("caption"):
            parts.append(f"<title>{html.escape(asset['caption'])}</title>")
        if "bbox" in asset:
            x, y, w, h = asset["bbox"]
            parts.append(
                f'<image x="{x}" y="{y}" width="{w}" height="{h}" '
                f'href="data:{asset["mime"]};base64,{asset["image_data"]}"/>'
            )
        for shape in asset.get("shapes", []):
            x, y, w, h = shape["bbox"]
            parts.append(
                f'<rect data-figure-shape="1" x="{x}" y="{y}" width="{w}" height="{h}" fill="{shape["fill"]}"/>'
            )
        for line in asset["text_lines"]:
            text = svg_text(line)
            if line.get("source_url", "").startswith(("https://", "http://")):
                text = f'<a href="{html.escape(line["source_url"])}">{text}</a>'
            parts.append(text)
        parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def render_html(scene, svg):
    from .explorer import render

    return render(scene, svg)


def _link_areas(scene):
    for ref in scene["sources"]:
        url = _source_url(ref)
        if url:
            for line in ref["text_lines"]:
                yield url, line["bbox"]
    for asset in scene.get("assets", []):
        for line in asset.get("text_lines", []):
            url = line.get("source_url", "")
            if url.startswith(("https://", "http://")):
                yield url, line["bbox"]


def _pdf_scale(scene):
    return min(1.0, 14000 / max(scene["canvas_width"], scene["canvas_height"]))


# Lower bound for annotations at native PDF size, in typographic points.
MIN_SCALED_TEXT_PT = 6.0


def render_pdf(scene, path):
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    w, h = scene["canvas_width"], scene["canvas_height"]
    from reportlab.lib.utils import ImageReader

    scale = _pdf_scale(scene)
    font()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(w * scale, h * scale), pageCompression=1)
    # All drawing and relative annotations below use original scene units.
    c.scale(scale, scale)
    c.setTitle(scene["title"])

    def flip(point):
        return point[0], h - point[1]

    def line_path(points):
        p = c.beginPath()
        p.moveTo(*flip(points[0]))
        for point in points[1:]:
            p.lineTo(*flip(point))
        return p

    def label(line):
        text = line["text"]
        if text:
            c.setFillColor(HexColor(line["fill"]))
            c.setFont(FONT_NAME, line["size"])
            c.drawString(line["x"], h - line["y"], text)

    c.setFillColor(HexColor(scene["styles"]["background"]))
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setStrokeColor(HexColor(scene["styles"]["grid"]))
    c.setLineWidth(0.65)
    for x in range(0, math.ceil(w), 20):
        c.line(x, 0, x, h)
    for y in range(0, math.ceil(h), 20):
        c.line(0, y, w, y)

    c.saveState()
    c.setFillColorRGB(1, 1, 1, alpha=0.42)
    for x, y, width, height in _background_bands(scene):
        c.rect(x, h - y - height, width, height, fill=1, stroke=0)
    c.restoreState()
    c.setStrokeColor(HexColor("#A7BBC4"))
    c.setLineWidth(0.8)
    c.setDash(4, 5)
    for _, y in scene["time_axis"]["anchors"]:
        c.line(96, h - y, w - 35, h - y)
    c.setDash(2, 3)
    for node in scene["nodes"]:
        if "anchor_leader" in node:
            c.setStrokeColor(HexColor(node["color"]))
            for points in node.get("anchor_leader_segments", [node["anchor_leader"]]):
                c.drawPath(line_path(points))
    c.setDash()
    for edge in scene["edges"]:
        style = edge["style"]
        color = HexColor(style["stroke"])
        c.setStrokeColor(color)
        c.setLineWidth(style["width"])
        if style["dash"]:
            c.setDash([int(value) for value in style["dash"].split(",")])
        p = c.beginPath()
        p.moveTo(*flip(edge["path"][0]))
        for _, a, b, end in cubics(edge["path"]):
            p.curveTo(*flip(a), *flip(b), *flip(end))
        c.drawPath(p, stroke=1, fill=0)
        c.setDash()
        arrow = line_path(edge["arrow"])
        arrow.close()
        c.setFillColor(color)
        c.drawPath(arrow, stroke=0, fill=1)
    for node in scene["nodes"]:
        x, y, ww, hh = node["bbox"]
        style = node["style"]
        c.setFillColor(HexColor(style["fill"]))
        c.setStrokeColor(HexColor(style["stroke"]))
        c.setLineWidth(style["width"])
        c.roundRect(x, h - y - hh, ww, hh, 9, fill=1, stroke=1)
    for asset in scene.get("assets", []):
        if "bbox" in asset:
            x, y, iw, ih = asset["bbox"]
            image = ImageReader(io.BytesIO(base64.b64decode(asset["image_data"])))
            c.drawImage(image, x, h - y - ih, iw, ih, mask="auto")
        for shape in asset.get("shapes", []):
            x, y, sw, sh = shape["bbox"]
            c.setFillColor(HexColor(shape["fill"]))
            c.rect(x, h - y - sh, sw, sh, fill=1, stroke=0)
    for line in all_text_lines(scene):
        label(line)
    for url, (x, y, ww, hh) in _link_areas(scene):
        c.linkURL(url, (x, h - y - hh, x + ww, h - y), relative=1)
    c.showPage()
    c.save()
    return path


def _check_svg(scene, svg, html_document):
    from .explorer import check_data

    check_data(scene, html_document)
    root = ET.fromstring(svg)
    if "execution" in scene:
        metadata = root.find(f"{SVG_NS}metadata[@id='execution']")
        if metadata is None or metadata.text != json.dumps(
            scene["execution"], ensure_ascii=False
        ):
            raise LayoutError(
                "artifact_metadata", "SVG execution metadata differs from scene"
            )
    nodes = {
        group.get("data-node-id"): group
        for group in root.findall(f"{SVG_NS}g")
        if group.get("data-node-id")
    }
    edges = {
        group.get("data-edge-id"): group
        for group in root.findall(f"{SVG_NS}g")
        if group.get("data-edge-id")
    }
    if set(nodes) != set(scene["expected_node_ids"]) or set(edges) != set(
        scene["expected_edge_ids"]
    ):
        raise LayoutError("artifact_ids", "SVG IDs differ from the drawing scene")
    for node in scene["nodes"]:
        key = node["node_id"]
        actual_lines = [line.text or "" for line in nodes[key].findall(f"{SVG_NS}text")]
        if actual_lines != [line["text"] for line in node["text_lines"]]:
            raise LayoutError(
                "artifact_text", f"{key}: SVG text differs from scene", node_id=key
            )
    for eid, endpoints in scene["edge_endpoints"].items():
        if [
            edges[eid].get("data-source-id"),
            edges[eid].get("data-target-id"),
        ] != endpoints:
            raise LayoutError(
                "artifact_edge",
                f"{eid}: SVG connection differs from scene",
                edge_id=eid,
            )
    groups = root.findall(f"{SVG_NS}g[@data-asset-id]")
    if [g.get("data-asset-id") for g in groups] != [
        a["id"] for a in scene.get("assets", [])
    ]:
        raise ValueError("SVG asset IDs differ from scene")
    if len(list(root.iter(f"{SVG_NS}image"))) != sum(
        "bbox" in asset for asset in scene.get("assets", [])
    ):
        raise ValueError("SVG image count differs from scene")
    for group, asset in zip(groups, scene.get("assets", [])):
        image = group.find(f"{SVG_NS}image")
        if "bbox" in asset:
            if (
                image is None
                or image.get("href")
                != f"data:{asset['mime']};base64,{asset['image_data']}"
            ):
                raise ValueError("SVG image differs from scene")
            if [
                float(image.get(v, "nan")) for v in ("x", "y", "width", "height")
            ] != asset["bbox"]:
                raise ValueError("SVG image bounds differ from scene")
        shapes = group.findall(f"{SVG_NS}rect[@data-figure-shape]")
        expected_shapes = asset.get("shapes", [])
        if len(shapes) != len(expected_shapes) or any(
            s.get("fill") != e["fill"]
            or [float(s.get(field, "nan")) for field in ("x", "y", "width", "height")]
            != e["bbox"]
            for s, e in zip(shapes, expected_shapes)
        ):
            raise ValueError("SVG vector figure differs from scene")
        expected_links = [
            line["source_url"]
            for line in asset["text_lines"]
            if line.get("source_url", "").startswith(("https://", "http://"))
        ]
        if [link.get("href") for link in group.findall(f"{SVG_NS}a")] != expected_links:
            raise ValueError("SVG illustration source links differ from scene")
    expected = [line["text"] for line in all_text_lines(scene)]
    # SVG groups place text in paint order, which differs from the PDF text layer.
    actual = [line.text or "" for line in root.iter(f"{SVG_NS}text")]
    if sorted(actual) != sorted(expected) or svg not in html_document:
        raise LayoutError(
            "artifact_text", "SVG or HTML text differs from the drawing scene"
        )


def _check_pdf_edges(drawings, edges, scale):
    def same_points(actual, expected, operation):
        return len(actual) == len(expected) and all(
            item[0] == operation
            and all(
                abs(a - e * scale) < 0.03
                for actual_point, expected_point in zip(item[1:], points, strict=True)
                for a, e in zip(actual_point, expected_point, strict=True)
            )
            for item, points in zip(actual, expected)
        )

    curves = [
        (index, drawing)
        for index, drawing in enumerate(drawings)
        if drawing["type"] == "s" and any(item[0] == "c" for item in drawing["items"])
    ]
    if len(curves) != len(edges):
        raise ValueError("PDF edge count differs from scene")
    for (index, drawing), edge in zip(curves, edges):
        style = edge["style"]
        stroke = tuple(bytes.fromhex(style["stroke"][1:]))
        expected_dashes = [int(value) for value in style["dash"].split(",") if value]
        dash_array = drawing["dashes"].split("[", 1)[1].split("]", 1)[0]
        actual_dashes = [float(value) for value in dash_array.split()]
        if (
            not same_points(drawing["items"], list(cubics(edge["path"])), "c")
            or tuple(round(value * 255) for value in drawing["color"]) != stroke
            or abs(drawing["width"] - style["width"] * scale) >= 0.03
            or len(actual_dashes) != len(expected_dashes)
            or any(
                abs(a - b * scale) >= 0.03
                for a, b in zip(actual_dashes, expected_dashes)
            )
        ):
            raise ValueError(
                f"PDF edge geometry or style differs from scene for {edge['edge_id']}"
            )
        arrow = edge["arrow"]
        polygon = drawings[index + 1]
        closed = [*arrow, arrow[0]]
        if polygon["type"] != "f" or not same_points(
            polygon["items"], list(pairwise(closed)), "l"
        ):
            raise ValueError(f"PDF arrow differs from scene for {edge['edge_id']}")


def _check_pdf(scene, output):
    if not output.is_file() or not output.read_bytes().startswith(b"%PDF"):
        raise ValueError("invalid PDF output")
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            raise ValueError("PyMuPDF required for PDF content verification")
    with pymupdf.open(output) as document:
        if len(document) != 1:
            raise ValueError("PDF was not single page")
        page = document[0]
        scale = _pdf_scale(scene)

        def page_rect(box):
            x, y, w, h = box
            return (x * scale, y * scale, (x + w) * scale, (y + h) * scale)

        def close_rect(actual, expected):
            return max(abs(a - b) for a, b in zip(actual, expected)) < 0.03

        if not close_rect(
            page.rect, page_rect((0, 0, scene["canvas_width"], scene["canvas_height"]))
        ):
            raise ValueError("PDF page bounds differ from scene")
        links = [(link.get("uri"), link["from"]) for link in page.get_links()]
        expected_links = [(url, page_rect(box)) for url, box in _link_areas(scene)]
        if len(links) != len(expected_links) or any(
            url != expected or not close_rect(rect, box)
            for (url, rect), (expected, box) in zip(links, expected_links)
        ):
            raise ValueError("PDF source link URL or rectangle differs from scene")
        boxes = [page_rect(a["bbox"]) for a in scene.get("assets", []) if "bbox" in a]
        images = page.get_image_info()
        if len(boxes) != len(images) or any(
            not close_rect(im["bbox"], box) for im, box in zip(images, boxes)
        ):
            raise ValueError("PDF image bounds differ from scene")
        shapes = [
            shape for a in scene.get("assets", []) for shape in a.get("shapes", [])
        ]
        drawings = page.get_drawings() if shapes or "explorer_research" in scene else []
        if "explorer_research" in scene:
            _check_pdf_edges(drawings, scene["edges"], scale)
            cards = [drawing for drawing in drawings if drawing["type"] == "fs"]
            if len(cards) != len(scene["nodes"]):
                raise ValueError("PDF card count differs from scene")
            for card, node in zip(cards, scene["nodes"]):
                style = node["style"]
                if (
                    not close_rect(card["rect"], page_rect(node["bbox"]))
                    or abs(card["width"] - style["width"] * scale) >= 0.03
                    or tuple(round(value * 255) for value in card["color"])
                    != tuple(bytes.fromhex(style["stroke"][1:]))
                ):
                    raise ValueError(
                        f"PDF card geometry or style differs from scene for {node['node_id']}"
                    )
        if shapes:
            rectangles = defaultdict(list)
            for drawing in drawings:
                if drawing["fill"] is not None:
                    color = tuple(round(value * 255) for value in drawing["fill"])
                    rectangles[color].extend(
                        item[1] for item in drawing["items"] if item[0] == "re"
                    )
            for shape in shapes:
                color = tuple(bytes.fromhex(shape["fill"][1:]))
                if not any(
                    close_rect(actual, page_rect(shape["bbox"]))
                    for actual in rectangles[color]
                ):
                    raise ValueError("PDF vector figure geometry differs from scene")
        # Extract glyph origins instead of clipping against renderer-specific ascenders.
        baselines = defaultdict(list)
        for block in page.get_text(
            "rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        )["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    for char in span["chars"]:
                        x, y = char["origin"]
                        baselines[y / scale].append((x / scale, char["c"]))
        rows = sorted(baselines)
        for line in all_text_lines(scene):
            if not line["text"].strip():
                continue
            left, _, width, _ = line["bbox"]
            start = bisect_left(rows, line["y"] - 0.02)
            end = bisect_right(rows, line["y"] + 0.02)
            row = [char for y in rows[start:end] for char in baselines[y]]
            read = "".join(
                char
                for x, char in sorted(row, key=lambda glyph: glyph[0])
                if left - 0.02 <= x < left + width - 0.02
            )
            if read.strip() != line["text"].strip():
                raise ValueError(
                    f"PDF text differs from the drawing scene at {line['role']} ({line['x']:.1f}, {line['y']:.1f})"
                )
    return "page_and_text"


def fingerprint(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()


def scene_fingerprint(scene):
    return hashlib.sha256(
        json.dumps(scene, ensure_ascii=False, indent=2).encode()
    ).hexdigest()


def _check_bundle(scene, out_dir, result):
    if "scene_sha256" in result:
        if (
            result["scene_sha256"] != scene_fingerprint(scene)
            or fingerprint(out_dir / "scene.json") != result["scene_sha256"]
        ):
            raise ValueError("saved scene fingerprint differs from render result")
        if result.get("generation") != scene.get("graph_generation") or result.get(
            "image_profile"
        ) != scene.get("image_profile", "distribution"):
            raise ValueError(
                "render result belongs to a different generation or image profile"
            )
        recorded = result["artifact_sha256"]
        present = {
            f"map.{fmt}"
            for fmt in ("pdf", "svg", "html")
            if (out_dir / f"map.{fmt}").is_file()
        }
        if set(recorded) != present or any(
            fingerprint(out_dir / name) != sha for name, sha in recorded.items()
        ):
            raise ValueError("saved artifact fingerprint differs from render result")


def check_saved_artifacts(scene, out_dir):
    """Check files on disk against the current, already-validated scene."""
    out_dir = Path(out_dir)
    result = json.loads((out_dir / "render_result.json").read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("invalid render result")  # noqa: TRY004 - invalid saved JSON value
    _check_bundle(scene, out_dir, result)
    _check_svg(
        scene,
        (out_dir / "map.svg").read_text(encoding="utf-8"),
        (out_dir / "map.html").read_text(encoding="utf-8"),
    )
    pdf = out_dir / "map.pdf"
    primary = result.get("primary_format")
    if primary not in ("pdf", "svg") or (
        primary == "svg" and not result.get("pdf_fallback_reason")
    ):
        raise ValueError("invalid primary format or undocumented PDF fallback")
    formats = ["svg", "html"]
    if pdf.is_file() or primary == "pdf":
        _check_pdf(scene, pdf)
        formats.append("pdf")
    count = sum(
        "bbox" in asset or "figure_bbox" in asset for asset in scene.get("assets", [])
    )
    return {name: count for name in formats}


def _build_outputs(scene, out_dir, force_pdf_error=False):
    scene_check = check_scene(scene)
    svg = render_svg(scene)
    html_document = render_html(scene, svg)
    _check_svg(scene, svg, html_document)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scene.json").write_text(
        json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    svg_path, html_path, pdf_path = (
        out_dir / f"map.{extension}" for extension in ("svg", "html", "pdf")
    )
    svg_path.write_text(svg, encoding="utf-8")
    html_path.write_text(html_document, encoding="utf-8")
    reason, pdf_check = None, None
    scale = _pdf_scale(scene)
    minimum = (
        min(line["size"] for line in all_text_lines(scene) if line["text"]) * scale
    )
    warning = None
    try:
        if force_pdf_error:
            raise ValueError("injected PDF failure")
        render_pdf(scene, pdf_path)
        pdf_check = _check_pdf(scene, pdf_path)
        if scale < 1 and minimum < MIN_SCALED_TEXT_PT:
            warning = f"PDF最小文字サイズ {minimum:.2f} pt は可読性の目安 {MIN_SCALED_TEXT_PT:g} pt 未満です。"
            reason = warning
            primary, output = "svg", svg_path
        else:
            primary, output = "pdf", pdf_path
    except Exception as exc:  # noqa: BLE001 - renderer and reader failures trigger SVG fallback
        reason = str(exc)
        primary, output = "svg", svg_path
        pdf_path.unlink(missing_ok=True)
    result = {
        "primary_format": primary,
        "primary_path": str(output),
        "pdf_fallback_reason": reason,
        "scene_check": scene_check,
        "svg_path": str(svg_path),
        "html_path": str(html_path),
        "canvas_width": scene["canvas_width"],
        "canvas_height": scene["canvas_height"],
        "coverage": {
            "nodes": len(scene["nodes"]),
            "edges": len(scene["edges"]),
            "images": sum("bbox" in a for a in scene.get("assets", [])),
            "generated_figures": sum(
                "figure_bbox" in a for a in scene.get("assets", [])
            ),
            "missing_nodes": [],
            "missing_edges": [],
            "missing_assets": [],
        },
        "pdf_check": pdf_check,
        "pdf_scale": scale,
        "pdf_min_text_pt": minimum,
        "pdf_warning": warning,
    }
    return result


def _write_result(scene, result, staging, destination, names):
    if "execution" in scene:
        result.setdefault("published_at", datetime.now(timezone.utc).isoformat())
    result.update(
        generation=scene.get("graph_generation"),
        image_profile=scene.get("image_profile", "distribution"),
        scene_sha256=scene_fingerprint(scene),
        artifact_sha256={
            name: fingerprint(
                (staging if (staging / name).is_file() else destination) / name
            )
            for name in names
        },
        primary_path=str(destination / f"map.{result['primary_format']}"),
        svg_path=str(destination / "map.svg"),
        html_path=str(destination / "map.html"),
        formal_formats=[fmt for fmt in ("pdf", "html", "svg") if f"map.{fmt}" in names],
    )
    (staging / "render_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _publish(staging, destination, names):
    # Put the receipt last. If an ordinary I/O error interrupts publication,
    # restore the previous bundle with links to its original files.
    backup = staging / "previous"
    backup.mkdir()
    replaced = []
    try:
        for name in names:
            old = destination / name
            if old.is_file():
                os.link(old, backup / name)
            os.replace(staging / name, old)
            replaced.append(name)
    except OSError:
        for name in reversed(replaced):
            original = backup / name
            if original.is_file():
                os.replace(original, destination / name)
            else:
                (destination / name).unlink(missing_ok=True)
        raise


def emit(scene, out_dir, force_pdf_error=False):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as temporary:
        staging = Path(temporary)
        result = _build_outputs(scene, staging, force_pdf_error)
        if (out_dir / "map.pdf").is_file() and not (staging / "map.pdf").is_file():
            try:
                previous = json.loads(
                    (out_dir / "render_result.json").read_text(encoding="utf-8")
                )
                if previous.get("scene_sha256") != scene_fingerprint(scene) or previous[
                    "artifact_sha256"
                ].get("map.pdf") != fingerprint(out_dir / "map.pdf"):
                    raise ValueError(
                        "previous PDF is from a different scene or has changed"
                    )
                result["pdf_check"] = _check_pdf(scene, out_dir / "map.pdf")
            except (ValueError, OSError, RuntimeError, KeyError) as exc:
                raise LayoutError(
                    "pdf_render",
                    "PDF generation failed; existing bundle preserved",
                    reason=result["pdf_fallback_reason"],
                    previous=str(out_dir),
                    resume="retry render or inspect the existing bundle",
                ) from exc
            result["pdf_render_error"] = result["pdf_fallback_reason"]
            result["primary_format"] = "pdf" if not result["pdf_warning"] else "svg"
            if result["primary_format"] == "pdf":
                result["pdf_fallback_reason"] = None
            result["preserved_pdf"] = True
        names = ["map.svg", "map.html"]
        if (staging / "map.pdf").is_file() or result.get("preserved_pdf"):
            names.append("map.pdf")
        _write_result(scene, result, staging, out_dir, names)
        _publish(
            staging,
            out_dir,
            ["scene.json"]
            + [n for n in names if (staging / n).exists()]
            + ["render_result.json"],
        )
    return result


def emit_one(scene, out_dir, fmt, *, force_pdf_error=False):
    """Replace a single artifact from the already published, verified scene."""
    out_dir = Path(out_dir)
    result = json.loads((out_dir / "render_result.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(dir=out_dir) as temporary:
        staging = Path(temporary)
        name = f"map.{fmt}"
        target = staging / name
        try:
            if fmt == "pdf":
                if force_pdf_error:
                    raise ValueError("injected PDF failure")
                render_pdf(scene, target)
                result["pdf_check"] = _check_pdf(scene, target)
                if result.get("pdf_warning"):
                    result["primary_format"] = "svg"
                    result["pdf_fallback_reason"] = result["pdf_warning"]
                else:
                    result["primary_format"] = "pdf"
                    result["pdf_fallback_reason"] = None
            else:
                previous_svg = (
                    (out_dir / "map.svg").read_text(encoding="utf-8")
                    if fmt == "html"
                    else render_svg(scene)
                )
                document = (
                    render_html(scene, previous_svg)
                    if fmt == "html"
                    else (out_dir / "map.html").read_text(encoding="utf-8")
                )
                _check_svg(scene, previous_svg, document)
                target.write_text(
                    document if fmt == "html" else previous_svg, encoding="utf-8"
                )
        except (ValueError, OSError, RuntimeError) as exc:
            raise LayoutError(
                f"{fmt}_render",
                "single-format regeneration failed; existing artifacts preserved",
                reason=str(exc),
                previous=str(out_dir),
                resume="retry the same format or render all formats",
            ) from exc
        names = set(result["artifact_sha256"]) | {name}
        if fmt == "pdf":
            result.pop("preserved_pdf", None)
            result.pop("pdf_render_error", None)
        _write_result(scene, result, staging, out_dir, sorted(names))
        _publish(staging, out_dir, [name, "render_result.json"])
    return result
