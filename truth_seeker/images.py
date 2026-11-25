"""A run-local image catalog and explicit, purpose-specific embedding decisions."""

import hashlib
import json
from pathlib import Path

from .illustrations import _ids, _index, _text, _url
from .model import ensure, is_id

PROFILES = ("distribution", "local")
IMAGE_TYPES = ("photo", "primary_document", "visual_source", "work_visual")
WORK_MEDIA = (
    "package",
    "screenshot",
    "key_visual",
    "poster",
    "cover",
    "promotional",
    "artwork",
    "other",
)


def _adopted_nodes(data):
    return {n["id"] for n in data["nodes"] if n["adoption_status"] == "adopted"}


def _validate_policy(policy, profile):
    ensure(
        isinstance(policy, dict)
        and policy.get("profile") == profile
        and profile in PROFILES
        and policy.get("basis")
        in ("license", "permission", "public_domain", "legal_exception")
        and _url(policy.get("source_url"))
        and _text(policy.get("conditions"))
        and (
            policy.get("basis") != "legal_exception"
            or _text(policy.get("jurisdiction"))
        ),
        "images: authorization requires an applicable source, basis and conditions",
    )


def validate_asset_use(asset):
    use = asset.get("image_use")
    if use is None and "image_use" not in asset:
        return
    ensure(
        asset.get("asset_type") in IMAGE_TYPES
        and isinstance(use, dict)
        and bool(use)
        and use.keys() <= set(PROFILES)
        and _text(asset.get("catalog_id"))
        and _text(asset.get("local_path"))
        and _text(asset.get("sha256"))
        and _url(asset.get("source_page_url")),
        f"{asset['id']}: invalid scoped image metadata",
    )
    for profile, proof in use.items():
        _validate_policy(proof, profile)
        ensure(_text(proof.get("reason")), f"{asset['id']}: missing use assessment")


def allowed(asset, profile):
    return "image_use" not in asset or profile in asset["image_use"]


def scoped_data(data, profile):
    ensure(profile in PROFILES, "images: invalid output profile")
    assets = []
    for asset in data["assets"]:
        if allowed(asset, profile):
            assets.append(asset)
        else:
            reference = {
                key: asset[key]
                for key in (
                    "id",
                    "schema_version",
                    "catalog_id",
                    "node_id",
                    "related_node_ids",
                    "source_page_url",
                    "caption",
                )
                if key in asset
            }
            assets.append(
                {
                    **reference,
                    "asset_type": "external_reference",
                    "caption": "画像参照（提供元） " + asset["caption"],
                }
            )
    return {**data, "assets": assets}


def scoped_report(report, data, profile):
    if not isinstance(report, dict):
        return report
    assets = {a["id"]: a for a in data["assets"] if "image_use" in a}
    candidates = []
    for item in report.get("candidates", []):
        asset_id = item.get("asset_id")
        asset = assets.get(asset_id) if _text(asset_id) else None
        if asset is None or item.get("decision") != "adopted":
            candidates.append(item)
        elif not allowed(asset, profile):
            candidates.append(
                {
                    **item,
                    "asset_id": None,
                    "decision": "rejected",
                    "rights": "restricted",
                    "rejection": "scope_unavailable",
                    "reason": "No approval is recorded for this output profile.",
                }
            )
        else:
            proof = asset["image_use"][profile]
            candidates.append({**item, "rights_url": proof["source_url"]})
    return {**report, "candidates": candidates}


def _catalog_file(run):
    return Path(run) / "image-library" / "catalog.json"


