"""Strict, dependency-light record validation."""

from __future__ import annotations

import json
import math
from pathlib import Path

VERSION = "0.5"
ADOPTIONS = {"candidate", "adopted", "pending", "rejected"}
ASSESSMENTS = {"verified", "favored", "contested", "hypothetical", "unverified"}
NODE_TYPES = {
    "concept",
    "technology",
    "invention",
    "person",
    "organization",
    "product",
    "event",
    "culture",
    "document",
    "object",
    "place",
    "other",
}
RELATIONS = {
    "dependency",
    "influence",
    "context",
    "confluence",
    "parallel",
    "opposition",
    "hypothesis",
    "other",
}
FILES = ("nodes", "edges", "claims", "evidence", "merge_history", "assets")


class ValidationError(ValueError):
    pass


def ensure(cond, msg):
    if not cond:
        raise ValidationError(msg)


class LayoutError(ValidationError):
    def __init__(self, kind, message, **details):
        self.issues = [
            {"type": kind, "severity": "error", "message": message, **details}
        ]
        super().__init__(f"{kind}: {message}")


def validate_importance(importance, node_id):
    ensure(isinstance(importance, dict), f"{node_id}: importance must be an object")
    level = importance.get("level")
    ensure(
        type(level) is int and 1 <= level <= 4,
        f"{node_id}: importance level must be 1–4",
    )
    for key in ("reason", "source"):
        ensure(
            isinstance(importance.get(key), str) and bool(importance[key].strip()),
            f"{node_id}: importance requires {key}",
        )


def records(path: Path):
    if not path.exists():
        return []
    result = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValidationError(f"{path.name}:{n}: blank JSONL line")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValidationError(f"{path.name}:{n}: {e}") from e
        ensure(isinstance(value, dict), f"{path.name}:{n}: expected object")
        result.append(value)
    return result


def dump_records(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(v, ensure_ascii=False, sort_keys=True) + "\n" for v in values
        ),
        encoding="utf-8",
    )


def required(record, keys, group):
    for k in keys:
        ensure(k in record, f"{group}:{record.get('id', '?')}: missing {k}")


def is_id(x, prefix):
    return isinstance(x, str) and x.startswith(prefix) and len(x) > len(prefix)


