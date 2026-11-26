"""Evidence-backed vector figures shared by the SVG and PDF renderers."""

import math
from collections import defaultdict

from .model import ensure
from .typography import text_lines

INK = "#243747"
ACCENT = "#326D8D"
MUTED = "#DBE8ED"
WHITE = "#FFFFFF"
EDGE_FIGURES = ("relations", "lineage", "composition")
FIGURE_KINDS = (*EDGE_FIGURES, "timeline", "table", "bar", "distribution")


EDGE_ROLES = (
    "technical_succession",
    "product_development",
    "organization_change",
    "conceptual_influence",
    "textual_transmission",
    "textual_revision",
    "component_of",
)
FIGURE_MEANINGS = {
    **{role: ("relations", "lineage") for role in EDGE_ROLES},
    "component_of": ("relations", "composition"),
    "dependency": ("relations",),
    "relationship_overview": ("relations",),
    "precedence": ("relations", "lineage", "timeline"),
    "quantitative_comparison": ("table", "bar"),
    "comparison": ("table",),
    "source_concordance": ("table",),
    "geographic_distribution": ("distribution",),
    "dispute": ("relations", "table"),
}
RELATION_LABELS = {
    "dependency": "依存",
    "influence": "影響",
    "context": "背景",
    "confluence": "合流",
    "parallel": "並行",
    "opposition": "対立",
    "hypothesis": "仮説",
    "other": "その他",
}


def _meaning_matches(meaning, edge, claims):
    if meaning == "relationship_overview":
        return True
    if meaning in EDGE_ROLES:
        return edge.get("semantic_role") == meaning
    if meaning == "precedence":
        return "chronology" in edge
    if meaning == "dependency":
        return edge["relation_type"] == "dependency"
    return edge["relation_type"] in ("opposition", "hypothesis") or any(
        claims[cid]["assessment"] in ("contested", "hypothetical")
        for cid in edge["claim_ids"]
    )


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return type(value) in (int, float) and abs(value) <= 1e100 and math.isfinite(value)


