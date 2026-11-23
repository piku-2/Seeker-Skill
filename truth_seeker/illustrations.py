"""Validate the evidence trail for an agent-led illustration search."""

from urllib.parse import urlsplit

from .model import ensure

TASK_KIND = "illustration_search"
TASK_TOPIC = "図版探索・権利確認・採用"


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _url(value):
    if not _text(value):
        return False
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _ids(value):
    return isinstance(value, list) and all(_text(item) for item in value)


def _index(records, field):
    ensure(
        isinstance(records, list), f"illustrations: expected list of {field} records"
    )
    result = {}
    for item in records:
        ensure(
            isinstance(item, dict) and _text(item.get(field)),
            f"illustrations: missing {field}",
        )
        key = item[field]
        ensure(key not in result, f"illustrations: duplicate ID {key}")
        result[key] = item
    return result


def searchable_assets(data):
    adopted = {
        n["id"] for n in data.get("nodes", []) if n["adoption_status"] == "adopted"
    }
    result = {}
    for asset in data.get("assets", []):
        owners = set(asset.get("related_node_ids", []))
        if asset.get("node_id"):
            owners.add(asset["node_id"])
        if asset.get("asset_type") != "external_reference" and (
            not owners or owners & adopted
        ):
            result[asset["id"]] = owners & adopted
    return result


def validate_search(report, data):
    """Check attribution and coverage of a final report, without judging its merits."""
    ensure(
        isinstance(report, dict) and _text(report.get("scope")),
        "illustrations: missing search scope",
    )
    searches = _index(report.get("searches"), "id")
    ensure(bool(searches), "illustrations: no executed search recorded")
    for search in searches.values():
        urls = search.get("checked_urls")
        ensure(
            _text(search.get("query"))
            and _text(search.get("result"))
            and isinstance(urls, list)
            and all(_url(url) for url in urls),
            f"illustrations: incomplete search {search['id']}",
        )

    targets = _index(report.get("targets"), "node_id")
    adopted = {
        n["id"]: n for n in data.get("nodes", []) if n["adoption_status"] == "adopted"
    }
    important = {
        key
        for key, node in adopted.items()
        if node.get("importance", {}).get("level", 2) >= 3
    }
    ensure(
        (not adopted or bool(targets))
        and important <= targets.keys() <= adopted.keys(),
        "illustrations: targets must cover important adopted nodes",
    )
    for key, target in targets.items():
        refs = target.get("search_ids")
        ensure(
            _ids(refs)
            and bool(refs)
            and set(refs) <= searches.keys()
            and _text(target.get("reason"))
            and _text(target.get("outcome")),
            f"illustrations: unfinished target {key}",
        )

    candidates = _index(report.get("candidates"), "id")
    required = searchable_assets(data)
    assets = {asset["id"]: asset for asset in data.get("assets", [])}
    evidence = {item["id"]: item for item in data.get("evidence", [])}
    registered = set()
    for key, item in candidates.items():
        owners, lookup = item.get("node_ids"), item.get("search_id")
        ensure(
            _ids(owners)
            and bool(owners)
            and set(owners) <= targets.keys()
            and _text(lookup)
            and lookup in searches
            and all(lookup in targets[owner]["search_ids"] for owner in owners),
            f"illustrations: candidate {key} has no matching target or search",
        )
        source = item.get("source_url")
        ensure(
            _url(source)
            and source in searches[lookup]["checked_urls"]
            and _text(item.get("reason")),
            f"illustrations: candidate {key} lacks source or decision rationale",
        )
        decision, rights = item.get("decision"), item.get("rights")
        ensure(
            decision in ("adopted", "rejected")
            and rights in ("cleared", "unknown", "restricted"),
            f"illustrations: unresolved candidate {key}",
        )
        if decision == "adopted":
            asset_id = item.get("asset_id")
            ensure(
                rights == "cleared"
                and _text(item.get("rights_note"))
                and _url(item.get("rights_url")),
                f"illustrations: candidate {key} lacks verified reuse information",
            )
            ensure(
                _text(asset_id)
                and asset_id in required
                and asset_id not in registered
                and set(owners) == required[asset_id],
                f"illustrations: candidate {key} differs from its registered asset or owners",
            )
            record = assets[asset_id]
            sources = [record.get("source_url"), record.get("source_page_url")]
            sources.extend(
                evidence[eid]["source_url"]
                for eid in record.get("evidence_ids", [])
                if eid in evidence
            )
            ensure(
                source in sources
                and _text(record.get("caption") or record.get("title"))
                and any(
                    _text(record.get(field)) for field in ("license", "rights_note")
                ),
                f"illustrations: asset {asset_id} lacks matching source, caption or rights metadata",
            )
            registered.add(asset_id)

    ensure(
        registered == required.keys(),
        "illustrations: registered images missing candidate decisions",
    )
    if not registered:
        ensure(
            _text(report.get("zero_reason")),
            "illustrations: no reason for zero images recorded",
        )
    return registered


def validate_visual_review(review, asset_ids):
    """Require explicit human/agent observations for each illustration and the topic."""
    ensure(
        isinstance(review, dict)
        and review.get("coverage_adequate") is True
        and _text(review.get("findings")),
        "illustrations: visual coverage has not passed review",
    )
    items = _index(review.get("assets"), "asset_id")
    ensure(
        items.keys() == asset_ids, "illustrations: review does not cover every image"
    )
    for key, item in items.items():
        ensure(
            all(
                item.get(field) is True
                for field in (
                    "caption_matches",
                    "historically_appropriate",
                    "rights_confirmed",
                )
            )
            and _text(item.get("explanatory_value")),
            f"illustrations: image {key} has not passed semantic review",
        )
