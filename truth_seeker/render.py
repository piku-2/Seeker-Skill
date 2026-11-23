"""Full-ID time-anchored lane layout; SVG, HTML and single-page PDF renderers."""

import html
import json
import math
from pathlib import Path

from .model import ValidationError, validate

STATUS_JA = {
    "verified": "検証済み",
    "favored": "有力説",
    "contested": "論争中",
    "hypothetical": "仮説",
    "unverified": "未検証",
}
PALETTE = {
    "concept": "#4B78AC",
    "technology": "#427EAD",
    "invention": "#D38B43",
    "person": "#8061A5",
    "organization": "#598D68",
    "product": "#D38B43",
    "event": "#A77666",
    "culture": "#8061A5",
    "document": "#78916F",
    "object": "#D38B43",
    "place": "#5D947E",
    "other": "#66788B",
}
LANES = [
    ("Ideas", ("concept", "culture", "document")),
    ("People", ("person", "organization")),
    ("Technology", ("technology", "invention", "product", "object")),
    ("Events", ("event", "place", "other")),
]
NODE_W = 205
NODE_H = 76


def date_text(y):
    if y is None:
        return "年代不明"
    y = round(y)
    return f"紀元前{1 - y}年" if y <= 0 else f"{y}年"


def layout(data, run_id="run_manual", title="歴史知識地図"):
    checked = validate(data)
    nodes = [n for n in data["nodes"] if n["adoption_status"] == "adopted"]
    edges = [e for e in data["edges"] if e["adoption_status"] == "adopted"]
    by_id = {n["id"]: n for n in nodes}
    claims = {c["id"]: c for c in data["claims"]}
    evidence = {e["id"]: e for e in data["evidence"]}
    used_evidence_ids = []
    for n in nodes:
        for cid in n.get("claim_ids", []):
            c = claims.get(cid, {})
            for evid in c.get("supporting_evidence_ids", []) + c.get(
                "contradicting_evidence_ids", []
            ):
                if evid in evidence and evid not in used_evidence_ids:
                    used_evidence_ids.append(evid)
    for edge in edges:
        for cid in edge.get("claim_ids", []):
            c = claims.get(cid, {})
            for evid in c.get("supporting_evidence_ids", []) + c.get(
                "contradicting_evidence_ids", []
            ):
                if evid in evidence and evid not in used_evidence_ids:
                    used_evidence_ids.append(evid)
    reference_numbers = {evid: str(i + 1) for i, evid in enumerate(used_evidence_ids)}

    def lane(n):
        for i, (_, types) in enumerate(LANES):
            if n["type"] in types:
                return i
        return len(LANES) - 1

    anchor_vals = sorted(
        {
            float(n["display_date"]["anchor_year"])
            for n in nodes
            if n["display_date"]["mode"] == "anchored"
        }
    )
    groups = {}
    for n in nodes:
        val = n["display_date"]
        yr = float(val["anchor_year"]) if val["mode"] == "anchored" else None
        groups.setdefault((yr, lane(n)), []).append(n)
    # Reserve sufficient vertical space for every same-date stack in every lane.
    group_heights = {
        yr: max(
            [
                len(group) * (NODE_H + 20)
                for (year, _), group in groups.items()
                if year == yr
            ],
            default=NODE_H + 20,
        )
        for yr in anchor_vals
    }
    axis = {}
    y = 155.0
    prev = None
    for year in anchor_vals:
        if prev is not None:
            gap = year - prev
            chronological = max(100, 80 * math.asinh(gap / 25))
            y += max(chronological, group_heights.get(prev, 0) + 22)
        axis[year] = y
        prev = year
    known_bottom = (
        (axis[prev] + group_heights.get(prev, 0)) if prev is not None else 150
    )
    unknown_y = known_bottom + 90
    lane_width = max(280, NODE_W + 110)
    lanes = [
        {"id": i, "name": name, "x": 135 + i * lane_width, "width": lane_width}
        for i, (name, _) in enumerate(LANES)
    ]
    width = 180 + len(LANES) * lane_width
    shown = []
    for (yr, li), group in groups.items():
        group = sorted(group, key=lambda n: n["id"])
        base = axis[yr] if yr is not None else unknown_y
        # Boxes may deviate from the factual anchor, and are tethered by a labelled leader.
        for j, n in enumerate(group):
            x = lanes[li]["x"] + 26
            yy = base + j * (NODE_H + 20)
            shown.append(
                {
                    "draw_id": "draw_" + n["id"],
                    "node_id": n["id"],
                    "bbox": [x, yy, NODE_W, NODE_H],
                    "anchor": [x + NODE_W / 2, base],
                    "anchor_year": yr,
                    "lane_id": li,
                    "label": n["label"],
                    "summary": n.get("summary", ""),
                    "date_label": date_text(yr),
                    "type": n["type"],
                    "color": PALETTE.get(n["type"], "#66788B"),
                    "claim_refs": n.get("claim_ids", []),
                    "assessments": sorted(
                        {
                            claims[cid]["assessment"]
                            for cid in n.get("claim_ids", [])
                            if cid in claims
                        }
                    ),
                    "source_numbers": sorted(
                        {
                            reference_numbers[evid]
                            for cid in n.get("claim_ids", [])
                            if cid in claims
                            for evid in claims[cid].get("supporting_evidence_ids", [])
                            + claims[cid].get("contradicting_evidence_ids", [])
                            if evid in reference_numbers
                        },
                        key=int,
                    ),
                    "asset_refs": [],
                    "z_index": 2,
                    "style": {
                        "fill": "#FFFFFF",
                        "stroke": PALETTE.get(n["type"], "#66788B"),
                    },
                }
            )
    positions = {n["node_id"]: n for n in shown}
    lines = []
    for i, e in enumerate(edges):
        s = positions[e["source_id"]]
        t = positions[e["target_id"]]
        sx, sy, sw, sh = s["bbox"]
        tx, ty, tw, th = t["bbox"]
        # Route from nearest vertical/horizontal boundary based on geometry.
        sc = (sx + sw / 2, sy + sh / 2)
        tc = (tx + tw / 2, ty + th / 2)
        dx = tc[0] - sc[0]
        dy = tc[1] - sc[1]
        if abs(dy) >= abs(dx) * 0.3:
            a = (sc[0], sy + sh if dy >= 0 else sy)
            b = (tc[0], ty if dy >= 0 else ty + th)
            step = max(30, abs(b[1] - a[1]) * 0.4)
            c1 = (a[0], a[1] + step * (1 if dy >= 0 else -1))
            c2 = (b[0], b[1] - step * (1 if dy >= 0 else -1))
        else:
            a = (sx + sw if dx >= 0 else sx, sc[1])
            b = (tx if dx >= 0 else tx + tw, tc[1])
            step = max(35, abs(dx) * 0.38)
            c1 = (a[0] + step * (1 if dx >= 0 else -1), a[1])
            c2 = (b[0] - step * (1 if dx >= 0 else -1), b[1])
        lines.append(
            {
                "draw_id": "draw_" + e["id"],
                "edge_id": e["id"],
                "source_node_id": e["source_id"],
                "target_node_id": e["target_id"],
                "path": [list(p) for p in (a, c1, c2, b)],
                "label": e.get("label", ""),
                "relation_type": e["relation_type"],
                "claim_refs": e.get("claim_ids", []),
                "style": {
                    "stroke": "#708398",
                    "width": 1.5,
                    "dash": "5,5" if e["relation_type"] == "hypothesis" else "",
                },
                "z_index": 1,
            }
        )
    refs = [
        {
            "number": reference_numbers[eid],
            "evidence_id": eid,
            "title": evidence[eid]["title"],
            "source_url": evidence[eid].get("source_url"),
            "locator": evidence[eid].get("locator", ""),
        }
        for eid in used_evidence_ids
    ]
    content_bottom = max([box["bbox"][1] + NODE_H for box in shown] + [unknown_y]) + 130
    height = content_bottom + max(0, len(refs)) * 26 + 60
    out = {
        "schema_version": "0.5",
        "run_id": run_id,
        "graph_generation": "active",
        "title": title,
        "canvas_width": width,
        "canvas_height": height,
        "units": "pt",
        "time_axis": {
            "monotonic": True,
            "anchors": [[yr, axis[yr]] for yr in anchor_vals],
            "unknown_y": unknown_y,
        },
        "lane_definitions": lanes,
        "nodes": shown,
        "edges": lines,
        "sources": refs,
        "sources_y": content_bottom,
        "expected_node_ids": checked["adopted_node_ids"],
        "expected_edge_ids": checked["adopted_edge_ids"],
        "layout_revision": 1,
        "styles": {"background": "#FCFBF8", "grid": "#DBE8E9"},
    }
    check_scene(out)
    return out


