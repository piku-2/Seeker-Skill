"""Build and validate the common Truth Seeker drawing scene."""

import base64
import hashlib
import io
import json
import math
from collections import defaultdict
from itertools import chain, pairwise
from pathlib import Path

from PIL import Image

from .model import LayoutError, ensure, validate, validate_importance
from .placement import place_rows
from .routing import (
    NORMALS,
    bounds,
    edge_obstacles,
    first_collision,
    intersects,
    padded,
    port_point,
    route_edges,
)
from .typography import display_text, measure, missing_glyphs, text_lines

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
CATEGORY_GROUPS = (
    {"concept", "culture", "document"},
    {"person", "organization"},
    {"technology", "invention", "product", "object"},
    {"event", "place", "other"},
)
LANE_BY_TYPE = {kind: i for i, types in enumerate(CATEGORY_GROUPS) for kind in types}
# Width multiplier, title size, padding, stroke width.
LEVEL_STYLE = (
    (0.85, 12.0, 11, 1.6),
    (1.00, 12.7, 12, 1.6),
    (1.20, 14.0, 14, 2.3),
    (1.40, 15.3, 16, 3.1),
)
DEFAULT_IMPORTANCE = {
    "level": 2,
    "reason": "重要度の指定がないため",
    "source": "default",
}
NODE_W = 205
GUTTER = 34
LEGEND = (
    "過去 ↑　未来 ↓　　太い線ほど歴史的に重要\n"
    "等間隔の破線＝仮説　長短の破線＝論争中　長い破線＝有力説　点線＝未検証"
)
RELATION_STYLES = {
    "dependency": ("依存", "#24588A"),
    "influence": ("影響", "#16706B"),
    "context": ("背景", "#627382"),
    "confluence": ("合流", "#754990"),
    "parallel": ("並行", "#845C28"),
    "opposition": ("対立", "#A34845"),
    "hypothesis": ("仮説", "#708398"),
    "other": ("その他", "#756B58"),
}
EDGE_WIDTHS = (0.9, 1.5, 2.25, 3.1)


def _edge_style(relation, level, assessments):
    if "contested" in assessments:
        dash = "8,3,2,3"
    elif relation == "hypothesis" or "hypothetical" in assessments:
        dash = "5,5"
    elif "unverified" in assessments:
        dash = "2,4"
    elif "favored" in assessments:
        dash = "8,4"
    else:
        dash = ""
    return {
        "stroke": RELATION_STYLES[relation][1],
        "width": EDGE_WIDTHS[level - 1],
        "dash": dash,
    }


def _date_label(node, year):
    if year is None:
        return date_text(year)
    date = node["display_date"]
    matching = [
        d
        for d in node["dates"]
        if d.get("earliest") is not None and d["earliest"] <= year <= d["latest"]
    ]
    dates = list(
        dict.fromkeys(
            d.get("label") or f"{date_text(d['earliest'])}〜{date_text(d['latest'])}"
            for d in matching
            if d.get("label") or d["earliest"] != d["latest"]
        )
    )
    label = date_text(year)
    if dates:
        return " / ".join(dates)
    reason = str(date.get("anchor_reason", ""))
    if any(word in reason for word in ("概年", "頃", "推定", "暫定")):
        return f"{label}頃"
    return label


def date_text(year):
    if year is None:
        return "年代不明"
    value = 1 - year if year <= 0 else year
    value = int(value) if int(value) == value else value
    return f"紀元前{value}年" if year <= 0 else f"{value}年"


def _footer(node):
    nums = " ".join(f"[{no}]" for no in node["source_numbers"])
    statuses = "/".join(STATUS_JA[st] for st in node["assessments"])
    return " ".join(value for value in (node["date_label"], nums, statuses) if value)


def _source_caption(ref):
    return " ".join(
        str(value)
        for value in (
            f"[{ref['number']}]",
            ref["title"],
            ref["locator"],
            ref["source_url"],
        )
        if value
    )


def _evidence_ids(record, claims):
    for cid in record["claim_ids"]:
        claim = claims[cid]
        yield from claim["supporting_evidence_ids"]
        yield from claim["contradicting_evidence_ids"]


def _end(lines):
    return lines[-1]["bbox"][1] + lines[-1]["bbox"][3]


def _node_lines(node):
    level = node["importance"]["level"]
    multiplier, title_size, pad, _ = LEVEL_STYLE[level - 1]
    width = max(NODE_W * multiplier, node.get("figure_min_width", 0))
    lines = []
    for role, text, size, color, gap in (
        ("title", node["label"], title_size, node["color"], 0),
        ("body", node["summary"], 9.5, "#243747", 6),
        ("footer", _footer(node), 8.5, "#526474", 7),
    ):
        if display_text(text):
            lines.extend(
                text_lines(
                    text,
                    pad,
                    _end(lines) + gap if lines else pad,
                    width - 2 * pad,
                    size,
                    color,
                    role,
                )
            )
    return width, _end(lines) + pad, lines


