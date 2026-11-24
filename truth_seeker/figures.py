"""Evidence-backed vector figures shared by the SVG and PDF renderers."""

import math

from .model import ensure
from .typography import text_lines

INK = "#243747"
ACCENT = "#326D8D"
MUTED = "#DBE8ED"
WHITE = "#FFFFFF"


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return type(value) in (int, float) and abs(value) <= 1e100 and math.isfinite(value)


def validate_figure(asset, nodes, edges, evidence, claims):
    figure = asset.get("figure")
    key = asset["id"]
    ensure(
        isinstance(figure, dict) and not asset.get("local_path"),
        f"{key}: expected a generated figure without a raster",
    )
    kind = figure.get("kind")
    ensure(
        kind in ("table", "relations", "bar", "distribution"),
        f"{key}: unknown figure kind",
    )
    refs = set(asset.get("evidence_ids", []))
    ensure(
        bool(refs)
        and all(evidence[eid]["access_status"] == "accessible" for eid in refs),
        f"{key}: generated figures require accessible evidence",
    )
    owners = set(asset.get("related_node_ids", [])) | (
        {asset["node_id"]} if asset.get("node_id") else set()
    )
    ensure(
        owners and all(nodes[k]["adoption_status"] == "adopted" for k in owners),
        f"{key}: generated figure needs adopted owners",
    )
    if kind == "relations":
        ids = figure.get("edge_ids")
        ensure(
            isinstance(ids, list)
            and bool(ids)
            and all(isinstance(eid, str) and eid in edges for eid in ids),
            f"{key}: missing relationship edge references",
        )
        ensure(
            len(set(ids)) == len(ids)
            and all(
                edges[eid]["adoption_status"] == "adopted"
                and {edges[eid]["source_id"], edges[eid]["target_id"]} <= owners
                for eid in ids
            ),
            f"{key}: relationships must reference distinct adopted edges between figure owners",
        )
        for eid in ids:
            edge_sources = {
                ref
                for cid in edges[eid]["claim_ids"]
                for field in ("supporting_evidence_ids", "contradicting_evidence_ids")
                for ref in claims[cid][field]
            }
            ensure(refs & edge_sources, f"{key}: missing figure evidence for {eid}")
        return
    rows = figure.get("rows")
    ensure(isinstance(rows, list) and bool(rows), f"{key}: figure requires rows")
    columns = figure.get("columns")
    if kind == "table":
        ensure(
            isinstance(columns, list)
            and bool(columns)
            and all(_text(x) for x in columns),
            f"{key}: table requires column labels",
        )
    if kind == "bar":
        ensure(_text(figure.get("unit")), f"{key}: chart requires a unit")
    for row in rows:
        ensure(
            isinstance(row, dict)
            and isinstance(row.get("evidence_ids"), list)
            and row["evidence_ids"]
            and all(
                isinstance(ref, str) and ref in refs for ref in row["evidence_ids"]
            ),
            f"{key}: each row must cite figure evidence",
        )
        if kind == "table":
            cells = row.get("cells")
            ensure(
                isinstance(cells, list)
                and len(cells) == len(columns)
                and all(isinstance(c, str) for c in cells),
                f"{key}: table row does not match its columns",
            )
        else:
            ensure(_text(row.get("label")), f"{key}: missing point label")
            if kind == "bar":
                ensure(_number(row.get("value")), f"{key}: invalid chart value")
            else:
                lat, lon = row.get("latitude"), row.get("longitude")
                ensure(
                    _number(lat)
                    and _number(lon)
                    and -90 <= lat <= 90
                    and -180 <= lon <= 180,
                    f"{key}: invalid geographical coordinates",
                )


def preferred_width(figure):
    if figure["kind"] in ("relations", "distribution"):
        return 460
    if figure["kind"] == "table":
        return max(280, 88 * len(figure["columns"]))
    return 360


