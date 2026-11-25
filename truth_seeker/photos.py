"""Trace documentary image opportunities, acquisitions and coverage.

Discovery, acquisition, identification, use assessment and embedding are separate.
Unknown reuse terms withhold embedding. They do not discard a useful image or
finish a search that never tried to collect the file.
"""

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path

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
VISUAL_KINDS = {
    "portrait",
    "object_photo",
    "screenshot",
    "package",
    "cover",
    "key_visual",
    "poster",
    "document",
    "manuscript",
    "inscription",
    "drawing",
    "advertisement",
    "archived_page",
    "other",
}
VISUAL_KINDS_BY_TYPE = {
    "person": ["portrait", "document", "archived_page"],
    "product": [
        "screenshot",
        "package",
        "cover",
        "key_visual",
        "poster",
        "object_photo",
        "advertisement",
    ],
    "technology": ["object_photo", "screenshot", "drawing", "document", "archived_page"],
    "invention": ["object_photo", "drawing", "document", "advertisement"],
    "object": ["object_photo", "document", "drawing"],
    "document": ["document", "manuscript", "inscription", "cover"],
    "event": ["object_photo", "poster", "document", "archived_page"],
    "place": ["object_photo", "document", "archived_page"],
    "organization": ["object_photo", "document", "archived_page", "advertisement"],
}
WORK_VISUALS = {"screenshot", "package", "cover", "key_visual"}
REQUIRED_WORK_VISUALS = {
    "product": WORK_VISUALS,
    "technology": {"object_photo", "screenshot", "drawing"},
    "document": {"document", "cover", "manuscript"},
}
ACCESS_BLOCKS = {"not_offered", "access_restricted"}
WEAK_EXCLUSION = re.compile(
    r"写真は不要|画像は不要|写真が不要|画像が不要|"
    r"現物写真で差を判別|パッケージや宣材の外観|宣材の外観|"
    r"単なる.{0,16}写真|別年代の.{0,16}写真だけ|"
    r"仕様の説明だから|年代の話だから|年代だから画像"
)


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
            "visual_kinds": list(
                VISUAL_KINDS_BY_TYPE.get(node["type"], ["document", "archived_page", "other"])
            ),
        }
        for node in data["nodes"]
        if node["adoption_status"] == "adopted"
        and (
            node["type"] in VISUAL_NODE_TYPES
            or node.get("importance", {}).get("level", 2) >= 3
            or node["id"] in related
        )
    ]


def weak_exclusion(reason):
    return bool(WEAK_EXCLUSION.search(reason or ""))


