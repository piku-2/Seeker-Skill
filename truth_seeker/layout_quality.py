"""Measure map geometry and compare paths using sampled cubic segments."""

from itertools import combinations, pairwise
from math import dist

from .routing import bounds, cubics, edge_obstacles, first_collision, intersects, padded


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _difference(a, b):
    return a[0] - b[0], a[1] - b[1]


def _crosses(a, b, c, d):
    ab, cd = _difference(b, a), _difference(d, c)
    divisor = _cross(ab, cd)
    if abs(divisor) < 1e-8:
        return False
    ac = _difference(c, a)
    t, u = _cross(ac, cd) / divisor, _cross(ac, ab) / divisor
    return 1e-7 < t < 1 - 1e-7 and 1e-7 < u < 1 - 1e-7


class PathSample:
    def __init__(self, path):
        self.box = bounds(path)
        points = [path[0]]
        for a, b, c, d in cubics(path):
            chord = _difference(d, a)
            # Only collapse a straight segment when both controls lie within its ends.
            if a != d and all(
                abs(_cross(chord, _difference(p, a))) < 1e-7
                and intersects((*p, *p), bounds((a, d)))
                for p in (b, c)
            ):
                points.append(d)
                continue
            for i in range(1, 9):
                t = i / 8
                weights = ((1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t**2 * (1 - t), t**3)
                points.append(
                    tuple(
                        sum(v * p[axis] for v, p in zip(weights, (a, b, c, d)))
                        for axis in (0, 1)
                    )
                )
        self.segments = [(a, b, bounds((a, b))) for a, b in pairwise(points)]
        self.length = sum(dist(a, b) for a, b, _ in self.segments)

    def crosses(self, other):
        if not intersects(self.box, other.box):
            return False
        return any(
            intersects(ab, cd) and _crosses(a, b, c, d)
            for a, b, ab in self.segments
            for c, d, cd in other.segments
        )


def layout_metrics(scene):
    """Count proper crossing pairs, excluding endpoint touches and shared segments."""
    nodes = {n["node_id"]: n["bbox"] for n in scene["nodes"]}
    panels = {
        a["id"]: box
        for a in scene.get("assets", [])
        if (box := a.get("reserved_bbox", a.get("bbox"))) is not None
    }
    paths = [PathSample(edge["path"]) for edge in scene["edges"]]
    edges = scene["edges"]

    def endpoints(edge):
        return {
            (edge[f"{role}_node_id"], edge.get(f"{role}_port", {}).get("side"))
            for role in ("source", "target")
        }

    crossings = same_card = same_side = 0
    ports = [endpoints(edge) for edge in edges]
    for (first, path_a), (second, path_b) in combinations(zip(ports, paths), 2):
        if not path_a.crosses(path_b):
            continue
        crossings += 1
        same_card += bool({key for key, _ in first} & {key for key, _ in second})
        same_side += any(side is not None for _, side in first & second)
    node_hits = 0
    panel_hits = 0
    obstacles = {**nodes, **panels}
    for e, path in zip(scene["edges"], paths):
        source, target = e["source_node_id"], e["target_node_id"]
        for key, box in edge_obstacles(obstacles, source, target):
            if (
                intersects(path.box, box)
                and first_collision(e["path"], [(key, box)]) is not None
            ):
                if key in panels:
                    panel_hits += 1
                else:
                    node_hits += 1
    hubs = []
    for node in scene["nodes"]:
        if node["importance"]["level"] < 3:
            continue
        key = node["node_id"]
        region = padded(node["bbox"], 24)
        through = sum(
            key not in (edge["source_node_id"], edge["target_node_id"])
            and intersects(path.box, region)
            and any(intersects(segment[2], region) for segment in path.segments)
            for edge, path in zip(scene["edges"], paths)
        )
        hubs.append({"node_id": key, "nearby_unrelated_edges": through})
    overlaps = sum(
        intersects(padded(a, -1e-6), padded(b, -1e-6))
        for a, b in combinations(nodes.values(), 2)
    )
    lengths = [p.length for p in paths]
    return {
        "crossing_pairs": crossings,
        "crossings_sharing_card": same_card,
        "crossings_sharing_port_side": same_side,
        "edge_node_collisions": node_hits,
        "edge_asset_collisions": panel_hits,
        "card_overlaps": overlaps,
        "total_edge_length_pt": round(sum(lengths), 2),
        "max_edge_length_pt": round(max(lengths, default=0), 2),
        "longest_edge_id": max(zip(edges, lengths), key=lambda item: item[1])[0].get(
            "edge_id"
        )
        if edges
        else None,
        "important_node_congestion": sorted(
            hubs, key=lambda h: (-h["nearby_unrelated_edges"], h["node_id"])
        ),
        "canvas_pt": [scene["canvas_width"], scene["canvas_height"]],
        "nodes": {"drawn": len(nodes), "expected": len(scene["expected_node_ids"])},
        "edges": {"drawn": len(paths), "expected": len(scene["expected_edge_ids"])},
        "coverage_ratio": {
            "nodes": len(nodes) / len(scene["expected_node_ids"])
            if scene["expected_node_ids"]
            else 1.0,
            "edges": len(paths) / len(scene["expected_edge_ids"])
            if scene["expected_edge_ids"]
            else 1.0,
        },
    }