def _arrow(end, width=1.5):
    x, y = end["point"]
    dx, dy = NORMALS[end["side"]]
    length, half = 7 + (width - 1.5), 3 + (width - 1.5) / 2
    return [
        [x, y],
        [x + length * dx - half * dy, y + length * dy + half * dx],
        [x + length * dx + half * dy, y + length * dy - half * dx],
    ]


def _saved_figure(run_dir, record):
    """Recover an embedded-only figure before the CLI replaces its old render."""
    previous = run_dir / "render"
    scene = json.loads((previous / "scene.json").read_text())
    for asset in scene.get("assets", []):
        if asset.get("record") == record and asset.get("image_data"):
            return base64.b64decode(asset["image_data"], validate=True)
    figure = scene.get("figure", {})
    if (figure.get("asset_id"), figure.get("node_id")) != (
        record["id"],
        record.get("node_id"),
    ):
        raise ValueError(f"{record['id']}: embedded figure not found")
    import pymupdf

    with pymupdf.open(previous / "map.pdf") as doc:
        page = doc[0]
        scale = page.rect.height / scene["canvas_height"]
        if len(doc) != 1 or abs(page.rect.width - scene["canvas_width"] * scale) > 0.05:
            raise ValueError("saved figure and PDF dimensions disagree")
        rect = pymupdf.Rect(*(v * scale for v in padded(figure["bbox"], 0)))
        if rect.is_empty or not page.rect.contains(rect):
            raise ValueError("invalid saved figure bounds")
        return page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(3, 3)).tobytes("png")