def read_catalog(run):
    path = _catalog_file(run)
    if not path.exists():
        return {"policies": [], "candidates": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _local_path(run, name):
    path = (run / name).resolve()
    ensure(path.is_relative_to(run), "images: file must be inside the run")
    return path


def _entry_metadata(item, policies, nodes):
    key = item["id"]
    refs = item.get("node_ids")
    ensure(
        is_id("asset_" + key, "asset_")
        and _ids(refs)
        and bool(refs)
        and len(set(refs)) == len(refs)
        and set(refs) <= nodes
        and item.get("media_kind") in IMAGE_TYPES
        and _url(item.get("source_page_url"))
        and ("media_url" not in item or _url(item["media_url"]))
        and (
            item["media_kind"] != "work_visual" or item.get("work_media") in WORK_MEDIA
        ),
        f"{key}: missing adopted subjects, classification or provider page",
    )
    authenticity = item.get("authenticity", "unreviewed")
    ensure(
        authenticity in ("unreviewed", "verified", "rejected"),
        f"{key}: invalid authenticity",
    )
    terms_status = item.get("terms_status", "unreviewed")
    ensure(
        terms_status in ("unreviewed", "license_stated", "terms_stated", "not_found")
        and (
            terms_status not in ("license_stated", "terms_stated")
            or (
                _ids(item.get("terms_urls"))
                and bool(item["terms_urls"])
                and all(_url(url) for url in item["terms_urls"])
            )
        ),
        f"{key}: invalid or unreferenced terms status",
    )
    reviewed = item.get("uses", {})
    ensure(
        isinstance(reviewed, dict) and reviewed.keys() <= set(PROFILES),
        f"{key}: invalid use reviews",
    )
    identity = ("provider", "identifier", "caption", "credit", "verification")
    if authenticity == "verified":
        ensure(
            all(_text(item.get(field)) for field in identity),
            f"{key}: missing identification or visual review",
        )
    for profile, verdict in reviewed.items():
        ensure(
            isinstance(verdict, dict)
            and verdict.get("decision") in ("approved", "withheld")
            and _text(verdict.get("reason")),
            f"{key}: invalid {profile} use review",
        )
        if verdict["decision"] == "approved":
            policy_id = verdict.get("policy_id")
            ensure(_text(policy_id) and policy_id in policies, f"{key}: missing policy")
            _validate_policy(policies[policy_id], profile)
            ensure(
                authenticity == "verified" and item.get("acquisition") == "verified",
                f"{key}: approval requires an acquired and identified image",
            )
    return item


def save_catalog(run, batch):
    """Index candidates in one transaction. Copy and inspect supplied files regardless of reuse state."""
    from .model import load_generation
    from .scene import inspect_image
    from .store import atomic_json, progress_lock, read_progress

    run = Path(run).resolve()
    ensure(isinstance(batch, dict), "images: expected a batch object")
    with progress_lock(run):
        generation = read_progress(run)["active_generation"]
        nodes = _adopted_nodes(load_generation(run / "snapshots" / generation))
        current = read_catalog(run)
        policies = _index(current["policies"], "id")
        for key, policy in _index(batch.get("policies", []), "id").items():
            _validate_policy(policy, policy.get("profile"))
            ensure(
                key not in policies or policies[key] == policy,
                f"images: use a new policy ID for changed terms ({key})",
            )
            policies[key] = policy
        entries = _index(current["candidates"], "id")
        for key, patch in _index(batch.get("candidates", []), "id").items():
            ensure(
                is_id("asset_" + key, "asset_"), f"images: invalid candidate ID {key}"
            )
            previous = entries.get(key, {})
            item = {**previous, **patch}
            path = patch.get("input_path")
            if path is not None:
                ensure(_text(path), f"{key}: invalid input path")
                source = _local_path(run, path)
                info = inspect_image(source)
                raw = source.read_bytes()
                ensure(
                    hashlib.sha256(raw).hexdigest() == info["sha256"],
                    f"{key}: input changed",
                )
                dest = _local_path(
                    run, Path("image-library") / "files" / info["sha256"]
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                if (
                    not dest.is_file()
                    or hashlib.sha256(dest.read_bytes()).hexdigest() != info["sha256"]
                ):
                    from tempfile import NamedTemporaryFile

                    with NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
                        tmp.write(raw)
                    Path(tmp.name).replace(dest)
                item.update(
                    info, local_path=str(dest.relative_to(run)), acquisition="verified"
                )
            elif "acquisition" in patch:
                ensure(
                    patch["acquisition"] in ("not_attempted", "failed")
                    and (
                        patch["acquisition"] != "failed"
                        or _text(patch.get("acquisition_note"))
                    ),
                    f"{key}: record the acquisition failure or import a local file",
                )
                for field in ("sha256", "size", "render_mime", "local_path"):
                    item.pop(field, None)
            else:
                item.setdefault("acquisition", "not_attempted")
            ensure(
                not ({"sha256", "size", "render_mime", "local_path"} & patch.keys()),
                f"{key}: image metadata must be measured by import",
            )
            item.pop("input_path", None)
            identity_fields = (
                "sha256",
                "source_page_url",
                "media_url",
                "provider",
                "identifier",
                "node_ids",
                "media_kind",
                "work_media",
                "caption",
                "credit",
            )
            if previous and any(
                item.get(field) != previous.get(field) for field in identity_fields
            ):
                if "authenticity" not in patch:
                    item["authenticity"] = "unreviewed"
                if "uses" not in patch:
                    item.pop("uses", None)
            entries[key] = _entry_metadata(item, policies, nodes)
        digests = [
            e["sha256"] for e in entries.values() if e.get("acquisition") == "verified"
        ]
        ensure(
            len(set(digests)) == len(digests),
            "images: identical image bytes under different candidate IDs",
        )
        catalog = {
            "policies": list(policies.values()),
            "candidates": list(entries.values()),
        }
        atomic_json(_catalog_file(run), catalog)
        return catalog_summary(catalog)


def catalog_summary(catalog):
    items = catalog["candidates"]
    return {
        "discovered": len(items),
        "acquired": sum(i.get("acquisition") == "verified" for i in items),
        "identified": sum(i.get("authenticity") == "verified" for i in items),
        "approved": {
            p: sum(
                i.get("uses", {}).get(p, {}).get("decision") == "approved"
                for i in items
            )
            for p in PROFILES
        },
        "not_attempted": sum(i.get("acquisition") == "not_attempted" for i in items),
        "failed": sum(i.get("acquisition") == "failed" for i in items),
        "embedding_pending": sum(
            i.get("acquisition") == "verified"
            and not any(
                verdict.get("decision") == "approved"
                for verdict in i.get("uses", {}).values()
            )
            for i in items
        ),
        "candidates": items,
    }


def catalog_assets(catalog, data):
    policies = _index(catalog["policies"], "id")
    nodes = _adopted_nodes(data)
    assets = []
    for item in catalog["candidates"]:
        _entry_metadata(item, policies, nodes)
        proofs = {
            profile: {**policies[verdict["policy_id"]], "reason": verdict["reason"]}
            for profile, verdict in item.get("uses", {}).items()
            if verdict["decision"] == "approved"
        }
        if not proofs:
            if item.get("authenticity") == "verified":
                assets.append(
                    {
                        "id": "asset_catalog_" + item["id"],
                        "schema_version": "0.5",
                        "catalog_id": item["id"],
                        "asset_type": "external_reference",
                        "node_id": item["node_ids"][0],
                        "related_node_ids": item["node_ids"][1:],
                        "source_page_url": item["source_page_url"],
                        "caption": "画像参照（提供元） " + item["caption"],
                    }
                )
            continue
        terms = " / ".join(f"{p}: {proof['conditions']}" for p, proof in proofs.items())
        assets.append(
            {
                "id": "asset_catalog_" + item["id"],
                "schema_version": "0.5",
                "catalog_id": item["id"],
                "asset_type": item["media_kind"],
                "node_id": item["node_ids"][0],
                "related_node_ids": item["node_ids"][1:],
                "source_url": item.get("media_url") or item["source_page_url"],
                "source_page_url": item["source_page_url"],
                "caption": item["caption"],
                "creator": item["credit"],
                "rights_note": terms,
                "local_path": item["local_path"],
                "sha256": item["sha256"],
                "image_use": proofs,
                **({"work_media": item["work_media"]} if "work_media" in item else {}),
            }
        )
    return assets


def check_catalog_assets(catalog, data):
    expected = {a["id"]: a for a in catalog_assets(catalog, data)}
    actual = {a["id"]: a for a in data["assets"] if "catalog_id" in a}
    ensure(actual == expected, "images: restage and merge current catalog approvals")
    return expected


def stage_catalog(run, stage):
    """Create a full staging generation. Neither the original snapshot nor its assets are modified."""
    from .model import FILES, dump_records, load_generation, validate
    from .store import progress_lock, read_progress

    run = Path(run).resolve()
    stage = Path(stage).resolve()
    ensure(
        stage.is_relative_to(run / "staging"),
        "images: stage into the run's staging directory",
    )
    with progress_lock(run):
        generation = read_progress(run)["active_generation"]
        src = (
            stage
            if (stage / "nodes.jsonl").is_file()
            else run / "snapshots" / generation
        )
        data = load_generation(src)
        catalog = read_catalog(run)
        assets = catalog_assets(catalog, data)
        data["assets"] = [a for a in data["assets"] if "catalog_id" not in a] + assets
        validate(data)
        stage.mkdir(parents=True, exist_ok=True)
        for kind in FILES:
            if src != stage or kind == "assets":
                dump_records(stage / (kind + ".jsonl"), data[kind])
        approved = {
            asset["catalog_id"]: asset for asset in assets if "image_use" in asset
        }
        drafts = [
            {
                "id": "image_" + item["id"],
                "catalog_id": item["id"],
                "node_ids": item["node_ids"],
                "media_kind": item["media_kind"],
                "source_url": item["source_page_url"],
                "provider": item.get("provider"),
                "identifier": item.get("identifier"),
                "acquisition": item["acquisition"],
                "decision": "pending",
                **(
                    {"asset_id": approved[item["id"]]["id"]}
                    if item["id"] in approved
                    else {}
                ),
            }
            for item in catalog["candidates"]
        ]
        return {
            "stage": str(stage),
            "catalog_assets": [a["id"] for a in assets],
            "candidate_drafts": drafts,
        }


def audit_catalog(catalog, data, report, run=None):
    """Require recorded candidates and the exact reviewed assets on the active generation."""
    expected = check_catalog_assets(catalog, data)
    decisions = _index(
        [i for i in report.get("candidates", []) if i.get("catalog_id")], "catalog_id"
    )
    searches = _index(report.get("searches", []), "id")
    ensure(
        decisions.keys() == {i["id"] for i in catalog["candidates"]},
        "images: search report must include every catalog candidate",
    )
    for item in catalog["candidates"]:
        key = item["id"]
        decision = decisions[key]
        asset = expected.get("asset_catalog_" + key)
        if asset and asset["asset_type"] == "external_reference":
            asset = None
        ensure(
            decision.get("media_kind") == item["media_kind"]
            and decision.get("source_url") == item["source_page_url"]
            and set(decision.get("node_ids", [])) == set(item["node_ids"])
            and decision.get("acquisition") == item["acquisition"]
            and (
                decision.get("decision") == "adopted"
                and decision.get("asset_id") == asset["id"]
                and decision.get("rights") == "scoped"
                if asset
                else decision.get("decision") == "rejected"
                and not decision.get("asset_id")
            ),
            f"images: missing or stale search decision for catalog candidate {key}",
        )
        if asset:
            urls = {proof["source_url"] for proof in asset["image_use"].values()}
            checked = searches[decision["search_id"]]["checked_urls"]
            ensure(
                urls <= set(checked) and decision.get("rights_url") in urls,
                f"images: record the applicable policy sources for {key}",
            )
        if run is not None and item["acquisition"] == "verified":
            path = _local_path(Path(run).resolve(), item["local_path"])
            ensure(
                hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"],
                f"{key}: collected image checksum differs",
            )
