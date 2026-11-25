"""Trace documentary image opportunities, acquisitions and coverage."""

from collections import Counter, defaultdict

from .illustrations import _ids, _index, _text, _url, searchable_assets
from .images import IMAGE_TYPES
from .model import ensure
from .representatives import subject_nodes

DOCUMENTARY_TYPES = IMAGE_TYPES
CLEARED_RIGHTS = {"cleared", "scoped"}
VISUAL_NODE_TYPES = {
    "person",
    "technology",
    "invention",
    "object",
    "organization",
    "event",
    "document",
    "place",
    "product",
}
STRATEGIES = {"institution", "catalog_id", "alternate_name", "image_search", "archive"}


def opportunities(data, representative_report):
    reps = defaultdict(set)
    related = set()
    for item in (representative_report or {}).get("candidates", []):
        if item.get("decision") == "adopted":
            related.update(item.get("node_ids", []))
            for key in subject_nodes(item):
                reps[key].add(item["id"])
    return [
        {
            "node_id": node["id"],
            "label": node["label"],
            "aliases": node["aliases"],
            "type": node["type"],
            "representative_ids": sorted(reps[node["id"]]),
        }
        for node in data["nodes"]
        if node["adoption_status"] == "adopted"
        and (
            node["type"] in VISUAL_NODE_TYPES
            or node.get("importance", {}).get("level", 2) >= 3
            or node["id"] in related
        )
    ]


def documentary_assets(data):
    visible = searchable_assets(data)
    return {
        a["id"]: a
        for a in data["assets"]
        if a["id"] in visible and a.get("asset_type") in DOCUMENTARY_TYPES
    }


def _searches(report):
    return {
        key: search
        for key, search in _index(report["searches"], "id").items()
        if search.get("purpose") == "photo"
    }


def validate_photo_search(report, data, representative_report=None):
    plan = report.get("photo_plan")
    ensure(
        isinstance(plan, dict) and _text(plan.get("scope")),
        "photos: missing search plan",
    )
    recorded_searches = _searches(report)
    for key, search in recorded_searches.items():
        ensure(
            _text(search.get("strategy"))
            and search["strategy"] in STRATEGIES
            and search.get("execution") in ("executed", "blocked"),
            f"photos: invalid search strategy or execution for {key}",
        )
    searches = {
        key: search
        for key, search in recorded_searches.items()
        if search["execution"] == "executed"
    }
    suggestions = opportunities(data, representative_report)
    required = {item["node_id"] for item in suggestions}
    adopted = {n["id"] for n in data["nodes"] if n["adoption_status"] == "adopted"}
    targets = _index(plan.get("targets"), "node_id")
    ensure(
        required <= targets.keys() <= adopted,
        "photos: missing required target assessments",
    )
    suitable = set()
    for key, target in targets.items():
        decision = target.get("decision")
        ids = target.get("search_ids", [])
        ensure(
            _text(target.get("reason"))
            and _ids(ids)
            and set(ids) <= recorded_searches.keys()
            and decision in ("search", "not_suitable"),
            f"photos: invalid target assessment {key}",
        )
        if decision == "search":
            ensure(
                _text(target.get("value"))
                and ids
                and any(searches[s]["checked_urls"] for s in ids if s in searches)
                and _text(target.get("outcome")),
                f"photos: no verifiable search for target {key}",
            )
            suitable.add(key)

    assets = documentary_assets(data)
    candidates, shown, found, usable, identities = {}, {}, set(), set(), set()
    for item in report["candidates"]:
        key, asset_id, kind = item["id"], item.get("asset_id"), item.get("media_kind")
        ensure(
            asset_id is None or _text(asset_id), f"photos: invalid asset ID for {key}"
        )
        if asset_id not in assets and kind not in DOCUMENTARY_TYPES:
            ensure(
                item["search_id"] not in recorded_searches
                or kind in ("diagram", "map", "chart", "external_reference"),
                f"photos: classify candidate {key} from a documentary search",
            )
            continue
        ids = item.get("depicted_node_ids")
        search_id = item["search_id"]
        ensure(
            kind in DOCUMENTARY_TYPES
            and _ids(ids)
            and bool(ids)
            and set(ids) <= suitable & set(item["node_ids"])
            and search_id in searches
            and all(search_id in targets[n]["search_ids"] for n in ids)
            and all(_text(item.get(field)) for field in ("provider", "identifier"))
            and type(item.get("useful")) is bool,
            f"photos: candidate {key} lacks classification, subject or catalog details",
        )
        identity = (
            item["provider"].strip().casefold(),
            item["identifier"].strip().casefold(),
        )
        ensure(identity not in identities, f"photos: duplicate catalog image {key}")
        identities.add(identity)
        acquisition = item.get("acquisition")
        ensure(
            acquisition in ("not_attempted", "failed", "verified"),
            f"photos: unresolved acquisition {key}",
        )
        cleared = item["rights"] in CLEARED_RIGHTS
        if cleared:
            ensure(
                _url(item.get("rights_url"))
                and _text(item.get("rights_note"))
                and item["rights_url"] in searches[search_id]["checked_urls"],
                f"photos: {key} lacks reviewed reuse terms",
            )
        if acquisition == "failed" or "attempts" in item:
            attempts = item.get("attempts")
            ensure(
                isinstance(attempts, list)
                and bool(attempts)
                and all(
                    isinstance(a, dict)
                    and _url(a.get("url"))
                    and _text(a.get("result"))
                    for a in attempts
                ),
                f"photos: {key} has no recorded acquisition attempt",
            )
        if item["useful"]:
            found.update(ids)
            if cleared and acquisition == "verified":
                usable.update(ids)

        if item["decision"] == "adopted":
            ensure(
                asset_id in assets
                and assets[asset_id]["asset_type"] == kind
                and cleared
                and acquisition == "verified"
                and item["useful"]
                and assets[asset_id].get("local_path")
                and assets[asset_id].get("sha256")
                and assets[asset_id].get("source_page_url") == item["source_url"]
                and (
                    assets[asset_id].get("rights_note") == item["rights_note"]
                    or assets[asset_id].get("license") == item["rights_note"]
                ),
                f"photos: {key} needs an acquired, checksummed image with matching provenance",
            )
            shown[asset_id] = set(ids)
        candidates[key] = item
    ensure(
        assets.keys() == shown.keys(),
        "photos: registered documentary images lack candidate records",
    )

    for key, item in candidates.items():
        if item["decision"] == "adopted":
            continue
        code = item.get("rejection")
        replacement = item.get("replacement_asset_id")
        ids = set(item["depicted_node_ids"])
        ensure(
            item["decision"] == "rejected"
            and not item.get("asset_id")
            and _text(item.get("reason")),
            f"photos: unresolved candidate {key}",
        )
        ensure(
            (code == "unsuitable" and not item["useful"])
            or (
                code == "rights_unavailable"
                and item["rights"] in ("unknown", "restricted")
            )
            or (code == "scope_unavailable" and item["rights"] == "restricted")
            or (code == "acquisition_failed" and item["acquisition"] == "failed")
            or (
                code == "redundant"
                and _text(replacement)
                and ids <= shown.get(replacement, set())
            ),
            f"photos: {key} leaves a usable candidate without a justified decision",
        )

    covered = set().union(*shown.values())
    for key in suitable - covered:
        lookups = [searches[s] for s in targets[key]["search_ids"] if s in searches]
        ensure(
            len({s["strategy"] for s in lookups}) >= 2
            and len({s["query"].strip().casefold() for s in lookups}) >= 2,
            f"photos: additional search needed for unillustrated target {key}",
        )

    return {
        "targets": targets,
        "suitable": suitable,
        "found": found,
        "usable": usable,
        "assets": shown,
        "searches": searches,
        "candidates": candidates,
        "opportunities": suggestions,
        "blocked_search_count": len(recorded_searches) - len(searches),
    }