def validate(records_by_type):
    data = {k: list(records_by_type.get(k, [])) for k in FILES}
    prefix = {
        "nodes": "node_",
        "edges": "edge_",
        "claims": "claim_",
        "evidence": "evi_",
        "merge_history": "merge_",
        "assets": "asset_",
    }
    for kind, items in data.items():
        seen = set()
        for r in items:
            required(r, ["id", "schema_version"], kind)
            ensure(is_id(r["id"], prefix[kind]), f"{kind}: bad id {r['id']}")
            ensure(r["schema_version"] == VERSION, f"{kind}:{r['id']}: schema_version")
            ensure(r["id"] not in seen, f"{kind}: duplicate {r['id']}")
            seen.add(r["id"])
    nodes = {r["id"]: r for r in data["nodes"]}
    edges = {r["id"]: r for r in data["edges"]}
    claims = {r["id"]: r for r in data["claims"]}
    evidence = {r["id"]: r for r in data["evidence"]}
    for n in nodes.values():
        required(
            n,
            [
                "type",
                "label",
                "aliases",
                "summary",
                "dates",
                "display_date",
                "claim_ids",
                "adoption_status",
                "adoption_reason",
                "provenance",
            ],
            "nodes",
        )
        ensure(n["type"] in NODE_TYPES, f"{n['id']}: bad node type")
        ensure(
            isinstance(n["label"], str) and bool(n["label"].strip()),
            f"{n['id']}: blank label",
        )
        ensure(
            isinstance(n["aliases"], list) and isinstance(n["dates"], list),
            f"{n['id']}: bad arrays",
        )
        ensure(n["adoption_status"] in ADOPTIONS, f"{n['id']}: adoption")
        ensure(
            isinstance(n["claim_ids"], list) and set(n["claim_ids"]) <= set(claims),
            f"{n['id']}: missing claim",
        )
        ensure(isinstance(n["display_date"], dict), f"{n['id']}: display_date")
        if "importance" in n:
            validate_importance(n["importance"], n["id"])
        d = n["display_date"]
        ensure(d.get("mode") in ("anchored", "unknown"), f"{n['id']}: bad display mode")
        if d["mode"] == "anchored":
            ensure(
                type(d.get("anchor_year")) in (int, float)
                and math.isfinite(d["anchor_year"]),
                f"{n['id']}: anchor_year",
            )
        for dt in n["dates"]:
            ensure(isinstance(dt, dict), f"{n['id']}: date assertion")
            a, b = dt.get("earliest"), dt.get("latest")
            ensure(
                (a is None and b is None)
                or (
                    isinstance(a, (int, float))
                    and isinstance(b, (int, float))
                    and a <= b
                ),
                f"{n['id']}: date bounds",
            )
            ensure(
                set(dt.get("basis_claim_ids", [])) <= set(claims),
                f"{n['id']}: date claim refs",
            )
    for e in edges.values():
        required(
            e,
            [
                "source_id",
                "target_id",
                "relation_type",
                "direction_meaning",
                "label",
                "claim_ids",
                "adoption_status",
                "adoption_reason",
                "provenance",
            ],
            "edges",
        )
        ensure(
            e["source_id"] in nodes and e["target_id"] in nodes,
            f"{e['id']}: dangling endpoint",
        )
        if "importance" in e:
            validate_importance(e["importance"], e["id"])
        ensure(e["relation_type"] in RELATIONS, f"{e['id']}: relation")
        ensure(e["adoption_status"] in ADOPTIONS, f"{e['id']}: adoption")
        ensure(set(e["claim_ids"]) <= set(claims), f"{e['id']}: missing claim")
        if e["adoption_status"] == "adopted":
            ensure(
                nodes[e["source_id"]]["adoption_status"] == "adopted"
                and nodes[e["target_id"]]["adoption_status"] == "adopted",
                f"{e['id']}: adopted edge with nonadopted node",
            )
    for c in claims.values():
        required(
            c,
            [
                "statement",
                "subject_refs",
                "supporting_evidence_ids",
                "contradicting_evidence_ids",
                "assessment",
                "adoption_status",
                "adoption_reason",
                "verification",
                "related_claim_ids",
                "provenance",
            ],
            "claims",
        )
        ensure(c["assessment"] in ASSESSMENTS, f"{c['id']}: assessment")
        ensure(c["adoption_status"] in ADOPTIONS, f"{c['id']}: adoption")
        for r in c["subject_refs"]:
            ensure(r.get("kind") in ("node", "edge"), f"{c['id']}: bad subject kind")
            ensure(
                r.get("id") in (nodes if r["kind"] == "node" else edges),
                f"{c['id']}: bad subject",
            )
        for key in ("supporting_evidence_ids", "contradicting_evidence_ids"):
            ensure(
                set(c[key]) <= set(evidence), f"{c['id']}: missing evidence in {key}"
            )
        ensure(set(c["related_claim_ids"]) <= set(claims), f"{c['id']}: related claim")
        if c["assessment"] == "verified":
            ensure(
                bool(c["supporting_evidence_ids"]),
                f"{c['id']}: verified without supporting evidence",
            )
    for ev in evidence.values():
        required(
            ev,
            [
                "title",
                "source_url",
                "locator",
                "excerpt_or_summary",
                "source_type",
                "access_status",
                "accessed_at",
                "independence_group",
                "rights_note",
                "provenance",
            ],
            "evidence",
        )
        ensure(
            ev["source_type"]
            in {"primary", "academic", "institutional", "secondary", "other"},
            f"{ev['id']}: source type",
        )
        ensure(
            ev["access_status"] in {"accessible", "inaccessible", "partial", "unknown"},
            f"{ev['id']}: access",
        )
    for m in data["merge_history"]:
        required(m, ["from_id", "to_id", "reason", "generation"], "merge_history")
    for asset in data["assets"]:
        kind = asset.get("asset_type")
        if kind in (
            "photo",
            "primary_document",
            "diagram",
            "map",
            "chart",
            "generated",
        ):
            key = asset["id"]
            owners = asset.get("related_node_ids", [])
            ensure(
                isinstance(owners, list)
                and all(isinstance(owner, str) and owner in nodes for owner in owners)
                and ("node_id" not in asset or asset["node_id"] in nodes),
                f"{key}: missing illustration node",
            )
            refs = asset.get("evidence_ids", [])
            ensure(
                isinstance(refs, list)
                and all(isinstance(ref, str) and ref in evidence for ref in refs),
                f"{key}: missing illustration evidence",
            )
            ensure(
                any(
                    isinstance(asset.get(field), str)
                    and asset[field].startswith(("https://", "http://"))
                    for field in ("source_page_url", "source_url")
                )
                or any(
                    isinstance(evidence[eid].get("source_url"), str)
                    and evidence[eid]["source_url"].startswith(("https://", "http://"))
                    for eid in refs
                ),
                f"{key}: illustration requires an identifiable source",
            )
            ensure(
                any(
                    isinstance(asset.get(field), str) and asset[field].strip()
                    for field in ("rights_note", "license")
                ),
                f"{key}: illustration requires rights metadata",
            )
            ensure(
                isinstance(asset.get("caption") or asset.get("title"), str)
                and bool((asset.get("caption") or asset.get("title")).strip()),
                f"{key}: illustration requires a caption",
            )
            if kind == "generated":
                from .figures import validate_figure

                validate_figure(asset, nodes, edges, evidence, claims)
    return {
        "counts": {k: len(v) for k, v in data.items()},
        "adopted_node_ids": sorted(
            k for k, v in nodes.items() if v["adoption_status"] == "adopted"
        ),
        "adopted_edge_ids": sorted(
            k for k, v in edges.items() if v["adoption_status"] == "adopted"
        ),
    }


def load_generation(path):
    return {k: records(Path(path) / (k + ".jsonl")) for k in FILES}


def provenance(run_id="example", task_id="task_initial", origin="main"):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    return dict(
        run_id=run_id,
        source_task_id=task_id,
        created_at=now,
        updated_at=now,
        origin=origin,
    )