def _sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(
        c in "0123456789abcdef" for c in value
    )


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
    node_types = {node["id"]: node.get("type") for node in data["nodes"]}
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
        if decision == "not_suitable":
            kinds = target.get("visual_kinds_considered")
            ensure(
                isinstance(kinds, list)
                and bool(kinds)
                and set(kinds) <= VISUAL_KINDS,
                f"photos: {key} must record the visual kinds that were considered",
            )
            ensure(
                not weak_exclusion(target["reason"]),
                f"photos: weak visual exclusion {key}",
            )
            required = REQUIRED_WORK_VISUALS.get(node_types.get(key))
            ensure(
                required is None or bool(set(kinds) & required),
                f"photos: work visual not considered {key}",
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
        acquisition = item.get("acquisition")
        replacement = item.get("replacement_asset_id")
        ids = set(item["depicted_node_ids"])
        ensure(
            item["decision"] == "rejected"
            and not item.get("asset_id")
            and _text(item.get("reason")),
            f"photos: unresolved candidate {key}",
        )
        if (
            code == "rights_unavailable"
            and item["rights"] in ("unknown", "restricted")
            and acquisition != "verified"
        ):
            ensure(
                False,
                f"photos: {key} rights_unavailable does not replace acquisition",
            )
        ensure(
            (code == "unsuitable" and not item["useful"])
            or (
                code == "rights_unavailable"
                and item["rights"] in ("unknown", "restricted")
                and acquisition == "verified"
                and _sha256(item.get("sha256"))
                and _text(item.get("local_path"))
            )
            or (code == "scope_unavailable" and item["rights"] == "restricted")
            or (
                code == "acquisition_failed"
                and item["acquisition"] == "failed"
                and _text(item.get("alternatives_considered"))
            )
            or (
                code == "access_unavailable"
                and acquisition == "not_attempted"
                and item.get("acquisition_block") in ACCESS_BLOCKS
                and _text(item.get("acquisition_block_note"))
            )
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


def pipeline_state(item):
    """Separate an unfinished useful image from a recorded acquisition or reuse limit."""
    code = item.get("rejection")
    acquisition = item.get("acquisition")
    if item.get("decision") == "adopted":
        return "embedded"
    if item.get("useful") is False or code == "unsuitable":
        return "closed"
    if code == "redundant":
        return "closed"
    if code == "scope_unavailable" and item.get("rights") == "restricted":
        return "profile_withheld"
    if (
        code == "access_unavailable"
        and acquisition == "not_attempted"
        and item.get("acquisition_block") in ACCESS_BLOCKS
        and _text(item.get("acquisition_block_note"))
    ):
        return "access_blocked"
    if (
        code == "acquisition_failed"
        and acquisition == "failed"
        and item.get("attempts")
        and _text(item.get("alternatives_considered"))
    ):
        return "acquisition_failed"
    if (
        code == "rights_unavailable"
        and acquisition == "verified"
        and item.get("rights") in ("unknown", "restricted")
        and _sha256(item.get("sha256"))
        and _text(item.get("local_path"))
    ):
        return "rights_withheld"
    if (
        item.get("useful")
        and item.get("rights") in CLEARED_RIGHTS
        and acquisition == "verified"
    ):
        return "usable_unadopted"
    if item.get("useful"):
        return "unresolved"
    return "closed"


def candidate_groups(candidates):
    groups = defaultdict(set)
    for key, item in candidates.items():
        groups[pipeline_state(item)].add(key)
    return groups


def depicted_ids(candidates, keys):
    nodes = set()
    for key in keys:
        nodes.update(candidates[key].get("depicted_node_ids") or [])
    return nodes


def verify_held_files(candidates, run_dir):
    if run_dir is None:
        return
    run = Path(run_dir).resolve()
    for key, item in candidates.items():
        if item.get("acquisition") != "verified" or not item.get("local_path"):
            continue
        path = (run / item["local_path"]).resolve()
        ensure(
            path.is_relative_to(run) and path.is_file(),
            f"photos: {key} acquired image is missing",
        )
        ensure(
            _sha256(item.get("sha256"))
            and hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"],
            f"photos: {key} acquired image checksum differs",
        )


def validate_photo_review(review, coverage):
    ensure(
        isinstance(review, dict) and _text(review.get("findings")),
        "photos: semantic coverage review missing",
    )
    groups = candidate_groups(coverage["candidates"])
    unresolved = depicted_ids(coverage["candidates"], groups["unresolved"])
    withheld = depicted_ids(coverage["candidates"], groups["rights_withheld"])
    usable_unadopted = depicted_ids(
        coverage["candidates"], groups["usable_unadopted"]
    )
    for note in review.get("checks") or []:
        if (
            isinstance(note, dict)
            and note.get("unresolved_useful_candidate") is False
            and note.get("node_id") in unresolved
        ):
            ensure(
                False,
                "photos: unresolved useful candidate contradicts the review for "
                + str(note.get("node_id")),
            )
    adequate = review.get("coverage_adequate")
    if adequate is False:
        ensure(
            review.get("visual_coverage") == "constrained"
            and _text(review.get("constraint"))
            and not unresolved
            and not usable_unadopted
            and bool(withheld),
            "photos: visual constraint requires a withheld useful image",
        )
        mode = "constrained"
    else:
        ensure(adequate is True, "photos: semantic coverage review missing")
        ensure(
            not unresolved and not withheld and not usable_unadopted,
            "photos: withheld useful image is not full coverage",
        )
        mode = "adequate"
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
    for key, reason in excluded.items():
        ensure(not weak_exclusion(reason), f"photos: weak visual exclusion {key}")
    queries = {s["query"].strip().casefold() for s in coverage["searches"].values()}
    for key, note in notes.items():
        if note.get("unresolved_useful_candidate") is False and key in unresolved:
            ensure(
                False,
                f"photos: unresolved useful candidate contradicts the review for {key}",
            )
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
    return mode


def metrics(coverage, drawn_ids):
    shown = coverage["assets"].keys() & drawn_ids
    depicted = set().union(*(coverage["assets"][key] for key in shown))
    reps = defaultdict(set)
    for item in coverage["opportunities"]:
        if item["node_id"] in coverage["suitable"]:
            for rep in item["representative_ids"]:
                reps[rep].add(item["node_id"])
    candidates = coverage["candidates"]
    groups = candidate_groups(candidates)
    embedded = [
        item
        for item in candidates.values()
        if item.get("decision") == "adopted" and item.get("asset_id") in shown
    ]
    excluded = {
        key: target["reason"]
        for key, target in coverage["targets"].items()
        if target.get("decision") == "not_suitable"
    }
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
        "candidate_count": len(candidates),
        "useful_candidate_count": sum(
            item.get("useful") is True for item in candidates.values()
        ),
        "acquisition_not_attempted_count": sum(
            item.get("acquisition") == "not_attempted" for item in candidates.values()
        ),
        "acquisition_verified_count": sum(
            item.get("acquisition") == "verified" for item in candidates.values()
        ),
        "acquisition_failed_count": sum(
            item.get("acquisition") == "failed" for item in candidates.values()
        ),
        "identified_count": sum(
            item.get("acquisition") == "verified"
            and _text(item.get("provider"))
            and _text(item.get("identifier"))
            for item in candidates.values()
        ),
        "rights_pending_count": sum(
            candidates[key].get("rights") == "unknown" for key in groups["rights_withheld"]
        ),
        "not_embeddable_count": len(groups["access_blocked"])
        + sum(
            candidates[key].get("rights") == "restricted"
            for key in groups["rights_withheld"]
        ),
        "embedded_count": len(embedded),
        "embedded_by_kind": dict(Counter(item.get("media_kind") for item in embedded)),
        "candidates_by_kind": dict(
            Counter(item.get("media_kind") for item in candidates.values())
        ),
        "rights_cleared_count": sum(
            c["rights"] in CLEARED_RIGHTS for c in candidates.values()
        ),
        "not_suitable_count": len(excluded),
        "weak_exclusion_count": sum(weak_exclusion(reason) for reason in excluded.values()),
        "excluded_targets": excluded,
        "unresolved_candidate_ids": sorted(groups["unresolved"]),
        "unillustrated_targets": sorted(coverage["suitable"] - depicted),
        "rejections": dict(
            Counter(
                c["rejection"]
                for c in candidates.values()
                if c["decision"] == "rejected"
            )
        ),
    }