def check_scene(scene):
    ensure = lambda cond, msg: (
        None if cond else (_ for _ in ()).throw(ValidationError(msg))
    )
    ensure(
        len({n["node_id"] for n in scene["nodes"]}) == len(scene["nodes"]),
        "duplicate drawn node",
    )
    ensure(
        len({e["edge_id"] for e in scene["edges"]}) == len(scene["edges"]),
        "duplicate drawn edge",
    )
    ensure(
        set(n["node_id"] for n in scene["nodes"]) == set(scene["expected_node_ids"]),
        "missing drawn nodes",
    )
    ensure(
        set(e["edge_id"] for e in scene["edges"]) == set(scene["expected_edge_ids"]),
        "missing drawn edges",
    )
    for a, b in zip(scene["time_axis"]["anchors"], scene["time_axis"]["anchors"][1:]):
        ensure(a[0] < b[0] and a[1] < b[1], "chronology reversed")
    for n in scene["nodes"]:
        x, y, w, h = n["bbox"]
        ensure(
            x >= 0
            and y >= 0
            and x + w <= scene["canvas_width"]
            and y + h <= scene["canvas_height"],
            "node out of page",
        )
    return {
        "status": "pass",
        "node_count": len(scene["nodes"]),
        "edge_count": len(scene["edges"]),
        "note": "structural layout QA; semantics and pixel-level rendering require separate review",
    }