def validate_photo_review(review, coverage):
    ensure(
        isinstance(review, dict)
        and review.get("coverage_adequate") is True
        and _text(review.get("findings")),
        "photos: semantic coverage review missing",
    )
    notes = _index(review.get("checks"), "node_id")
    suitable = coverage["suitable"]
    uncovered = suitable - set().union(*coverage["assets"].values())
    ensure(
        uncovered <= notes.keys() <= coverage["targets"].keys()
        and (not suitable or bool(suitable & notes.keys())),
        "photos: follow-up checks must cover unillustrated subjects",
    )
    excluded = review.get("excluded")
    ensure(
        isinstance(excluded, dict)
        and excluded.keys() == coverage["targets"].keys() - suitable
        and all(_text(reason) for reason in excluded.values()),
        "photos: review the reasons for excluding documentary targets",
    )
    queries = {s["query"].strip().casefold() for s in coverage["searches"].values()}
    for key, note in notes.items():
        ensure(
            _text(note.get("finding"))
            and _text(note.get("query"))
            and note["query"].strip().casefold() not in queries
            and _ids(note.get("checked_urls"))
            and note["checked_urls"]
            and all(_url(url) for url in note["checked_urls"])
            and note.get("unresolved_useful_candidate") is False,
            f"photos: additional review incomplete for {key}",
        )


def metrics(coverage, drawn_ids):
    shown = coverage["assets"].keys() & drawn_ids
    depicted = set().union(*(coverage["assets"][key] for key in shown))
    reps = defaultdict(set)
    for item in coverage["opportunities"]:
        if item["node_id"] in coverage["suitable"]:
            for rep in item["representative_ids"]:
                reps[rep].add(item["node_id"])
    return {
        "assessed_targets": len(coverage["targets"]),
        "suitable_targets": len(coverage["suitable"]),
        "searched_targets": len(coverage["suitable"]),
        "targets_with_useful_candidates": len(coverage["found"]),
        "targets_with_usable_candidates": len(coverage["usable"]),
        "depicted_targets": len(depicted),
        "suitable_representatives": len(reps),
        "searched_representatives": len(reps),
        "representatives_with_useful_candidates": sum(
            bool(v & coverage["found"]) for v in reps.values()
        ),
        "representatives_with_usable_candidates": sum(
            bool(v & coverage["usable"]) for v in reps.values()
        ),
        "depicted_representatives": sum(bool(v & depicted) for v in reps.values()),
        "search_count": len(coverage["searches"]),
        "blocked_search_count": coverage["blocked_search_count"],
        "candidate_count": len(coverage["candidates"]),
        "rights_cleared_count": sum(
            c["rights"] in CLEARED_RIGHTS for c in coverage["candidates"].values()
        ),
        "unillustrated_targets": sorted(coverage["suitable"] - depicted),
        "rejections": dict(
            Counter(
                c["rejection"]
                for c in coverage["candidates"].values()
                if c["decision"] == "rejected"
            )
        ),
    }
