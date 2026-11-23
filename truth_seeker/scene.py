"""Build and validate the common Truth Seeker drawing scene."""

import math
from collections import defaultdict
from itertools import chain, pairwise

from .model import LayoutError, ensure, validate, validate_importance
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
LANES = [
    ("Ideas", {"concept", "culture", "document"}),
    ("People", {"person", "organization"}),
    ("Technology", {"technology", "invention", "product", "object"}),
    ("Events", {"event", "place", "other"}),
]
LANE_BY_TYPE = {kind: i for i, (_, types) in enumerate(LANES) for kind in types}
# Width multiplier, title size, padding, stroke width, space after the card.
LEVEL_STYLE = (
    (0.85, 12.0, 11, 1.6, 34),
    (1.00, 12.7, 12, 1.6, 34),
    (1.20, 14.0, 14, 2.3, 40),
    (1.40, 15.3, 16, 3.1, 46),
)
DEFAULT_IMPORTANCE = {
    "level": 2,
    "reason": "重要度の指定がないため",
    "source": "default",
}
NODE_W = 205
GUTTER = 34
LEGEND = "過去 ↑　未来 ↓　　点線：仮説　　年代は各カードのアンカーが示す"


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
    multiplier, title_size, pad, _, _ = LEVEL_STYLE[level - 1]
    width = NODE_W * multiplier
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


def _arrow(end):
    x, y = end["point"]
    dx, dy = NORMALS[end["side"]]
    return [
        [x, y],
        [x + 7 * dx - 3 * dy, y + 7 * dy + 3 * dx],
        [x + 7 * dx + 3 * dy, y + 7 * dy - 3 * dx],
    ]


