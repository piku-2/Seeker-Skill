"""Pack cards into chronological bands, then shorten their important connections."""

from collections import defaultdict
from itertools import permutations


def place_rows(groups, edges, column_count, preferred_widths, weights):
    cards = {n["node_id"]: n for group in groups.values() for n in group}
    near = defaultdict(dict)
    for edge in edges:
        a, b = edge["source_id"], edge["target_id"]
        if a == b:
            continue
        year_a, year_b = cards[a]["anchor_year"], cards[b]["anchor_year"]
        years = (
            abs(year_a - year_b) if year_a is not None and year_b is not None else 300
        )
        relation = edge["relation_type"]
        affinity = 1.5 if relation in {"dependency", "confluence", "influence"} else 0.7
        score = affinity * weights[edge["id"]]["level"] / (1 + years / (100 + years))
        near[a][b] = near[a].get(b, 0) + score
        near[b][a] = near[b].get(a, 0) + score

    columns = {key: card["category_id"] for key, card in cards.items()}
    bands = {}
    for year, group in groups.items():
        remaining = {n["node_id"] for n in group}
        rows = []
        while remaining:
            row = []
            while remaining and len(row) < column_count:
                key = min(
                    remaining,
                    key=lambda key: (
                        -sum(near[key].get(other, 0) for other in row),
                        -cards[key]["importance"]["level"],
                        -sum(near[key].values()),
                        key,
                    ),
                )
                remaining.remove(key)
                row.append(key)
            rows.append(row)
        bands[year] = rows

    choices = {
        n: tuple(permutations(range(column_count), n))
        for n in range(1, column_count + 1)
    }
    rows = [row for group in bands.values() for row in group]

    def best_columns(row):
        row_set = set(row)
        fixed_cost = {
            (key, col): (
                0.24 * abs(col - cards[key]["category_id"])
                + max(0, cards[key]["bbox"][2] - preferred_widths[col]) / 500
                + sum(
                    weight * abs(col - columns[other])
                    for other, weight in near[key].items()
                    if other not in row_set
                )
            )
            for key in row
            for col in range(column_count)
        }
        pairs = [
            (i, j, near[a][b])
            for i, a in enumerate(row)
            for j, b in enumerate(row)
            if i < j and b in near[a]
        ]

        def cost(slots):
            return sum(fixed_cost[key, col] for key, col in zip(row, slots)) + sum(
                weight * abs(slots[i] - slots[j]) for i, j, weight in pairs
            )

        current = tuple(columns[key] for key in row)
        best = min(choices[len(row)], key=lambda slots: (cost(slots), slots))
        if len(set(current)) == len(row) and cost(current) <= cost(best) + 1e-9:
            return False
        columns.update(zip(row, best))
        return True

    # Revisit bands in both directions so connected cards on either side
    # influence the result. Stop early when every legal row assignment is stable.
    for sweep in range(12):
        changed = False
        for row in rows if sweep % 2 == 0 else reversed(rows):
            changed |= best_columns(row)
        if not changed:
            break
    return {
        year: [[(columns[key], cards[key]) for key in row] for row in group]
        for year, group in bands.items()
    }
