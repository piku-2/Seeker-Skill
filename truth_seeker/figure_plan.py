"""Find structures worth reviewing, and validate saved figure decisions."""

from collections import defaultdict

from .chronology import is_text
from .figures import FIGURE_KINDS
from .model import ensure

CHECKS = {
    "comparisons",
    "chronology",
    "lineage",
    "composition",
    "hypotheses",
    "sources",
    "geography",
    "quantities",
    "relationships",
}


def opportunities(data):
    """Suggest reusable structures. Subject-specific opportunities remain an agent task."""
    nodes = {n["id"]: n for n in data["nodes"] if n["adoption_status"] == "adopted"}
    groups = defaultdict(set)
    links = defaultdict(set)

    for edge in data["edges"]:
        if edge["adoption_status"] != "adopted":
            continue
        ends = {edge["source_id"], edge["target_id"]}
        category = {
            "parallel": "comparisons",
            "opposition": "comparisons",
            "hypothesis": "hypotheses",
            "dependency": "composition",
        }.get(edge["relation_type"], "relationships")
        groups[category].update(ends)
        links[category].add(edge["id"])
        if "chronology" in edge:
            groups["lineage"].update(ends)
            links["lineage"].add(edge["id"])

    for key, node in nodes.items():
        if node["display_date"]["mode"] in ("anchored", "estimated"):
            groups["chronology"].add(key)
        if node["type"] == "document":
            groups["sources"].add(key)

    return [
        {
            "id": f"graph-{kind}",
            "category": kind,
            "node_ids": sorted(ids),
            "edge_ids": sorted(links[kind]),
        }
        for kind, ids in sorted(groups.items())
        if len(ids) >= 2
    ]


def validate_plan(plan, data):
    ensure(isinstance(plan, dict), "figure plan: missing opportunity review")
    checks = plan.get("checks")
    ensure(
        isinstance(checks, dict)
        and CHECKS <= checks.keys()
        and all(is_text(checks[key]) for key in CHECKS),
        "figure plan: record findings across the figure opportunity categories",
    )
    decisions = plan.get("decisions")
    ensure(isinstance(decisions, list), "figure plan: missing decisions")
    assets = {a["id"]: a for a in data["assets"] if a.get("asset_type") == "generated"}
    actual, covered = set(), set()
    suggested = {item["id"]: item for item in opportunities(data)}
    nodes = {n["id"] for n in data["nodes"] if n["adoption_status"] == "adopted"}
    for item in decisions:
        ensure(
            isinstance(item, dict) and is_text(item.get("id")),
            "figure plan: invalid candidate",
        )
        key = item["id"]
        refs = item.get("node_ids")
        ensure(
            key not in covered
            and item.get("kind") in FIGURE_KINDS
            and is_text(item.get("value"))
            and is_text(item.get("reason"))
            and isinstance(refs, list)
            and bool(refs)
            and all(isinstance(ref, str) and ref in nodes for ref in refs),
            f"figure plan: incomplete or duplicate candidate {key}",
        )
        related = set(refs)
        if key in suggested:
            ensure(
                len(related.intersection(suggested[key]["node_ids"])) >= 2,
                f"figure plan: candidate {key} must reference its graph opportunity",
            )
        covered.add(key)
        status, asset = item.get("decision"), item.get("asset_id")
        if status == "adopted":
            ensure(
                isinstance(asset, str) and asset in assets,
                f"figure plan: candidate {key} needs a registered figure",
            )
            record = assets[asset]
            owners = set(record.get("related_node_ids", [])) | {record.get("node_id")}
            ensure(
                related <= owners and item["kind"] == record["figure"]["kind"],
                f"figure plan: figure {asset} does not match candidate {key}",
            )
            actual.add(asset)
        else:
            ensure(
                status == "rejected" and asset is None,
                f"figure plan: candidate {key} is unresolved",
            )
    ensure(suggested.keys() <= covered, "figure plan: unreviewed graph opportunities")
    ensure(
        actual == assets.keys(), "figure plan: some generated figures have no decision"
    )
    ensure(
        bool(actual) or is_text(plan.get("zero_reason")),
        "figure plan: no reason for zero generated figures",
    )
    return len(decisions)


def validate_review(review, data):
    ensure(
        isinstance(review, dict)
        and review.get("coverage_adequate") is True
        and is_text(review.get("findings")),
        "figures: semantic review missing",
    )
    items = review.get("assets")
    expected = {a["id"] for a in data["assets"] if a.get("asset_type") == "generated"}
    ensure(
        isinstance(items, dict) and items.keys() == expected,
        "figures: review must cover every generated figure",
    )
    for key, item in items.items():
        ensure(
            isinstance(item, dict)
            and item.get("data_matches") is True
            and item.get("uncertainty_clear") is True
            and is_text(item.get("findings")),
            f"figures: {key} has unresolved evidence or uncertainty review",
        )