def short_lines(t, limit=25, max_lines=2):
    text = str(t or "")
    return [
        text[i : i + limit] for i in range(0, min(len(text), limit * max_lines), limit)
    ] or [""]


def svg_text(x, y, value, size=11, fill="#243747", weight="normal"):
    return f'<text x="{x:.2f}" y="{y:.2f}" fill="{fill}" font-size="{size}" font-weight="{weight}" font-family="Noto Sans CJK JP, sans-serif">{html.escape(str(value))}</text>'


def render_svg(scene):
    w = scene["canvas_width"]
    h = scene["canvas_height"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(scene["title"])}">',
        '<defs><pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0 L0 0 0 20" fill="none" stroke="#D9E6E8" stroke-width="0.65"/></pattern></defs>',
        f'<rect width="100%" height="100%" fill="{scene["styles"]["background"]}"/><rect width="100%" height="100%" fill="url(#grid)"/>',
    ]
    parts.append(svg_text(45, 58, scene["title"], 23, weight="bold"))
    parts.append(
        svg_text(
            45,
            83,
            "過去 ↑　未来 ↓　　点線：仮説　　年代の位置は各カードのアンカーが示す",
            10,
        )
    )
    for lane in scene["lane_definitions"]:
        parts.append(
            f'<rect x="{lane["x"] - 10}" y="99" width="{lane["width"] - 15}" height="{h - 115}" rx="12" fill="#FFFFFF" fill-opacity="0.23"/>'
        )
        parts.append(svg_text(lane["x"] + 16, 120, lane["name"], 12, weight="bold"))
    for year, y in scene["time_axis"]["anchors"]:
        parts.append(
            f'<line x1="96" y1="{y}" x2="{w - 35}" y2="{y}" stroke="#A7BBC4" stroke-width="0.8" stroke-dasharray="4,5"/>'
        )
        parts.append(svg_text(10, y - 4, date_text(year), 10))
    parts.append(svg_text(8, scene["time_axis"]["unknown_y"] - 8, "年代不明", 10))
    for e in scene["edges"]:
        a, c, d, b = e["path"]
        style = e["style"]
        dash = f' stroke-dasharray="{style["dash"]}"' if style.get("dash") else ""
        parts.append(
            f'<path data-edge-id="{html.escape(e["edge_id"])}" d="M{a[0]:.2f},{a[1]:.2f} C{c[0]:.2f},{c[1]:.2f} {d[0]:.2f},{d[1]:.2f} {b[0]:.2f},{b[1]:.2f}" fill="none" stroke="{style["stroke"]}" stroke-width="{style["width"]}"{dash}><title>{html.escape(e["label"] or e["relation_type"])}</title></path>'
        )
    for n in scene["nodes"]:
        x, y, ww, hh = n["bbox"]
        ay = n["anchor"][1]
        if abs(y - ay) > 1:
            parts.append(
                f'<line x1="{x + ww / 2}" y1="{ay}" x2="{x + ww / 2}" y2="{y}" stroke="{n["color"]}" stroke-dasharray="2,3"/>'
            )
        parts.append(
            f'<g data-node-id="{html.escape(n["node_id"])}"><rect x="{x}" y="{y}" width="{ww}" height="{hh}" rx="9" fill="white" stroke="{n["color"]}" stroke-width="2"/>'
        )
        parts.append(
            svg_text(
                x + 9, y + 20, short_lines(n["label"], 17, 1)[0], 13, n["color"], "bold"
            )
        )
        for j, s in enumerate(short_lines(n["summary"], 28, 2)):
            parts.append(svg_text(x + 9, y + 39 + j * 13, s, 9))
        nums = " ".join("[" + no + "]" for no in n.get("source_numbers", []))
        statuses = "/".join(STATUS_JA.get(st, st) for st in n.get("assessments", []))
        parts.append(
            svg_text(
                x + 9,
                y + hh - 7,
                (n["date_label"] + " " + nums + " " + statuses)[:41],
                9,
                "#66788B",
            )
        )
        parts.append("</g>")
    if scene.get("sources"):
        parts.append(
            svg_text(
                44,
                scene["sources_y"] + 14,
                "資料・出典（番号はカードの参照番号）",
                13,
                weight="bold",
            )
        )
        for i, ref in enumerate(scene["sources"]):
            line = f"[{ref['number']}] {ref['title']} {ref.get('locator', '')} {ref.get('source_url') or ''}"
            parts.append(svg_text(44, scene["sources_y"] + 38 + i * 26, line[:150], 9))
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


