"""Render a common drawing scene as one-page PDF, standalone SVG and HTML."""

import base64
import html
import io
import json
import math
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from collections import defaultdict
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
    for x, y, width, height in _background_bands(scene):
        parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="#FFFFFF" fill-opacity="0.42"/>'
        )
    for _, y in scene["time_axis"]["anchors"]:
        parts.append(
            f'<line x1="96" y1="{y}" x2="{w - 35}" y2="{y}" stroke="#A7BBC4" stroke-width="0.8" stroke-dasharray="4,5"/>'
        )
    for node in scene["nodes"]:
        if "anchor_leader" in node:
            parts.append(
                f'<polyline points="{_svg_points(node["anchor_leader"])}" fill="none" stroke="{node["color"]}" stroke-dasharray="2,3"/>'
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
            f'<g data-node-id="{html.escape(node["node_id"])}" data-importance="{node["importance"]["level"]}">'
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
        for line in asset["text_lines"]:
            text = svg_text(line)
            if line.get("source_url", "").startswith(("https://", "http://")):
                text = f'<a href="{html.escape(line["source_url"])}">{text}</a>'
            parts.append(text)
        parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def render_html(scene, svg):
    # The SVG remains the complete logical map; HTML provides an accessible full-ID lookup.
    menu = "".join(
        f'<li><a href="#n-{html.escape(n["node_id"])}" data-target="{html.escape(n["node_id"])}">{html.escape(n["label"])}</a></li>'
        for n in scene["nodes"]
    )
    return (
        """<!doctype html><html lang="ja"><meta charset="utf-8"><title>"""
        + html.escape(scene["title"])
        + """</title><style>body{margin:0;font-family:system-ui,sans-serif;background:#eee;color:#213245}header{position:sticky;top:0;background:white;padding:10px;z-index:2;border-bottom:1px solid #ddd}main{display:flex}aside{width:230px;overflow:auto;height:90vh;background:white}aside a{color:#325d92}article{flex:1;overflow:auto;height:90vh}svg{max-width:none}input{padding:8px}</style><header><b>"""
        + html.escape(scene["title"])
        + """</b>　<input id="search" placeholder="ノードを検索" aria-label="ノードを検索">　<span id="count"></span></header><main><aside><ul id="menu">"""
        + menu
        + """</ul></aside><article id="view">"""
        + svg
        + """</article></main><script>let nodes=[...document.querySelectorAll('[data-node-id]')],links=[...document.querySelectorAll('[data-target]')];document.getElementById('count').textContent=`${nodes.length} nodes`;document.getElementById('search').addEventListener('input',e=>{let s=e.target.value.toLowerCase();links.forEach(a=>a.parentElement.hidden=!a.textContent.toLowerCase().includes(s))});links.forEach(a=>a.addEventListener('click',e=>{e.preventDefault();let node=nodes.find(n=>n.getAttribute('data-node-id')===a.dataset.target);if(node){node.scrollIntoView({behavior:'smooth',block:'center',inline:'center'})}}));</script></html>"""
    )


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
            c.drawPath(line_path(node["anchor_leader"]))
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
    for line in all_text_lines(scene):
        label(line)
    for url, (x, y, ww, hh) in _link_areas(scene):
        c.linkURL(url, (x, h - y - hh, x + ww, h - y), relative=1)
    c.showPage()
    c.save()
    return path


def _check_svg(scene, svg, html_document):
    root = ET.fromstring(svg)
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


def check_saved_artifacts(scene, out_dir):
    """Check files on disk against the current, already-validated scene."""
    out_dir = Path(out_dir)
    result = json.loads((out_dir / "render_result.json").read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("invalid render result")
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
    count = sum("bbox" in asset for asset in scene.get("assets", []))
    return {name: count for name in formats}


def emit(scene, out_dir, force_pdf_error=False):
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
        "pdf_check": pdf_check,
        "pdf_scale": scale,
        "pdf_min_text_pt": minimum,
        "pdf_warning": warning,
    }
    (out_dir / "render_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