def validate_figure(asset, nodes, edges, evidence, claims, chronology):
    figure = asset.get("figure")
    key = asset["id"]
    ensure(
        isinstance(figure, dict) and not asset.get("local_path"),
        f"{key}: expected a generated figure without a raster",
    )
    kind = figure.get("kind")
    ensure(
        kind in FIGURE_KINDS,
        f"{key}: unknown figure kind",
    )
    meaning = figure.get("meaning")
    if "title" in figure or "meaning" in figure:
        title = figure.get("title")
        ensure(
            _text(title)
            and len(title) <= 160
            and title.splitlines() == [title]
            and isinstance(meaning, str)
            and kind in FIGURE_MEANINGS.get(meaning, ()),
            f"{key}: figure title and compatible meaning are required together",
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
    if kind == "timeline":
        ids = figure.get("node_ids")
        ensure(
            isinstance(ids, list)
            and len(ids) >= 2
            and all(isinstance(nid, str) and nid in owners for nid in ids)
            and len(set(ids)) == len(ids)
            and all(
                chronology[nid]["mode"] in ("anchored", "estimated") for nid in ids
            ),
            f"{key}: timeline needs distinct dated owners with historical date assertions",
        )
        for nid in ids:
            node = nodes[nid]
            date = node["display_date"]
            basis_ids = (
                date["basis_claim_ids"]
                if date["mode"] == "estimated"
                else (
                    {
                        cid
                        for item in node["dates"]
                        for cid in item.get("basis_claim_ids", [])
                    }
                    or node["claim_ids"]
                )
            )
            ensure(
                any(
                    refs.intersection(claims[cid]["supporting_evidence_ids"])
                    for cid in basis_ids
                ),
                f"{key}: missing timeline evidence for {nid}",
            )
        return
    if kind in EDGE_FIGURES:
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
            if meaning is not None:
                ensure(
                    _meaning_matches(meaning, edges[eid], claims),
                    f"{key}: figure meaning {meaning} differs from edge {eid}",
                )
            edge_sources = {
                ref
                for cid in edges[eid]["claim_ids"]
                for field in ("supporting_evidence_ids", "contradicting_evidence_ids")
                if kind == "relations" or field == "supporting_evidence_ids"
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
    if figure["kind"] in (*EDGE_FIGURES, "distribution", "timeline"):
        return 460
    if figure["kind"] == "table":
        return max(280, 88 * len(figure["columns"]))
    return 360


def draw_figure(figure, x, top, width, nodes, edges, *, legacy=False):
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
    if figure.get("title"):
        y = label(figure["title"], x, y, width) + 8
    if kind in ("table", "timeline"):
        if kind == "timeline":
            ordered = sorted(
                (nodes[nid] for nid in figure["node_ids"]),
                key=lambda node: node["anchor_year"],
            )
            figure = {
                "columns": ["対象", "年代（研究上の推定を含む）"],
                "rows": [
                    {"cells": [node["label"], node["date_label"]]} for node in ordered
                ],
            }
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
    elif kind in EDGE_FIGURES:
        # Reconstruct the old geometry only when checking an archived scene.
        if legacy and kind == "lineage":
            y = (
                label("本文の伝承・翻訳・継承（関係の種類は各行に表示）", x, y, width)
                + 8
            )
        if legacy and kind == "composition":
            y = label("全体と構成要素（矢印は年代順を示さない）", x, y, width) + 8
        if (
            kind == "composition"
            and not legacy
            and figure.get("meaning") != "component_of"
        ):
            kind = "relations"
        box_width = width * 0.25
        middle = x + box_width
        right = x + 3 * box_width
        groups = defaultdict(list)
        for eid in figure["edge_ids"]:
            edge = edges[eid]
            group = (
                edge["source_id"]
                if kind == "lineage"
                else (edge["target_id"] if kind == "composition" else eid)
            )
            groups[group].append(edge)
        for group, members in groups.items():
            group_top = y
            if kind in ("lineage", "composition"):
                shared_x = x if kind == "lineage" else right
                shared_bottom = (
                    label(nodes[group]["label"], shared_x + 7, y + 8, box_width - 14)
                    + 7
                )
            for edge in members:
                qualifiers = [
                    name
                    for state, name in (
                        ("favored", "有力説"),
                        ("contested", "論争中"),
                        ("hypothetical", "仮説"),
                        ("unverified", "未検証"),
                    )
                    if state in edge.get("assessments", ())
                    or (
                        state == "hypothetical"
                        and edge["relation_type"] == "hypothesis"
                    )
                ]
                relation = edge["label"] or edge["relation_type"]
                if not legacy:
                    relation_type = RELATION_LABELS[edge["relation_type"]]
                    relation = (
                        f"{edge['label']}（{relation_type}）"
                        if edge["label"]
                        else relation_type
                    )
                if qualifiers:
                    relation += "（" + "・".join(qualifiers) + "）"
                end = label(relation, middle + 12, y + 6, 2 * box_width - 24)
                endpoints = []
                if kind != "lineage":
                    endpoints.append((x, edge["source_id"]))
                if kind != "composition":
                    endpoints.append((right, edge["target_id"]))
                bottom = (
                    max(
                        [end + 16]
                        + [
                            label(nodes[nid]["label"], left + 7, y + 8, box_width - 14)
                            for left, nid in endpoints
                        ]
                    )
                    + 7
                )
                for left, _ in endpoints:
                    rect(left, y, box_width, bottom - y, MUTED)
                rect(middle + 3, bottom - 9, 2 * box_width - 16, 1, ACCENT)
                label("→", right - 14, bottom - 14, 14, ACCENT)
                y = bottom + 8
            if kind in ("lineage", "composition"):
                y = max(y, shared_bottom + 8)
                rect(shared_x, group_top, box_width, y - group_top - 8, MUTED)
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