def _asset_image(record, run_dir):
    if record.get("asset_type") == "external_reference":
        return None
    if run_dir is None:
        raise ValueError(f"{record['id']}: run directory required for registered image")
    root = Path(run_dir).resolve()
    if record.get("local_path"):
        path = (root / record["local_path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"{record['id']}: asset outside run")
        raw = path.read_bytes()
        if record.get("sha256") and hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError(f"{record['id']}: image checksum differs")
    else:
        raw = _saved_figure(root, record)
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.format not in {"JPEG", "PNG"}:
            buffer = io.BytesIO()
            image.convert("RGBA").save(buffer, format="PNG")
            raw = buffer.getvalue()
            mime = "image/png"
        else:
            mime = Image.MIME[image.format]
        return raw, mime, image.size


def _prepare_assets(nodes, data, run_dir, evidence):
    assets = []
    for record in data.get("assets", []):
        owners = _asset_owners(record) & nodes.keys()
        if _asset_owners(record) and not owners:
            continue
        owner_ids = sorted(
            owners,
            key=lambda key: (
                key != record.get("node_id"),
                -nodes[key]["importance"]["level"],
                key,
            ),
        )
        for key in owner_ids:
            nodes[key]["asset_refs"].append(record["id"])
        primary = owner_ids[0] if owner_ids else None
        try:
            image = _asset_image(record, run_dir)
        except (OSError, ValueError, KeyError, ImportError) as exc:
            raise LayoutError("asset_image", f"{record['id']}: {exc}") from exc
        asset = {"id": record["id"], "record": record, "owner_id": primary}
        if image:
            raw, mime, size = image
            asset.update(
                image_data=base64.b64encode(raw).decode("ascii"),
                mime=mime,
                image_size=list(size),
            )
            if primary:
                node = nodes[primary]
                node["figure_min_width"] = 250 + 25 * node["importance"]["level"]
        urls = [record.get("source_page_url"), record.get("source_url")]
        urls.extend(
            evidence[k]["source_url"]
            for k in record.get("evidence_ids", [])
            if k in evidence
        )
        asset["source_urls"] = list(dict.fromkeys(url for url in urls if url))
        others = owner_ids[1:] if primary else owner_ids
        credit = [
            record.get("caption") or record.get("title"),
            "・".join(nodes[key]["label"] for key in others),
            record.get("creator"),
            record.get("license"),
            record.get("rights_note"),
        ]
        asset["caption"] = "　".join(filter(None, credit)) or record["id"]
        assets.append(asset)
    return assets


def _asset_owners(record):
    return (set(record.get("related_node_ids", [])) | {record.get("node_id")}) - {None}


def _layout_asset(asset, x, cursor, width, max_height):
    top = cursor
    if "image_size" in asset:
        iw, ih = asset["image_size"]
        factor = min(width / iw, max_height / ih)
        asset["bbox"] = [
            x + (width - iw * factor) / 2,
            cursor,
            iw * factor,
            ih * factor,
        ]
        cursor += ih * factor + 8
    asset["text_lines"] = text_lines(
        asset["caption"], x, cursor, width, 9, "#243747", "asset_caption"
    )
    for i, url in enumerate(asset["source_urls"], 1):
        label = f"図版出典 {i}（リンク）"
        lines = text_lines(
            label, x, _end(asset["text_lines"]) + 4, width, 9, "#325d92", "asset_source"
        )
        for line in lines:
            line["source_url"] = url
        asset["text_lines"].extend(lines)
    bottom = _end(asset["text_lines"])
    asset["reserved_bbox"] = [x, top, width, bottom - top]
    return bottom


def _offset_text(lines, x, y):
    for line in lines:
        line["x"] += x
        line["y"] += y
        line["bbox"][0] += x
        line["bbox"][1] += y


def _move_asset(asset, x, y):
    for field in ("reserved_bbox", "bbox"):
        if field in asset:
            asset[field][0] += x
            asset[field][1] += y
    _offset_text(asset["text_lines"], x, y)


def layout(
    data, run_id="run_manual", title="歴史知識地図", importance=None, *, run_dir=None
):
    checked = validate(data)
    nodes = [n for n in data.get("nodes", []) if n["adoption_status"] == "adopted"]
    edges = [e for e in data.get("edges", []) if e["adoption_status"] == "adopted"]
    if importance is None:
        importance = {}
    ensure(
        isinstance(importance, dict),
        "layout importance must map node or edge IDs to records",
    )
    ensure(
        set(importance)
        <= set(checked["adopted_node_ids"] + checked["adopted_edge_ids"]),
        "layout importance references a nonadopted node or edge",
    )
    for node_id, entry in importance.items():
        validate_importance(entry, node_id)

    claims = {c["id"]: c for c in data.get("claims", [])}
    evidence = {e["id"]: e for e in data.get("evidence", [])}

    used_evidence = dict.fromkeys(
        eid for record in chain(nodes, edges) for eid in _evidence_ids(record, claims)
    )
    reference_numbers = {eid: str(i) for i, eid in enumerate(used_evidence, 1)}
    refs = [
        {
            "number": number,
            "evidence_id": eid,
            "title": evidence[eid]["title"],
            "source_url": evidence[eid].get("source_url"),
            "locator": evidence[eid].get("locator", ""),
        }
        for eid, number in reference_numbers.items()
    ]

    positions = {}
    preferred_widths = [NODE_W] * len(CATEGORY_GROUPS)
    edge_weights = {
        e["id"]: importance.get(e["id"], e.get("importance", DEFAULT_IMPORTANCE)).copy()
        for e in edges
    }
    for n in nodes:
        date = n["display_date"]
        year = date.get("anchor_year") if date["mode"] == "anchored" else None
        lane = LANE_BY_TYPE[n["type"]]
        weight = importance.get(n["id"], n.get("importance", DEFAULT_IMPORTANCE)).copy()
        color = PALETTE[n["type"]]
        node = {
            "draw_id": "draw_" + n["id"],
            "node_id": n["id"],
            "category_id": lane,
            "anchor_year": year,
            "label": n["label"],
            "summary": n["summary"],
            "date_label": _date_label(n, year),
            "display_date": n["display_date"],
            "dates": n["dates"],
            "type": n["type"],
            "color": color,
            "claim_refs": n["claim_ids"],
            "importance": weight,
            "assessments": sorted(
                {claims[cid]["assessment"] for cid in n["claim_ids"]}
            ),
            "source_numbers": sorted(
                {reference_numbers[eid] for eid in _evidence_ids(n, claims)}, key=int
            ),
            "asset_refs": [],
            "z_index": 2,
            "style": {
                "fill": "#FFFFFF",
                "stroke": color,
                "width": LEVEL_STYLE[weight["level"] - 1][3],
            },
        }
        positions[n["id"]] = node

    assets = _prepare_assets(positions, data, run_dir, evidence)
    by_owner = defaultdict(list)
    for asset in assets:
        by_owner[asset["owner_id"]].append(asset)
    groups = defaultdict(list)
    for node in positions.values():
        w, h, lines = _node_lines(node)
        pad = LEVEL_STYLE[node["importance"]["level"] - 1][2]
        for asset in by_owner[node["node_id"]]:
            h = (
                _layout_asset(
                    asset,
                    pad,
                    h + 4,
                    w - 2 * pad,
                    240 + 10 * (node["importance"]["level"] - 2),
                )
                + pad
            )
        node.update(bbox=[0, 0, w, h], text_lines=lines)
        groups[node["anchor_year"]].append(node)
        category = node["category_id"]
        preferred_widths[category] = max(preferred_widths[category], w)

    years = sorted(year for year in groups if year is not None)
    bands = place_rows(
        {year: groups[year] for year in [*years, None]},
        edges,
        len(CATEGORY_GROUPS),
        preferred_widths,
        edge_weights,
    )
    # Continuous gutters leave the router a clear vertical detour in every track.
    lane_widths = [w + 2 * GUTTER + (8 if w > NODE_W else 0) for w in preferred_widths]
    for rows in bands.values():
        for row in rows:
            for col, node in row:
                lane_widths[col] = max(lane_widths[col], node["bbox"][2] + 2 * GUTTER)
    axis_labels = [date_text(year) for year in [*years, None]]
    lane_x = max(135, max(measure(label, 10)[2] for label in axis_labels) + 40)
    lanes = []
    for i, lane_width in enumerate(lane_widths):
        lanes.append({"id": i, "x": lane_x, "width": lane_width})
        lane_x += lane_width
    width = lane_x + 42
    title_lines = text_lines(title, 44, 34, width - 88, 23, "#243747", "map_title")
    legend = text_lines(
        LEGEND, 44, _end(title_lines) + 9, width - 88, 10, "#243747", "legend"
    )
    # Color keys are shared text objects so they remain selectable in PDF.
    key_lines = []
    for items in (
        [(label, color) for label, color in RELATION_STYLES.values()],
        [
            (label, PALETTE[kind])
            for kind, label in (
                ("concept", "概念"),
                ("technology", "技術"),
                ("invention", "発明・製品・物品"),
                ("person", "人物・文化"),
                ("organization", "組織"),
                ("event", "出来事"),
                ("document", "資料"),
                ("place", "場所"),
                ("other", "他"),
            )
        ],
    ):
        top = _end(key_lines or legend) + 6
        x = 45
        for label, color in items:
            line = text_lines(
                "■ " + label, x, top, width - x - 30, 10, color, "color_key"
            )[0]
            key_lines.append(line)
            x += line["bbox"][2] + 17
    track_top = _end(key_lines) + 14
    markers, shown, obstacles, anchors = [], [], {}, []
    y = max(track_top + 22, 155)
    for i, year in enumerate([*years, None]):
        if year is not None:
            anchors.append([year, y])
        label = text_lines(
            date_text(year), 9, y - 19, lanes[0]["x"] - 24, 10, "#526474", "axis_label"
        )[0]
        markers.append(label)
        obstacles[f"axis:{year}"] = label["bbox"]
        cursor = y
        for row in bands[year]:
            row_height = max(node["bbox"][3] for _, node in row)
            for col, node in row:
                lane = lanes[col]
                _, _, w, h = node["bbox"]
                x = lane["x"] + (lane["width"] - w) / 2
                # Stagger shorter cards slightly within a row, keeping every
                # rectangle strictly inside the same chronological band.
                top = cursor + min(14, (row_height - h) / 2)
                node.update(lane_id=col, bbox=[x, top, w, h], anchor=[x + w / 2, y])
                if top > y:
                    guide_x = lane["x"] + 25
                    node["anchor_leader"] = [
                        [guide_x, y],
                        [guide_x, top + h / 2],
                        [x, top + h / 2],
                    ]
                _offset_text(node["text_lines"], x, top)
                for asset in by_owner[node["node_id"]]:
                    _move_asset(asset, x, top)
                obstacles[node["node_id"]] = node["bbox"]
                shown.append(node)
            cursor += row_height + 32
        band_bottom = cursor
        if year is not None:
            next_year = years[i + 1] if i + 1 < len(years) else None
            chronological = (
                max(78, 80 * math.asinh((next_year - year) / 25))
                if next_year is not None
                else 78
            )
            y = max(y + chronological, band_bottom + 3)

    columns = [
        lane["x"] + offset
        for lane in lanes
        for offset in (10, 19, lane["width"] - 19, lane["width"] - 10)
    ]
    drawn_edges = []
    ranked = sorted(edges, key=lambda e: (-edge_weights[e["id"]]["level"], e["id"]))
    for edge, start, end, path in route_edges(ranked, positions, obstacles, columns):
        weight = edge_weights[edge["id"]]
        assessments = sorted({claims[cid]["assessment"] for cid in edge["claim_ids"]})
        style = _edge_style(edge["relation_type"], weight["level"], assessments)
        drawn_edges.append(
            {
                "draw_id": "draw_" + edge["id"],
                "edge_id": edge["id"],
                "source_node_id": edge["source_id"],
                "target_node_id": edge["target_id"],
                "source_port": start,
                "target_port": end,
                "path": [list(point) for point in path],
                "arrow": _arrow(end, style["width"]),
                "label": edge.get("label", ""),
                "relation_type": edge["relation_type"],
                "importance": weight,
                "assessments": assessments,
                "claim_refs": edge["claim_ids"],
                "z_index": 1,
                "style": style,
            }
        )
    # Draw the strongest relations above lighter connectors at crossings.
    drawn_edges.sort(key=lambda e: e["importance"]["level"])

    sources_y = (
        max(
            band_bottom,
            y,
            max((bounds(edge["path"])[3] for edge in drawn_edges), default=0),
        )
        + 55
    )
    sources_heading = []
    cursor = sources_y
    if refs:
        sources_heading = text_lines(
            "資料・出典（番号はカードの参照番号）",
            44,
            cursor,
            width - 88,
            13,
            "#243747",
            "sources_title",
        )
        cursor = _end(sources_heading) + 13
    for ref in refs:
        ref["text_lines"] = text_lines(
            _source_caption(ref), 44, cursor, width - 88, 9, "#243747", "source"
        )
        cursor = _end(ref["text_lines"]) + 8

    gallery_top = cursor + 34
    gallery_cursor = gallery_top
    for asset in by_owner[None]:
        gallery_cursor = (
            _layout_asset(asset, 44, gallery_cursor, min(width - 88, 560), 260) + 22
        )

    out = {
        "schema_version": "0.5",
        "run_id": run_id,
        "graph_generation": "active",
        "title": title,
        "canvas_width": width,
        "canvas_height": max(cursor, gallery_cursor if by_owner[None] else cursor) + 45,
        "units": "pt",
        "time_axis": {
            "monotonic": True,
            "anchors": anchors,
            "unknown_y": y,
            "labels": markers,
        },
        "lane_definitions": lanes,
        "lane_top": track_top,
        "header_lines": title_lines + legend + key_lines,
        "nodes": shown,
        "edges": drawn_edges,
        "sources": refs,
        "sources_y": sources_y,
        "sources_heading": sources_heading,
        "expected_node_ids": checked["adopted_node_ids"],
        "expected_edge_ids": checked["adopted_edge_ids"],
        "edge_endpoints": {e["id"]: [e["source_id"], e["target_id"]] for e in edges},
        "layout_revision": 3,
        "asset_layout_version": 2,
        "assets": assets,
        "gallery_y": gallery_top,
        "importance_overrides": importance,
        "styles": {"background": "#FCFBF8", "grid": "#DBE8E9"},
    }
    check_scene(out, data)
    return out


def all_text_lines(scene):
    return chain(
        scene["header_lines"],
        scene["time_axis"]["labels"],
        *(node["text_lines"] for node in scene["nodes"]),
        scene["sources_heading"],
        *(ref["text_lines"] for ref in scene["sources"]),
        *(asset.get("text_lines", []) for asset in scene.get("assets", [])),
    )


def _require(condition, kind, message, **details):
    if not condition:
        raise LayoutError(kind, message, **details)


def _inside(inner, outer, epsilon=1e-6):
    return (
        inner[0] >= outer[0] - epsilon
        and inner[1] >= outer[1] - epsilon
        and inner[2] <= outer[2] + epsilon
        and inner[3] <= outer[3] + epsilon
    )


def _same_port(a, b):
    return len(a) == len(b) == 2 and math.dist(a, b) < 1e-6


def _check_graph_content(scene, graph_nodes, graph_edges, data):
    flexible = scene["layout_revision"] == 3
    overrides = scene.get("importance_overrides", {})
    if flexible:
        _require(
            set(overrides) <= graph_nodes.keys() | graph_edges.keys(),
            "importance",
            "unknown importance override",
        )
    claims = {c["id"]: c for c in data.get("claims", [])}
    evidence = {e["id"]: e for e in data.get("evidence", [])}
    used = {
        eid
        for record in chain(graph_nodes.values(), graph_edges.values())
        for eid in _evidence_ids(record, claims)
    }
    refs = {ref["evidence_id"]: ref for ref in scene["sources"]}
    _require(
        set(refs) == used and len(refs) == len(scene["sources"]),
        "source_ids",
        "drawn sources differ from graph",
    )
    for eid, ref in refs.items():
        _require(
            all(
                ref[field] == evidence[eid].get(field)
                for field in ("title", "locator", "source_url")
            ),
            "source_text",
            f"{eid}: source differs from graph",
            evidence_id=eid,
        )
    for n in scene["nodes"]:
        key = n["node_id"]
        record = graph_nodes[key]
        year = record["display_date"]
        _require(
            n["anchor_year"]
            == (year["anchor_year"] if year["mode"] == "anchored" else None),
            "chronology",
            f"{key}: anchor year differs from graph",
            node_id=key,
        )
        if flexible:
            _require(
                n["display_date"] == year
                and n["dates"] == record["dates"]
                and n["importance"]
                == overrides.get(key, record.get("importance", DEFAULT_IMPORTANCE)),
                "node_content",
                f"{key}: layout metadata differs from graph",
                node_id=key,
            )
        nums = sorted(
            {refs[eid]["number"] for eid in _evidence_ids(record, claims)}, key=int
        )
        _require(
            all(n[field] == record[field] for field in ("label", "summary", "type"))
            and n["claim_refs"] == record["claim_ids"]
            and n["source_numbers"] == nums
            and n["assessments"]
            == sorted({claims[cid]["assessment"] for cid in record["claim_ids"]}),
            "text_content",
            f"{key}: text or references differ from graph",
            node_id=key,
        )
    for edge in scene["edges"]:
        key = edge["edge_id"]
        record = graph_edges[key]
        if flexible:
            _require(
                edge["assessments"]
                == sorted({claims[cid]["assessment"] for cid in record["claim_ids"]})
                and edge["importance"]
                == overrides.get(key, record.get("importance", DEFAULT_IMPORTANCE)),
                "edge_content",
                f"{key}: assessment or importance differs from graph",
                edge_id=key,
            )
        _require(
            all(edge[field] == record[field] for field in ("label", "relation_type"))
            and edge["claim_refs"] == record["claim_ids"],
            "edge_content",
            f"{key}: relation differs from graph",
            edge_id=key,
        )


def _check_inline_assets(scene, nodes, data, canvas, inline):
    assets = scene["assets"]
    by_id = {asset["id"]: asset["record"] for asset in assets}
    _require(len(assets) == len(by_id), "asset_ids", "duplicate illustration IDs")
    if data is not None:
        registered = {
            record["id"]: record
            for record in data.get("assets", [])
            if not _asset_owners(record) or _asset_owners(record) & nodes.keys()
        }
        _require(
            by_id == registered, "asset_ids", "registered assets differ from scene"
        )
    for owner, items in inline.items():
        if not items:
            continue
        _require(
            owner is None or owner in nodes, "asset_owner", "unknown illustration owner"
        )
        node = nodes.get(owner)
        limit = (
            padded(node["bbox"], -LEVEL_STYLE[node["importance"]["level"] - 1][2])
            if node
            else canvas
        )
        cursor = (
            _end(node["text_lines"])
            if node
            else max(
                [scene["sources_y"], scene["gallery_y"]]
                + [_end(ref["text_lines"]) for ref in scene["sources"]]
            )
        )
        for asset in items:
            record = asset["record"]
            key = asset["id"]
            owners = _asset_owners(record) & nodes.keys()
            _require(
                ({owner} <= owners if owners else owner is None)
                and {k for k, n in nodes.items() if key in n["asset_refs"]} == owners,
                "asset_owner",
                f"{key}: incorrect asset owner",
                asset_id=key,
            )
            area = asset["reserved_bbox"]
            _require(
                min(area[2:]) > 0
                and area[1] >= cursor
                and _inside(padded(area, 0), limit),
                "asset_bounds",
                f"{key}: illustration outside reserved area",
                asset_id=key,
            )
            text = asset["text_lines"]
            boxes = []
            if record.get("asset_type") != "external_reference":
                _require(
                    bool(asset.get("image_data")) and "bbox" in asset,
                    "asset_image",
                    f"{key}: missing registered illustration",
                    asset_id=key,
                )
                boxes.append(asset["bbox"])
            boxes.extend(line["bbox"] for line in text)
            bottom = area[1]
            for box in boxes:
                _require(
                    box[1] >= bottom - 1e-6
                    and min(box[2:]) >= 0
                    and _inside(padded(box, 0), padded(area, 0)),
                    "asset_bounds",
                    f"{key}: illustration or caption outside reserved area",
                    asset_id=key,
                )
                bottom = box[1] + box[3]
            caption = "".join(
                line["text"] + ("\n" if line["break_after"] else "")
                for line in text
                if line["role"] == "asset_caption"
            )
            urls = list(
                dict.fromkeys(
                    line["source_url"]
                    for line in text
                    if line["role"] == "asset_source"
                )
            )
            _require(
                caption == display_text(asset["caption"])
                and urls == asset["source_urls"],
                "asset_text",
                f"{key}: caption or credits missing",
                asset_id=key,
            )
            cursor = area[1] + area[3]


def check_scene(scene, data=None):
    flexible = scene.get("layout_revision") == 3
    _require(
        scene.get("layout_revision") in (2, 3),
        "layout_revision",
        "unsupported scene layout revision",
    )
    nodes = {n["node_id"]: n for n in scene["nodes"]}
    edges = {e["edge_id"]: e for e in scene["edges"]}
    expected_nodes, expected_edges = (
        scene["expected_node_ids"],
        scene["expected_edge_ids"],
    )
    endpoints = scene["edge_endpoints"]
    if data is not None:
        graph_nodes = {
            n["id"]: n
            for n in data.get("nodes", [])
            if n["adoption_status"] == "adopted"
        }
        graph_edges = {
            e["id"]: e
            for e in data.get("edges", [])
            if e["adoption_status"] == "adopted"
        }
        expected_nodes, expected_edges = graph_nodes, graph_edges
        endpoints = {
            eid: [e["source_id"], e["target_id"]] for eid, e in graph_edges.items()
        }
    for name, actual, expected in (
        ("node", nodes, expected_nodes),
        ("edge", edges, expected_edges),
    ):
        _require(
            set(actual) == set(expected) and len(actual) == len(scene[name + "s"]),
            f"{name}_ids",
            f"invalid drawn {name} IDs",
            missing=sorted(set(expected) - set(actual)),
            extra=sorted(set(actual) - set(expected)),
        )
    if data is not None:
        _check_graph_content(scene, graph_nodes, graph_edges, data)

    canvas = (0, 0, scene["canvas_width"], scene["canvas_height"])
    _require(
        all(math.isfinite(n) for n in canvas) and min(canvas[2:]) > 0,
        "canvas",
        "invalid canvas dimensions",
    )
    axis = scene["time_axis"]
    anchors = dict(axis["anchors"])
    _require(
        all(a[0] < b[0] and a[1] < b[1] for a, b in pairwise(axis["anchors"])),
        "chronology",
        "chronology reversed",
    )
    _require(
        set(anchors)
        == {n["anchor_year"] for n in nodes.values() if n["anchor_year"] is not None}
        and [line["text"] for line in axis["labels"]]
        == [date_text(year) for year in [*anchors, None]],
        "chronology",
        "axis labels and node dates do not match",
    )
    ordered = sorted(anchors.values()) + [axis["unknown_y"]]
    _require(
        all(a < b for a, b in pairwise(ordered)),
        "chronology",
        "invalid unknown band",
    )
    band_limits = dict(zip(ordered, ordered[1:] + [scene["sources_y"]]))
    obstacles = {
        f"axis:{year}": line["bbox"]
        for year, line in zip([*anchors, None], axis["labels"])
    }
    inline = defaultdict(list)
    if flexible:
        _require(
            scene.get("asset_layout_version") == 2,
            "asset_layout",
            "missing illustration layout",
        )
        for asset in scene["assets"]:
            inline[asset["owner_id"]].append(asset)
    active = []
    lanes = {lane["id"]: lane for lane in scene["lane_definitions"]}
    for n in sorted(nodes.values(), key=lambda node: node["bbox"][1]):
        key = n["node_id"]
        x, y, w, h = n["bbox"]
        box = padded(n["bbox"], 0)
        _require(
            all(math.isfinite(v) for v in box)
            and min(w, h) > 0
            and _inside(box, canvas),
            "node_bounds",
            f"{key}: invalid node rectangle",
            node_id=key,
        )
        validate_importance(n["importance"], key)
        expected_w, expected_h, expected_lines = _node_lines(n)
        if inline[key]:
            pad = LEVEL_STYLE[n["importance"]["level"] - 1][2]
            expected_h = (
                inline[key][-1]["reserved_bbox"][1]
                + inline[key][-1]["reserved_bbox"][3]
                - y
                + pad
            )
        _require(
            abs(w - expected_w) < 1e-6
            and abs(h - expected_h) < 1e-6
            and n["style"]["width"] == LEVEL_STYLE[n["importance"]["level"] - 1][3]
            and n["color"] == PALETTE[n["type"]] == n["style"]["stroke"],
            "node_style",
            f"{key}: invalid category or importance styling",
            node_id=key,
        )
        lane = lanes.get(n["lane_id"])
        _require(
            lane is not None
            and (n.get("category_id", n["lane_id"]) == LANE_BY_TYPE[n["type"]])
            and lane["x"] <= x
            and x + w <= lane["x"] + lane["width"],
            "node_lane",
            f"{key}: node outside its track",
            node_id=key,
        )
        year = n["anchor_year"]
        anchor = axis["unknown_y"] if year is None else anchors.get(year)
        _require(
            anchor is not None
            and _same_port(n["anchor"], (x + w / 2, anchor))
            and anchor <= y
            and y + h <= band_limits[anchor]
            and n["date_label"]
            == (_date_label(n, year) if flexible else date_text(year)),
            "chronology",
            f"{key}: node outside its time band",
            node_id=key,
        )
        active = [(other, rect) for other, rect in active if rect[3] >= y]
        for other, rect in active:
            _require(
                not intersects(box, rect),
                "node_overlap",
                f"{key} overlaps {other}",
                node_id=key,
                other_node_id=other,
            )
        active.append((key, box))
        prev_bottom = y
        lines = n["text_lines"]
        _require(
            len(lines) == len(expected_lines),
            "text_content",
            f"{key}: missing lines",
            node_id=key,
        )
        for line, expected in zip(lines, expected_lines):
            _, ly, _, lh = line["bbox"]
            expected_box = expected["bbox"][:]
            expected_box[0] += x
            expected_box[1] += y
            _require(
                all(
                    line[k] == expected[k]
                    for k in ("text", "role", "size", "fill", "break_after")
                ),
                "text_content",
                f"{key}: text or typography changed",
                node_id=key,
                role=line["role"],
            )
            _require(
                ly >= prev_bottom - 1e-6
                and _inside(padded(line["bbox"], 0), box)
                and all(abs(a - b) < 1e-6 for a, b in zip(line["bbox"], expected_box))
                and _same_port(
                    (line["x"], line["y"]), (x + expected["x"], y + expected["y"])
                ),
                "text_bounds",
                f"{key}: text overflow or overlap",
                node_id=key,
                role=line["role"],
            )
            prev_bottom = ly + lh
        obstacles[key] = n["bbox"]

    for eid, edge in edges.items():
        source, target = edge["source_node_id"], edge["target_node_id"]
        _require(
            [source, target] == endpoints.get(eid)
            and source in nodes
            and target in nodes,
            "edge_endpoints",
            f"{eid}: incorrect endpoints",
            edge_id=eid,
        )
        path = edge["path"]
        _require(
            len(path) >= 4
            and (len(path) - 1) % 3 == 0
            and all(len(p) == 2 and all(math.isfinite(v) for v in p) for p in path)
            and _inside(bounds(path), canvas),
            "edge_path",
            f"{eid}: invalid cubic path",
            edge_id=eid,
        )
        for role, node, point in (
            ("source", source, path[0]),
            ("target", target, path[-1]),
        ):
            port = edge[f"{role}_port"]
            _require(
                port["side"] in NORMALS
                and 0 <= port["fraction"] <= 1
                and _same_port(
                    port["point"],
                    port_point(nodes[node]["bbox"], port["side"], port["fraction"]),
                )
                and _same_port(point, port["point"]),
                "edge_port",
                f"{eid}: incorrect {role} port",
                edge_id=eid,
                node_id=node,
            )
        if flexible:
            validate_importance(edge["importance"], eid)
            expected_style = _edge_style(
                edge["relation_type"], edge["importance"]["level"], edge["assessments"]
            )
        else:
            expected_style = {
                "stroke": "#708398",
                "width": 1.5,
                "dash": "5,5" if edge["relation_type"] == "hypothesis" else "",
            }
        _require(
            edge["arrow"] == _arrow(edge["target_port"], expected_style["width"])
            and edge["style"] == expected_style,
            "edge_style",
            f"{eid}: incorrect direction marker or relation styling",
            edge_id=eid,
        )
        blocker = first_collision(path, edge_obstacles(obstacles, source, target))
        _require(
            blocker is None,
            "edge_node_collision",
            f"{eid} intersects {blocker}",
            edge_id=eid,
            node_id=blocker,
        )

    refs = scene["sources"]
    _require(
        [r["number"] for r in refs] == [str(i) for i in range(1, len(refs) + 1)],
        "source_numbers",
        "source numbers must be unique and consecutive",
    )
    for ref in refs:
        caption = "".join(
            line["text"] + ("\n" if line["break_after"] else "")
            for line in ref["text_lines"]
        )
        _require(
            caption == display_text(_source_caption(ref)),
            "source_text",
            f"{ref['evidence_id']}: truncated source",
            evidence_id=ref["evidence_id"],
        )
    if flexible:
        _check_inline_assets(scene, nodes, data, canvas, inline)
    if scene.get("asset_layout_version") == 1:
        assets = scene["assets"]
        if data is not None:
            registered = {}
            for record in data.get("assets", []):
                owners = set(record.get("related_node_ids", []))
                if record.get("node_id"):
                    owners.add(record["node_id"])
                if not owners or owners.intersection(nodes):
                    registered[record["id"]] = record
            _require(
                {a["id"]: a["record"] for a in assets} == registered
                and len(assets) == len(registered),
                "asset_ids",
                "registered assets differ from scene",
            )
        bottom = max(
            [scene["sources_y"]]
            + [
                line["bbox"][1] + line["bbox"][3]
                for ref in refs
                for line in ref["text_lines"]
            ]
            + [line["bbox"][1] + line["bbox"][3] for line in scene["sources_heading"]]
        )
        for asset in assets:
            record = asset["record"]
            owners = set(record.get("related_node_ids", [])) | {record.get("node_id")}
            _require(
                {k for k, n in nodes.items() if asset["id"] in n["asset_refs"]}
                == owners.intersection(nodes),
                "asset_owner",
                "incorrect asset owner",
            )
            boxes = []
            if record.get("asset_type") != "external_reference":
                _require(
                    bool(asset.get("image_data")) and "bbox" in asset,
                    "asset_image",
                    "missing registered illustration",
                )
                boxes.append(asset["bbox"])
            boxes.extend(line["bbox"] for line in asset["text_lines"])
            for box in boxes:
                _require(
                    box[1] >= bottom
                    and min(box[2:]) > 0
                    and _inside(padded(box, 0), canvas),
                    "asset_bounds",
                    "illustration or caption outside reserved area",
                )
                bottom = box[1] + box[3]

    glyphs = set()
    for line in all_text_lines(scene):
        _require(
            _inside(padded(line["bbox"], 0), canvas),
            "text_bounds",
            f"text outside canvas: {line['role']}",
            role=line["role"],
        )
        glyphs.update(missing_glyphs(line["text"]))
    _require(
        not glyphs,
        "font_coverage",
        f"missing font glyphs: {''.join(sorted(glyphs))}",
        glyphs=sorted(glyphs),
    )
    return {"status": "pass", "node_count": len(nodes), "edge_count": len(edges)}