def render_pdf(scene, path):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    # PDF UserUnit is unsupported by many readers; enforce conservative one-page dimensions.
    w = scene["canvas_width"]
    h = scene["canvas_height"]
    if w > 14000 or h > 14000:
        raise ValueError("one-page PDF exceeds conservative 14000 pt canvas limit")
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
        font = "HeiseiKakuGo-W5"
    except Exception:
        font = "Helvetica"
    from reportlab.lib.colors import HexColor

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(w, h), pageCompression=1)

    def cy(y):
        return h - y

    c.setFillColor(HexColor("#FCFBF8"))
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setStrokeColor(HexColor("#DEE9E9"))
    c.setLineWidth(0.55)
    for x in range(0, math.ceil(w), 20):
        c.line(x, 0, x, h)
    for y in range(0, math.ceil(h), 20):
        c.line(0, y, w, y)

    def label(x, y, t, size=10, color="#243747"):
        c.setFillColor(HexColor(color))
        c.setFont(font, size)
        c.drawString(x, cy(y), str(t))

    label(45, 58, scene["title"], 22)
    label(45, 83, "過去 ↑　未来 ↓　点線：仮説", 10)
    for lane in scene["lane_definitions"]:
        label(lane["x"] + 16, 120, lane["name"], 12)
    for yr, y in scene["time_axis"]["anchors"]:
        c.setStrokeColor(HexColor("#A7BBC4"))
        c.setDash(4, 5)
        c.line(96, cy(y), w - 35, cy(y))
        c.setDash()
        label(10, y - 4, date_text(yr), 9)
    label(8, scene["time_axis"]["unknown_y"] - 8, "年代不明", 9)
    for e in scene["edges"]:
        a, b, d, z = e["path"]
        c.setStrokeColor(HexColor(e["style"]["stroke"]))
        c.setLineWidth(1.5)
        c.setDash(4, 4) if e["style"].get("dash") else c.setDash()
        p = c.beginPath()
        p.moveTo(a[0], cy(a[1]))
        p.curveTo(b[0], cy(b[1]), d[0], cy(d[1]), z[0], cy(z[1]))
        c.drawPath(p, stroke=1, fill=0)
        c.setDash()
    for n in scene["nodes"]:
        x, y, ww, hh = n["bbox"]
        c.setFillColorRGB(1, 1, 1)
        c.setStrokeColor(HexColor(n["color"]))
        c.setLineWidth(2)
        c.roundRect(x, cy(y + hh), ww, hh, 9, fill=1, stroke=1)
        label(x + 9, y + 20, short_lines(n["label"], 17, 1)[0], 12, n["color"])
        for j, s in enumerate(short_lines(n["summary"], 28, 2)):
            label(x + 9, y + 39 + j * 13, s, 9)
        nums = " ".join("[" + no + "]" for no in n.get("source_numbers", []))
        statuses = "/".join(STATUS_JA.get(st, st) for st in n.get("assessments", []))
        label(
            x + 9,
            y + hh - 7,
            (n["date_label"] + " " + nums + " " + statuses)[:41],
            9,
            "#66788B",
        )
        if abs(y - n["anchor"][1]) > 1:
            c.setStrokeColor(HexColor(n["color"]))
            c.setDash(2, 3)
            c.line(x + ww / 2, cy(n["anchor"][1]), x + ww / 2, cy(y))
            c.setDash()
    if scene.get("sources"):
        label(44, scene["sources_y"] + 14, "資料・出典", 13)
        for i, ref in enumerate(scene["sources"]):
            line = f"[{ref['number']}] {ref['title']} {ref.get('locator', '')} {ref.get('source_url') or ''}"
            label(44, scene["sources_y"] + 38 + i * 26, line[:150], 9)
    c.showPage()
    c.save()
    return path


