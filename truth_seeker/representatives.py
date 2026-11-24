"""Check coverage records for representative subjects discovered by the agent."""

from .illustrations import _ids, _index, _text, _url
from .model import ensure

KIND = "representative_search"
TOPIC = "代表的対象の探索・採否"


def validate_representatives(report, data):
    ensure(
        isinstance(report, dict) and _text(report.get("scope")),
        "representatives: missing search scope",
    )
    searches = _index(report.get("searches"), "id")
    ensure(
        searches
        and all(
            _text(s.get("query")) and _text(s.get("result")) for s in searches.values()
        ),
        "representatives: search queries and results are required",
    )
    candidates = _index(report.get("candidates"), "id")
    ensure(bool(candidates), "representatives: no candidates considered")
    adopted = {
        n["id"] for n in data.get("nodes", []) if n["adoption_status"] == "adopted"
    }
    for key, item in candidates.items():
        references = item.get("search_ids")
        urls = item.get("source_urls")
        ensure(
            _text(item.get("label"))
            and _text(item.get("kind"))
            and _ids(references)
            and references
            and set(references) <= searches.keys()
            and isinstance(urls, list)
            and urls
            and all(_url(url) for url in urls),
            f"representatives: candidate {key} lacks identity or consulted sources",
        )
        decision = item.get("decision")
        ensure(
            decision in ("adopted", "rejected") and _text(item.get("reason")),
            f"representatives: unresolved candidate {key}",
        )
        node_ids = item.get("node_ids", [])
        ensure(
            _ids(node_ids)
            and set(node_ids) <= adopted
            and bool(node_ids) == (decision == "adopted"),
            f"representatives: {key} must link an adopted candidate to its adopted nodes",
        )
    return len(candidates)
