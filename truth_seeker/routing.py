"""Distribute boundary ports and route cubic Bézier paths around map obstacles."""

from bisect import bisect_left
from collections import defaultdict
from functools import cache
from heapq import heappop, heappush
from itertools import pairwise
from math import dist

from .model import LayoutError

CLEARANCE = 5
LEAD = 14
TRAFFIC_SPAN = 60
NORMALS = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}


def padded(box, margin):
    x, y, w, h = box
    return x - margin, y - margin, x + w + margin, y + h + margin


def bounds(points):
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def intersects(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def cubics(path):
    """A path starts with one point, followed by triples of cubic control/end points."""
    return (path[i : i + 4] for i in range(0, len(path) - 1, 3))


def curve_hits(curve, obstacle):
    # A Bézier lies in its control hull. Subdivision tightens the bounds;
    # ambiguous intersections under 0.05pt are conservatively blocked.
    if not intersects(bounds(curve), obstacle):
        return False
    a, b, c, d = curve
    if any(intersects((*p, *p), obstacle) for p in (a, d)):
        return True
    if max(dist(a, point) for point in (b, c, d)) < 0.05:
        return True
    ab, bc, cd = lerp(a, b, 0.5), lerp(b, c, 0.5), lerp(c, d, 0.5)
    abc, bcd = lerp(ab, bc, 0.5), lerp(bc, cd, 0.5)
    mid = lerp(abc, bcd, 0.5)
    return curve_hits((a, ab, abc, mid), obstacle) or curve_hits(
        (mid, bcd, cd, d), obstacle
    )


def first_collision(path, obstacles):
    extent = bounds(path)
    segments = [(curve, bounds(curve)) for curve in cubics(path)]
    for key, box in obstacles:
        if intersects(extent, box) and any(
            intersects(hull, box) and curve_hits(curve, box) for curve, hull in segments
        ):
            return key
    return None


def edge_obstacles(obstacles, source, target):
    return [
        (key, padded(box, -0.12 if key in (source, target) else CLEARANCE))
        for key, box in obstacles.items()
    ]


def port_point(box, side, fraction):
    x, y, w, h = box
    if side in {"top", "bottom"}:
        return x + 13 + (w - 26) * fraction, y + (h if side == "bottom" else 0)
    return x + (w if side == "right" else 0), y + 13 + (h - 26) * fraction


def _ports(edges, positions):
    requests = defaultdict(list)
    for edge in edges:
        source, target = edge["source_id"], edge["target_id"]
        sx, sy, sw, sh = positions[source]["bbox"]
        tx, ty, tw, th = positions[target]["bbox"]
        sc, tc = (sx + sw / 2, sy + sh / 2), (tx + tw / 2, ty + th / 2)
        dx, dy = tc[0] - sc[0], tc[1] - sc[1]
        if source == target:
            sides = "bottom", "right"
        elif sy + sh <= ty or ty + th <= sy:
            sides = ("bottom", "top") if dy > 0 else ("top", "bottom")
        else:
            sides = ("right", "left") if dx > 0 else ("left", "right")
        for role, node, other, side in zip(
            ("source", "target"), (source, target), (tc, sc), sides
        ):
            order = other[0] if side in {"top", "bottom"} else other[1]
            requests[node, side].append((order, edge["id"], role))
    ports = {}
    for (node, side), group in requests.items():
        for index, (_, edge_id, role) in enumerate(sorted(group), 1):
            fraction = index / (len(group) + 1)
            ports[edge_id, role] = {
                "side": side,
                "fraction": fraction,
                "point": list(port_point(positions[node]["bbox"], side, fraction)),
            }
    return ports


def _extend(point, side, length):
    return tuple(p + n * length for p, n in zip(point, NORMALS[side]))


def _line_cubic(a, b):
    return lerp(a, b, 1 / 3), lerp(a, b, 2 / 3), b


def _rounded(points, radius):
    path = [points[0]]
    for a, corner, b in zip(points, points[1:], points[2:]):
        # Use a single smooth cubic at each corner, tangent to the approaches.
        before = lerp(corner, a, min(radius / dist(corner, a), 0.42))
        after = lerp(corner, b, min(radius / dist(corner, b), 0.42))
        path.extend(_line_cubic(path[-1], before))
        path.extend((lerp(before, corner, 2 / 3), lerp(after, corner, 2 / 3), after))
    path.extend(_line_cubic(path[-1], points[-1]))
    return path


def _obstacle_index(obstacles):
    @cache
    def blocked_intervals(axis, line):
        cross = 1 - axis
        spans = sorted(
            (box[axis], box[axis + 2])
            for box in obstacles
            if box[cross] <= line <= box[cross + 2]
        )
        merged = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return merged, [end for _, end in merged]

    def clear(a, b, axis):
        spans, ends = blocked_intervals(axis, a[1 - axis])
        lo, hi = sorted((a[axis], b[axis]))
        i = bisect_left(ends, lo)
        return i == len(spans) or spans[i][0] > hi

    return clear


def _traffic_cost(a, b, axis, traffic):
    step = abs(a[axis] - b[axis])
    bucket = int((a[axis] + b[axis]) / (2 * TRAFFIC_SPAN))
    usage = traffic.get((axis, a[1 - axis], bucket), 0)
    return step * (1 + min(2, usage * 0.35))


def _gutter_route(start, goal, columns, rows, clear, traffic):
    # Every lane has continuous, obstacle-free gutters. Try joining two
    # gutters through a clear row before exploring the full corridor grid.
    def nearby(point):
        x, y = point
        left = [col for col in reversed(columns) if col <= x][:2]
        right = [col for col in columns if col >= x][:2]
        return [col for col in dict.fromkeys(left + right) if clear(point, (col, y), 0)]

    y0, y1 = sorted((start[1], goal[1]))
    middle = (y0 + y1) / 2
    span = abs(start[0] - goal[0]) + y1 - y0
    best = None
    best_cost = float("inf")
    for x0 in nearby(start):
        for x1 in nearby(goal):
            a = x0, start[1]
            b = x1, goal[1]
            for y in rows if x0 != x1 else (middle,):
                length = (
                    abs(start[0] - x0)
                    + abs(goal[0] - x1)
                    + abs(x0 - x1)
                    + abs(y - a[1])
                    + abs(y - b[1])
                )
                if (
                    length > span + 480
                    or length >= best_cost
                    or not clear((x0, y), (x1, y), 0)
                ):
                    continue
                cost = (
                    _traffic_cost(start, a, 0, traffic)
                    + _traffic_cost(a, (x0, y), 1, traffic)
                    + _traffic_cost((x0, y), (x1, y), 0, traffic)
                    + _traffic_cost((x1, y), b, 1, traffic)
                    + _traffic_cost(b, goal, 0, traffic)
                    + abs(y - middle) * 0.02
                )
                if cost < best_cost:
                    best_cost = cost
                    best = [start, a, (x0, y), (x1, y), b, goal]
    return best


def _corridor_route(start, goal, columns, rows, clear, traffic):
    """A* over lane gutters and the spaces above and below laid-out cards."""
    xs = sorted(set(columns) | {start[0], goal[0]})
    ys = sorted(set(rows) | {start[1], goal[1]})
    source = xs.index(start[0]), ys.index(start[1])
    target = xs.index(goal[0]), ys.index(goal[1])
    initial = (*source, -1)
    queue, best, previous = [(0, 0, initial)], {initial: 0}, {}

    while queue:
        _, negative_cost, state = heappop(queue)
        cost = -negative_cost
        i, j, direction = state
        if cost != best[state]:
            continue
        if (i, j) == target:
            route = [(xs[i], ys[j])]
            while state != initial:
                state = previous[state]
                route.append((xs[state[0]], ys[state[1]]))
            return route[::-1]
        a = xs[i], ys[j]
        for ii, jj, axis in (
            (i - 1, j, 0),
            (i + 1, j, 0),
            (i, j - 1, 1),
            (i, j + 1, 1),
        ):
            if not (0 <= ii < len(xs) and 0 <= jj < len(ys)):
                continue
            b = xs[ii], ys[jj]
            if not clear(a, b, axis):
                continue
            # Prefer free gutters for long parallel runs and discourage extra corners.
            new_cost = cost + _traffic_cost(a, b, axis, traffic)
            if direction not in (-1, axis):
                new_cost += 16
            neighbor = (ii, jj, axis)
            if new_cost < best.get(neighbor, float("inf")):
                best[neighbor], previous[neighbor] = new_cost, state
                remaining = abs(b[0] - goal[0]) + abs(b[1] - goal[1])
                heappush(queue, (new_cost + 1.15 * remaining, -new_cost, neighbor))
    return None


def _turns(points):
    path = []
    for point in points:
        if path and point == path[-1]:
            continue
        if len(path) > 1:
            a, b = path[-2:]
            straight = any(
                a[axis] == b[axis] == point[axis]
                and min(a[1 - axis], point[1 - axis])
                <= b[1 - axis]
                <= max(a[1 - axis], point[1 - axis])
                for axis in (0, 1)
            )
            if straight:
                path.pop()
        path.append(point)
    return path


def _shortcuts(walk, boxes):
    """Cut visible corners before smoothing, keeping the two port approaches."""
    vertices = _turns(walk)
    path = [vertices[0]]
    i = 0
    while i < len(vertices) - 1:
        # Keep the search local on maps with many consecutive bends.
        j = min(i + 5, len(vertices) - 1)
        while j > i + 1:
            a, b = vertices[i], vertices[j]
            if first_collision([a, *_line_cubic(a, b)], boxes) is None:
                break
            j -= 1
        path.append(vertices[j])
        i = j
    return path


def _detour(a, b, walk, search_boxes, margin_boxes):
    shortcut = _turns([a, *_shortcuts(walk, search_boxes), b])
    original = _turns([a, *walk, b])
    for points in (shortcut, original):
        for radius in (18, 6, 1):
            candidate = _rounded(points, radius)
            if first_collision(candidate, margin_boxes) is None:
                return candidate, original
    return None, original


def route_edges(edges, positions, obstacles, columns):
    ports = _ports(edges, positions)
    search_boxes = {key: padded(box, CLEARANCE + 3) for key, box in obstacles.items()}
    clear = _obstacle_index(tuple(search_boxes.values()))
    rows = sorted(
        {
            yy + offset
            for box in obstacles.values()
            for yy, offset in ((box[1], -LEAD), (box[1] + box[3], LEAD))
        }
    )
    traffic = defaultdict(float)
    for edge in edges:
        eid = edge["id"]
        start, end = ports[eid, "source"], ports[eid, "target"]
        a, b = tuple(start["point"]), tuple(end["point"])
        margin_boxes = edge_obstacles(obstacles, edge["source_id"], edge["target_id"])
        step = max(LEAD, min(100, dist(a, b) * 0.4))
        direct = [a, _extend(a, start["side"], step), _extend(b, end["side"], step), b]
        if first_collision(direct, margin_boxes) is None:
            yield edge, start, end, direct
            continue

        lead_a = _extend(a, start["side"], LEAD)
        lead_b = _extend(b, end["side"], LEAD)
        for search in (_gutter_route, _corridor_route):
            walk = search(lead_a, lead_b, columns, rows, clear, traffic)
            if walk is None:
                continue
            path, corridors = _detour(a, b, walk, search_boxes.items(), margin_boxes)
            if path is None:
                continue
            for p, q in pairwise(corridors):
                axis = 0 if p[1] == q[1] else 1
                lo, hi = sorted((p[axis], q[axis]))
                for bucket in range(int(lo / TRAFFIC_SPAN), int(hi / TRAFFIC_SPAN) + 1):
                    bucket_start = bucket * TRAFFIC_SPAN
                    portion = min(hi, bucket_start + TRAFFIC_SPAN) - max(
                        lo, bucket_start
                    )
                    traffic[axis, p[1 - axis], bucket] += portion / TRAFFIC_SPAN
            yield edge, start, end, path
            break
        else:
            blocker = first_collision(direct, margin_boxes)
            raise LayoutError(
                "edge_node_collision",
                f"{eid}: no clear Bézier route around {blocker}",
                edge_id=eid,
                node_id=blocker,
            )