def emit(scene, out_dir, force_pdf_error=False):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scene.json").write_text(
        json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    svg = render_svg(scene)
    (out_dir / "map.svg").write_text(svg, encoding="utf-8")
    (out_dir / "map.html").write_text(render_html(scene, svg), encoding="utf-8")
    output = None
    reason = None
    try:
        if force_pdf_error:
            raise ValueError("injected PDF failure")
        output = render_pdf(scene, out_dir / "map.pdf")
        if not output.exists() or output.stat().st_size < 100:
            raise ValueError("invalid PDF output")
        # Validate that the file has one page when PyMuPDF is available.
        try:
            import fitz

            with fitz.open(str(output)) as doc:
                if len(doc) != 1:
                    raise ValueError("PDF was not single page")
        except ImportError:
            pass
        primary = "pdf"
    except Exception as e:
        reason = str(e)
        primary = "svg"
        output = out_dir / "map.svg"
        if (out_dir / "map.pdf").exists():
            (out_dir / "map.pdf").unlink()
    result = {
        "primary_format": primary,
        "primary_path": str(output),
        "pdf_fallback_reason": reason,
        "scene_check": check_scene(scene),
        "svg_path": str(out_dir / "map.svg"),
        "html_path": str(out_dir / "map.html"),
        "note": "SVG structural ID checks passed; full visual, source, and semantic QA require agent review",
    }
    (out_dir / "render_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