def layout(data, run_id="run_manual", title="歴史知識地図", importance=None):
    checked = validate(data)
    nodes = [n for n in data.get("nodes", []) if n["adoption_status"] == "adopted"]
    edges = [e for e in data.get("edges", []) if e["adoption_status"] == "adopted"]
    if importance is None:
        importance = {}
    ensure(
        isinstance(importance, dict), "layout importance must map node IDs to records"
    )
    ensure(
        set(importance) <= set(checked["adopted_node_ids"]),
        "layout importance references a nonadopted node",
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

    groups = defaultdict(list)
    lane_widths = [NODE_W + 2 * GUTTER] * len(LANES)
    for n in nodes:
        date = n["display_date"]
        year = date.get("anchor_year") if date["mode"] == "anchored" else None
        lane = LANE_BY_TYPE[n["type"]]
        weight = importance.get(n["id"], n.get("importance", DEFAULT_IMPORTANCE)).copy()
        color = PALETTE[n["type"]]
        node = {
            "draw_id": "draw_" + n["id"],
            "node_id": n["id"],
            "lane_id": lane,
            "anchor_year": year,
            "label": n["label"],
            "summary": n["summary"],
            "date_label": date_text(year),
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
        w, h, lines = _node_lines(node)
        node.update(bbox=[0, 0, w, h], text_lines=lines)
        groups[year, lane].append(node)
        lane_widths[lane] = max(lane_widths[lane], w + 2 * GUTTER)

    years = sorted({year for year, _ in groups if year is not None})
    axis_labels = [date_text(year) for year in [*years, None]]
    lane_x = max(135, max(measure(label, 10)[2] for label in axis_labels) + 40)
    lanes = []
    for i, ((name, _), lane_width) in enumerate(zip(LANES, lane_widths)):
        lanes.append({"id": i, "name": name, "x": lane_x, "width": lane_width})
        lane_x += lane_width
    width = lane_x + 42
    title_lines = text_lines(title, 44, 34, width - 88, 23, "#243747", "map_title")
    legend = text_lines(
        LEGEND, 44, _end(title_lines) + 9, width - 88, 10, "#243747", "legend"
    )
    track_top = _end(legend) + 15
    lane_headers = [
        text_lines(
            lane["name"],
            lane["x"] + 16,
            track_top + 8,
            lane["width"] - 32,
            12,
            "#243747",
            "lane_title",
        )[0]
        for lane in lanes
    ]

    markers, shown, obstacles, anchors = [], [], {}, []
    y = max(_end(lane_headers) + 30, 155)
    for i, year in enumerate([*years, None]):
        if year is not None:
            anchors.append([year, y])
        label = text_lines(
            date_text(year), 9, y - 19, lanes[0]["x"] - 24, 10, "#526474", "axis_label"
        )[0]
        markers.append(label)
        obstacles[f"axis:{year}"] = label["bbox"]
        band_bottom = y
        for lane in lanes:
            cursor = y
            group = groups.get((year, lane["id"]), ())
            for node in sorted(group, key=lambda n: n["node_id"]):
                _, _, w, h = node["bbox"]
                x = lane["x"] + (lane["width"] - w) / 2
                node["bbox"] = [x, cursor, w, h]
                node["anchor"] = [x + w / 2, y]
                if cursor > y:
                    guide_x = lane["x"] + 25
                    node["anchor_leader"] = [
                        [guide_x, y],
                        [guide_x, cursor + h / 2],
                        [x, cursor + h / 2],
                    ]
                for line in node["text_lines"]:
                    line["x"] += x
                    line["y"] += cursor
                    line["bbox"][0] += x
                    line["bbox"][1] += cursor
                obstacles[node["node_id"]] = node["bbox"]
                shown.append(node)
                cursor += h + LEVEL_STYLE[node["importance"]["level"] - 1][4]
            band_bottom = max(band_bottom, cursor)
        # Each band's measured bottom is reserved before placing a later year.
        if year is not None:
            next_year = years[i + 1] if i + 1 < len(years) else None
            chronological = (
                max(100, 80 * math.asinh((next_year - year) / 25))
                if next_year is not None
                else 90
            )
            y = max(y + chronological, band_bottom + 12)

    positions = {node["node_id"]: node for node in shown}
    columns = [
        lane["x"] + offset
        for lane in lanes
        for offset in (10, 19, lane["width"] - 19, lane["width"] - 10)
    ]
    drawn_edges = [
        {
            "draw_id": "draw_" + edge["id"],
            "edge_id": edge["id"],
            "source_node_id": edge["source_id"],
            "target_node_id": edge["target_id"],
            "source_port": start,
            "target_port": end,
            "path": [list(point) for point in path],
            "arrow": _arrow(end),
            "label": edge.get("label", ""),
            "relation_type": edge["relation_type"],
            "claim_refs": edge["claim_ids"],
            "z_index": 1,
            "style": {
                "stroke": "#708398",
                "width": 1.5,
                "dash": "5,5" if edge["relation_type"] == "hypothesis" else "",
            },
        }
        for edge, start, end, path in route_edges(edges, positions, obstacles, columns)
    ]

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

    out = {
        "schema_version": "0.5",
        "run_id": run_id,
        "graph_generation": "active",
        "title": title,
        "canvas_width": width,
        "canvas_height": cursor + 45,
        "units": "pt",
        "time_axis": {
            "monotonic": True,
            "anchors": anchors,
            "unknown_y": y,
            "labels": markers,
        },
        "lane_definitions": lanes,
        "lane_top": track_top,
        "header_lines": title_lines + legend + lane_headers,
        "nodes": shown,
        "edges": drawn_edges,
        "sources": refs,
        "sources_y": sources_y,
        "sources_heading": sources_heading,
        "expected_node_ids": checked["adopted_node_ids"],
        "expected_edge_ids": checked["adopted_edge_ids"],
        "edge_endpoints": {e["id"]: [e["source_id"], e["target_id"]] for e in edges},
        "layout_revision": 2,
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
        _require(
            all(edge[field] == record[field] for field in ("label", "relation_type"))
            and edge["claim_refs"] == record["claim_ids"],
            "edge_content",
            f"{key}: relation differs from graph",
            edge_id=key,
        )


def check_scene(scene, data=None):
    _require(
        scene.get("layout_revision") == 2,
        "layout_revision",
        "render the graph with layout revision 2",
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
            and n["lane_id"] == LANE_BY_TYPE[n["type"]]
            and lane["x"] <= x
            and x + w <= lane["x"] + lane["width"],
            "node_lane",
            f"{key}: node outside its category lane",
            node_id=key,
        )
        year = n["anchor_year"]
        anchor = axis["unknown_y"] if year is None else anchors.get(year)
        _require(
            anchor is not None
            and _same_port(n["anchor"], (x + w / 2, anchor))
            and anchor <= y
            and y + h <= band_limits[anchor]
            and n["date_label"] == date_text(year),
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
        _require(
            edge["arrow"] == _arrow(edge["target_port"])
            and edge["style"]["dash"]
            == ("5,5" if edge["relation_type"] == "hypothesis" else ""),
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