def draw_figure(figure, x, top, width, nodes, edges):
    shapes, lines = [], []
    y = top

    def rect(x, y, w, h, fill):
        shapes.append({"bbox": [x, y, w, h], "fill": fill})

    def label(text, left, at, room, color=INK):
        block = text_lines(text, left, at, room, 9, color, "figure_label")
        lines.extend(block)
        box = block[-1]["bbox"]
        return box[1] + box[3]

    kind = figure["kind"]
    if kind == "table":
        headings = figure["columns"]
        rows = [row["cells"] for row in figure["rows"]]
        room = width / len(headings)
        for i, row in enumerate([headings, *rows]):
            bottoms = [
                label(
                    cell, x + j * room + 5, y + 5, room - 10, WHITE if i == 0 else INK
                )
                for j, cell in enumerate(row)
            ]
            h = max(bottoms) - y + 5
            for j in range(len(row)):
                rect(
                    x + j * room,
                    y,
                    room - 0.6,
                    h,
                    ACCENT if i == 0 else MUTED if i % 2 else WHITE,
                )
            y += h + 1
    elif kind == "relations":
        box_width = width * 0.25
        middle = x + box_width
        for eid in figure["edge_ids"]:
            edge = edges[eid]
            qualifiers = [
                label
                for state, label in (
                    ("favored", "有力説"),
                    ("contested", "論争中"),
                    ("hypothetical", "仮説"),
                    ("unverified", "未検証"),
                )
                if state in edge.get("assessments", ())
                or (state == "hypothetical" and edge["relation_type"] == "hypothesis")
            ]
            relation = edge["label"] or edge["relation_type"]
            if qualifiers:
                relation += "（" + "・".join(qualifiers) + "）"
            end = label(relation, middle + 12, y + 6, 2 * box_width - 24)
            left_end = label(
                nodes[edge["source_id"]]["label"], x + 7, y + 8, box_width - 14
            )
            right = x + 3 * box_width
            right_end = label(
                nodes[edge["target_id"]]["label"], right + 7, y + 8, box_width - 14
            )
            bottom = max(end + 16, left_end, right_end) + 7
            rect(x, y, box_width, bottom - y, MUTED)
            rect(right, y, box_width, bottom - y, MUTED)
            rect(middle + 3, bottom - 9, 2 * box_width - 16, 1, ACCENT)
            label("→", right - 14, bottom - 14, 14, ACCENT)
            y = bottom + 8
    elif kind == "bar":
        rows = figure["rows"]
        low, high = (
            min(0, min(r["value"] for r in rows)),
            max(0, max(r["value"] for r in rows)),
        )
        extent = high - low or 1
        zero = x + width * (-low / extent)
        for row in rows:
            y = (
                label(f"{row['label']}　{row['value']} {figure['unit']}", x, y, width)
                + 3
            )
            end = x + width * ((row["value"] - low) / extent)
            if end != zero:
                rect(min(zero, end), y, abs(end - zero), 9, ACCENT)
            rect(min(zero, x + width - 1), y, 1, 10, INK)
            y += 17
    else:
        rows = figure["rows"]
        lat0, lat1 = min(r["latitude"] for r in rows), max(r["latitude"] for r in rows)
        lon0, lon1 = (
            min(r["longitude"] for r in rows),
            max(r["longitude"] for r in rows),
        )
        y = label("緯度・経度の分布（北が上、地形は省略）", x, y, width) + 7
        height = min(width * 0.52, 240)
        rect(x, y, width, height, MUTED)
        seen = set()
        for row in rows:
            px = (
                x
                + 5
                + (width - 10)
                * ((row["longitude"] - lon0) / (lon1 - lon0) if lon1 != lon0 else 0.5)
            )
            py = (
                y
                + 5
                + (height - 10)
                * ((lat1 - row["latitude"]) / (lat1 - lat0) if lat1 != lat0 else 0.5)
            )
            if (px, py) not in seen:
                rect(px - 2, py - 2, 4, 4, ACCENT)
                seen.add((px, py))
        y += height + 8
        for row in rows:
            y = (
                label(
                    f"{row['label']}　{row['latitude']}, {row['longitude']}",
                    x,
                    y,
                    width,
                )
                + 4
            )
    return shapes, lines, y
