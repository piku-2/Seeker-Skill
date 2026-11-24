"""Resolve evidence-backed chronology without turning graphic positions into dates."""

import math
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter
from statistics import median

KINDS = {
    "event": "出来事",
    "activity": "活動",
    "composition": "成立",
    "editing": "編集",
    "production": "制作",
    "discovery": "発見",
    "transmission": "伝承",
    "translation": "翻訳",
    "attestation": "資料による確認",
    "reception": "受容",
    "narrative": "物語内年代",
    "language": "言語層",
}


def is_text(value):
    return isinstance(value, str) and bool(value.strip())


def is_year(value):
    return type(value) in (int, float) and abs(value) <= 1e100 and math.isfinite(value)


def valid_bounds(lo, hi):
    return (
        (lo is None or is_year(lo))
        and (hi is None or is_year(hi))
        and (lo is None or hi is None or lo <= hi)
    )


def resolve(nodes, edges, claims, evidence):
    from .model import ensure

    def basis(ids, available, key):
        ensure(
            isinstance(ids, list)
            and bool(ids)
            and all(isinstance(cid, str) and cid in available for cid in ids),
            f"{key}: chronology requires related basis claims",
        )
        for cid in ids:
            claim = claims[cid]
            ensure(
                claim["adoption_status"] == "adopted"
                and claim["assessment"] in {"verified", "favored", "contested"}
                and any(
                    evidence[e]["access_status"] == "accessible"
                    for e in claim["supporting_evidence_ids"]
                ),
                f"{key}: chronology claim {cid} lacks accessible adopted support",
            )

    dates, pending = {}, {}
    for key, node in nodes.items():
        date = node["display_date"]
        mode, kind = date["mode"], date.get("kind")
        ensure(
            kind is None or (is_text(kind) and kind in KINDS),
            f"{key}: invalid chronology kind",
        )
        if mode == "unknown":
            if "reason" in date:
                ensure(is_text(date["reason"]), f"{key}: empty unknown date reason")
            dates[key] = {"mode": mode, "anchor_year": None, "kind": kind}
            continue
        if mode == "anchored":
            year = date["anchor_year"]
            ensure(
                kind != "narrative", f"{key}: narrative dates belong in date assertions"
            )
            relevant = [
                d
                for d in node["dates"]
                if kind is not None
                and d.get("kind") == kind
                and (d.get("earliest") is not None or d.get("latest") is not None)
                and d.get("status", "confirmed") == "confirmed"
            ]
            matching = [
                d
                for d in relevant
                if (d.get("earliest") is None or d["earliest"] <= year)
                and (d.get("latest") is None or year <= d["latest"])
            ]
            ensure(
                not relevant or matching,
                f"{key}: anchored year contradicts its date assertions",
            )
            lower = [d.get("earliest") for d in matching]
            upper = [d.get("latest") for d in matching]
            dates[key] = {
                "mode": mode,
                "anchor_year": year,
                "kind": kind,
                "earliest": min(lower)
                if lower and None not in lower
                else None
                if lower
                else year,
                "latest": max(upper)
                if upper and None not in upper
                else None
                if upper
                else year,
            }
            continue
        ensure(
            kind in KINDS and is_text(date.get("uncertainty")),
            f"{key}: estimated chronology requires a date kind and uncertainty",
        )
        ensure(
            is_text(date.get("anchor_reason")),
            f"{key}: estimated chronology requires an anchor reason",
        )
        if mode == "estimated":
            ensure(
                kind != "narrative",
                f"{key}: place narrative material by a historical phase",
            )
            lo, hi = date.get("earliest"), date.get("latest")
            ensure(
                valid_bounds(lo, hi) and (lo is not None or hi is not None),
                f"{key}: invalid estimated range",
            )
            basis(date.get("basis_claim_ids"), node["claim_ids"], key)
            dates[key] = _place(key, date, lo, hi)
        else:
            ensure(
                kind != "narrative" and "earliest" not in date and "latest" not in date,
                f"{key}: sequence bounds must derive from historical phases",
            )
            pending[key] = date

    orders = defaultdict(list)
    for key, edge in edges.items():
        if "chronology" not in edge:
            continue
        order = edge["chronology"]
        ensure(isinstance(order, dict), f"{key}: invalid chronological order")
        source, target = edge["source_id"], edge["target_id"]
        ensure(
            edge["adoption_status"] == "adopted"
            and source != target
            and all(
                nodes[n]["display_date"].get("kind") not in (None, "narrative")
                and order.get(field) == nodes[n]["display_date"].get("kind")
                for n, field in ((source, "source_kind"), (target, "target_kind"))
            ),
            f"{key}: chronology must name the two displayed historical phases",
        )
        basis(order.get("basis_claim_ids"), edge["claim_ids"], key)
        lower = dates.get(source, {}).get("earliest")
        upper = dates.get(target, {}).get("latest")
        ensure(
            valid_bounds(lower, upper),
            f"{key}: chronological order contradicts dated endpoints",
        )
        orders[source].append((key, target, False))
        orders[target].append((key, source, True))

    # Propagate evidence-backed lower bounds to detect contradictions even
    # across undated intermediates. Never use that propagation to invent dates.
    try:
        order = tuple(
            TopologicalSorter(
                {
                    n: [other for _, other, incoming in entries if incoming]
                    for n, entries in orders.items()
                }
            ).static_order()
        )
    except CycleError as exc:
        ensure(False, f"chronology: cyclic precedence {exc.args[1]}")
    inherited = {}
    for key in order:
        lo = dates.get(key, {}).get("earliest")
        for _, other, incoming in orders[key]:
            if incoming and inherited[other] is not None:
                lo = inherited[other] if lo is None else max(lo, inherited[other])
        ensure(
            valid_bounds(lo, dates.get(key, {}).get("latest")),
            f"{key}: chronological order contradicts dated endpoints",
        )
        inherited[key] = lo

    direct = dates.copy()
    for key, date in pending.items():
        ids = date.get("edge_ids")
        available = {eid: (other, incoming) for eid, other, incoming in orders[key]}
        ensure(
            isinstance(ids, list)
            and bool(ids)
            and all(isinstance(eid, str) and eid in available for eid in ids)
            and len(set(ids)) == len(ids),
            f"{key}: sequence placement requires explicit temporal edges",
        )
        lowers, uppers, earlier_anchors, later_anchors = [], [], [], []
        for eid in ids:
            other, incoming = available[eid]
            bound = "earliest" if incoming else "latest"
            reference = direct.get(other, {})
            ensure(
                reference.get(bound) is not None,
                f"{key}: {eid} does not provide an independently dated {bound} bound",
            )
            (lowers if incoming else uppers).append(reference[bound])
            (earlier_anchors if incoming else later_anchors).append(
                reference["anchor_year"]
            )
        lo, hi = max(lowers, default=None), min(uppers, default=None)
        ensure(valid_bounds(lo, hi), f"{key}: incompatible sequence bounds")
        position_lo = max(earlier_anchors, default=lo)
        position_hi = min(later_anchors, default=hi)
        ensure(
            valid_bounds(position_lo, position_hi),
            f"{key}: reference anchors reverse the displayed order",
        )
        default = median(earlier_anchors + later_anchors)
        if position_lo is not None:
            default = max(default, position_lo)
        if position_hi is not None:
            default = min(default, position_hi)
        placement = _place(key, date, lo, hi, default)
        year = placement["anchor_year"]
        ensure(
            (position_lo is None or position_lo <= year)
            and (position_hi is None or year <= position_hi),
            f"{key}: representative year reverses the displayed order",
        )
        dates[key] = placement
    return dates


def _place(key, date, lo, hi, default=None):
    from .model import ensure

    if default is None:
        default = (
            (lo + hi) / 2
            if lo is not None and hi is not None
            else lo
            if lo is not None
            else hi
        )
    default = round(default, 2)
    if lo is not None:
        default = max(default, lo)
    if hi is not None:
        default = min(default, hi)
    year = date.get("anchor_year", default)
    ensure(
        is_year(year) and (lo is None or year >= lo) and (hi is None or year <= hi),
        f"{key}: representative year outside its range",
    )
    return {
        "mode": date["mode"],
        "kind": date["kind"],
        "anchor_year": year,
        "earliest": lo,
        "latest": hi,
    }
