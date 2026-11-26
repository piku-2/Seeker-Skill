"""Pack each chronological band using measured card widths and graph affinities."""

import math
from collections import defaultdict
from statistics import median

CARD_GAP = 40
ROW_GAP = 34


def _neighbors(edges, cards, weights):
    near = defaultdict(dict)
    for edge in edges:
        a, b = edge["source_id"], edge["target_id"]
        if a == b:
            continue
        ya, yb = cards[a]["anchor_year"], cards[b]["anchor_year"]
        years = abs(ya - yb) if ya is not None and yb is not None else 300
        affinity = (
            1.5
            if edge["relation_type"] in {"dependency", "confluence", "influence"}
            else 0.7
        )
        score = affinity * weights[edge["id"]]["level"] / (1 + years / (75 + years))
        near[a][b] = near[a].get(b, 0) + score
        near[b][a] = near[b].get(a, 0) + score
    for key, panel in cards.items():
        for owner in panel.get("panel_owners", ()):
            near[key][owner] = 0.16
            near[owner][key] = 0.16
    return near


def _group_order(group, near):
    def priority(node):
        return (
            -node["importance"]["level"],
            -sum(near[node["node_id"]].values()),
            node["node_id"],
        )

    by_id = {n["node_id"]: n for n in group}
    remaining = set(by_id)
    order = []
    for node in sorted(group, key=priority):
        if node["node_id"] not in remaining:
            continue
        current = node["node_id"]
        while current in remaining:
            remaining.remove(current)
            order.append(by_id[current])
            options = near[current].keys() & remaining
            if not options:
                break
            current = min(
                options, key=lambda key: (-near[current][key], priority(by_id[key]))
            )
    return order


def _pack(groups, capacity):
    packed = {}
    for year, group in groups.items():
        rows = []
        used = 0
        for node in group:
            width = node["bbox"][2]
            if not rows or used + CARD_GAP + width > capacity:
                rows.append([])
                used = -CARD_GAP
            rows[-1].append(node)
            used += CARD_GAP + width
        packed[year] = rows
    return packed


def _row_width(row):
    return sum(n["bbox"][2] for n in row) + (len(row) - 1) * CARD_GAP


def place_rows(groups, edges, weights, chronological, minimum_width=0):
    cards = {n["node_id"]: n for group in groups.values() for n in group}
    if not cards:
        return {year: [] for year in groups}, minimum_width
    near = _neighbors(edges, cards, weights)
    ordered = {year: _group_order(group, near) for year, group in groups.items()}
    widest = max(n["bbox"][2] for n in cards.values())
    widest_band = max(_row_width(group) for group in ordered.values() if group)
    typical = median(n["bbox"][2] for n in cards.values())
    # Width trials grow geometrically and stop at the width of the widest
    # unbroken band. Charge for width as well as measured vertical extent.
    candidates = {widest, widest_band}
    mixed_band = any(
        len(group) >= 6 and any("panel" in item for item in group)
        for group in ordered.values()
    )
    growth = 1.28 if mixed_band else 1.45
    width_cost = 0.40 if mixed_band else 0.35
    capacity = widest + typical * 0.45
    while capacity < widest_band:
        candidates.add(capacity)
        capacity *= growth
    best = None
    for capacity in sorted(candidates):
        packed = _pack(ordered, capacity)
        width = max(
            minimum_width,
            max(_row_width(row) for rows in packed.values() for row in rows),
        )
        band_heights = {
            year: sum(max(n["bbox"][3] for n in row) + ROW_GAP for row in rows)
            for year, rows in packed.items()
        }
        height = sum(
            max(chronological[year], band_height)
            for year, band_height in band_heights.items()
        )
        # Calendar gaps elsewhere do not make a dense band easier to scan.
        # Use the occupied height and widest unsplit item in each crowded band.
        elongation = max(
            (
                max(0, max(map(_row_width, rows)) - 4 * band_heights[year])
                for year, rows in packed.items()
                if len(ordered[year]) >= 6
            ),
            default=0,
        )
        if len(cards) > 1:
            score = (
                1.4 * height
                + width_cost * width
                + 0.4 * max(0, width - 3 * height)
                + 0.8 * elongation
            )
        else:
            score = width
        if best is None or score < best[0] - 1:
            best = score, width, packed
    _, width, packed = best

    rows = [row for band in packed.values() for row in band]
    offsets = [max(0, (width - _row_width(row)) / 2) for row in rows]
    centers = {}

    def update(row, left):
        for node in row:
            card_width = node["bbox"][2]
            centers[node["node_id"]] = left + card_width / 2
            left += card_width + CARD_GAP

    for row, left in zip(rows, offsets):
        update(row, left)
    # A small, bounded number of barycentric sweeps settles neighboring
    # bands without permuting all possible orders or moving time anchors.
    for sweep in range(6):
        indexes = range(len(rows)) if sweep % 2 == 0 else reversed(range(len(rows)))
        drift = 0
        for i in indexes:
            row = rows[i]
            keys = sorted(n["node_id"] for n in row)
            row_ids = set(keys)
            desired = {}
            for key in keys:
                adjacent = near[key]
                mass = sum(adjacent.values()) + 0.35
                desired[key] = (
                    sum(centers[k] * w for k, w in adjacent.items()) + 0.35 * width / 2
                ) / mass

            def energy():
                return sum(
                    0.35 * abs(centers[k] - width / 2)
                    + sum(
                        weight
                        * abs(centers[k] - centers[j])
                        * (0.5 if j in row_ids else 1)
                        for j, weight in near[k].items()
                    )
                    for k in keys
                )

            previous = energy()
            old = row[:]
            row.sort(key=lambda n: (desired[n["node_id"]], n["node_id"]))
            update(row, 0)
            pairs = sorted(
                (desired[k] - centers[k], sum(near[k].values()) + 0.35) for k in keys
            )
            half = sum(w for _, w in pairs) / 2
            for shift, weight in pairs:
                half -= weight
                if half <= 0:
                    break
            left = max(0, min(width - _row_width(row), shift))
            update(row, left)
            if energy() < previous - 0.1:
                drift += abs(left - offsets[i]) + (1 if old != row else 0)
                offsets[i] = left
            else:
                row[:] = old
                update(row, offsets[i])
        if math.isclose(drift, 0, abs_tol=0.1):
            break
    return {
        year: [
            [(centers[n["node_id"]] - n["bbox"][2] / 2, n) for n in row] for row in band
        ]
        for year, band in packed.items()
    }, width
