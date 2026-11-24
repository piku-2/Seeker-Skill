"""Run completion gate for graph, artifact and agent review."""

import base64
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

from .chronology import is_text
from .figure_plan import validate_plan, validate_review
from .illustrations import validate_search, validate_visual_review
from .images import (
    audit_catalog,
    catalog_summary,
    read_catalog,
    scoped_data,
    scoped_report,
)
from .model import ValidationError, ensure, validate
from .photos import (
    DOCUMENTARY_TYPES,
    metrics,
    validate_photo_review,
    validate_photo_search,
)
from .render import check_saved_artifacts
from .representatives import KIND, validate_representatives
from .scene import _asset_image, check_scene
from .store import illustration_task, research_task


def classify(
    research_complete, structural_pass, semantic_pass, artifact_pass, independent
):
    if not all([research_complete, structural_pass, semantic_pass, artifact_pass]):
        return "incomplete"
    if independent == "pass":
        return "complete"
    if independent == "unavailable":
        return "complete_limited_review"
    return "incomplete"


def _illustration_qa(data, scene, review, progress, run_dir):
    task = illustration_task(progress) if progress else None
    images = sum("bbox" in asset for asset in scene.get("assets", []))
    figures = sum("figure_bbox" in asset for asset in scene.get("assets", []))
    count = images + figures
    result = {
        "status": "fail",
        "image_count": images,
        "generated_figure_count": figures,
        "task_status": task["status"] if task else "missing",
    }
    try:
        ensure(
            task is not None and "illustration_report" in task,
            "illustrations: search has not been recorded",
        )
        ensure(task["status"] == "completed", "illustrations: search is unfinished")
        generation = progress["active_generation"]
        ensure(
            task.get("illustration_generation") == generation,
            "illustrations: search must be rechecked for the active generation",
        )
        asset_ids = validate_search(task["illustration_report"], data)
        drawn = {
            asset["id"]: asset
            for asset in scene.get("assets", [])
            if "bbox" in asset or "figure_bbox" in asset
        }
        ensure(
            drawn.keys() == asset_ids and len(drawn) == count,
            "illustrations: rendered images differ from approved assets",
        )
        ensure(
            review.get("generation") == generation
            and scene.get("graph_generation") == generation,
            "illustrations: rerender and review the active generation",
        )
        validate_visual_review(review.get("illustration_review"), asset_ids)
        ensure(
            run_dir is not None, "illustrations: saved artifacts are required for QA"
        )
        for key, asset in drawn.items():
            if "figure_bbox" in asset:
                continue
            raw, mime, _ = _asset_image(asset["record"], run_dir)
            ensure(
                mime == asset["mime"]
                and raw == base64.b64decode(asset["image_data"], validate=True),
                f"illustrations: saved payload for {key} differs from the rendered image",
            )
        directory = (
            "render-local" if scene.get("image_profile") == "local" else "render"
        )
        result["formats"] = check_saved_artifacts(scene, Path(run_dir) / directory)
    except (
        ValueError,
        OSError,
        KeyError,
        ET.ParseError,
        ImportError,
        RuntimeError,
    ) as exc:
        result["reason"] = str(exc)
    else:
        result["status"] = "pass"
        if not count:
            result["zero_reason"] = task["illustration_report"]["zero_reason"]
    return result


def _representative_qa(data, review, progress):
    task = research_task(progress, KIND) if progress else None
    try:
        ensure(
            task is not None and task["status"] == "completed",
            "representatives: search is unfinished or missing",
        )
        generation = progress["active_generation"]
        ensure(
            task.get("representative_generation") == generation
            and review.get("generation") == generation,
            "representatives: recheck search and review for the active generation",
        )
        count = validate_representatives(task.get("representative_report"), data)
        assessment = review.get("representative_review")
        ensure(
            isinstance(assessment, dict)
            and assessment.get("coverage_adequate") is True
            and isinstance(assessment.get("findings"), str)
            and assessment["findings"].strip(),
            "representatives: coverage has not passed review",
        )
    except (ValidationError, ValueError) as exc:
        return {"status": "fail", "reason": str(exc)}
    return {"status": "pass", "candidate_count": count}


def _chronology_qa(data, review):
    audit = review.get("chronology_review")
    nodes = [node for node in data["nodes"] if node["adoption_status"] == "adopted"]
    estimates = {
        n["id"] for n in nodes if n["display_date"]["mode"] in ("estimated", "sequence")
    }
    unknown = {
        n["id"]
        for n in nodes
        if n["display_date"]["mode"] == "unknown"
        and not n["display_date"].get("reason")
    }
    try:
        ensure(
            isinstance(audit, dict)
            and audit.get("phases_and_order_checked") is True
            and is_text(audit.get("findings")),
            "chronology: missing review of phases and precedence",
        )
        notes = audit.get("node_notes")
        ensure(
            isinstance(notes, dict)
            and estimates | unknown <= notes.keys()
            and all(is_text(notes[key]) for key in estimates | unknown),
            "chronology: missing evidence review or explanation for an unplaced node",
        )
    except ValidationError as exc:
        return {"status": "fail", "reason": str(exc)}
    return {"status": "pass", "estimated_or_sequence_count": len(estimates)}


def _figure_qa(data, review, progress, illustration):
    try:
        ensure(
            illustration["status"] == "pass", "figures: finish illustration QA first"
        )
        report = illustration_task(progress)["illustration_report"]
        count = validate_plan(report.get("figure_plan"), data)
        validate_review(review.get("figure_review"), data)
    except ValidationError as exc:
        return {"status": "fail", "reason": str(exc)}
    return {"status": "pass", "candidate_count": count}


def _photo_qa(data, scene, review, progress, illustrations, representatives):
    nonvector = [a for a in scene.get("assets", []) if "bbox" in a]
    drawn = [a for a in nonvector if a["record"].get("asset_type") in DOCUMENTARY_TYPES]
    result = {
        "status": "fail",
        "scene_documentary_images": len(drawn),
        "generated_figure_count": illustrations["generated_figure_count"],
    }
    try:
        ensure(
            illustrations["status"] == "pass",
            "photos: verify saved illustration outputs first",
        )
        result.update(
            {
                "photo_count": sum(
                    a["record"].get("asset_type") == "photo" for a in drawn
                ),
                "scan_count": sum(
                    a["record"].get("asset_type") == "primary_document" for a in drawn
                ),
                "other_documentary_count": sum(
                    a["record"].get("asset_type") == "visual_source" for a in drawn
                ),
                "work_visual_count": sum(
                    a["record"].get("asset_type") == "work_visual" for a in drawn
                ),
                "external_raster_count": len(nonvector) - len(drawn),
                "verified_documentary_images": len(drawn),
                "formats": dict.fromkeys(illustrations["formats"], len(drawn)),
            }
        )
        ensure(
            representatives["status"] == "pass",
            "photos: verify representative subjects first",
        )
        report = illustration_task(progress)["illustration_report"]
        reps = research_task(progress, KIND)["representative_report"]
        coverage = validate_photo_search(report, data, reps)
        drawn_ids = {a["id"] for a in drawn}
        result.update(metrics(coverage, drawn_ids))
        ensure(
            drawn_ids == coverage["assets"].keys(),
            "photos: registered images absent from output",
        )
        fingerprints = {
            hashlib.sha256(a["image_data"].encode()).digest() for a in drawn
        }
        ensure(
            len(fingerprints) == len(drawn),
            "photos: identical image payloads are registered twice",
        )
        validate_photo_review(review.get("photo_review"), coverage)
    except ValidationError as exc:
        result["reason"] = str(exc)
    else:
        result["status"] = "pass"
    return result


def _catalog_qa(data, scene, report, run_dir):
    if run_dir is None:
        return (
            {"status": "fail", "reason": "images: saved catalog required"}
            if any("catalog_id" in asset for asset in data["assets"])
            else {"status": "pass"}
        )
    catalog = read_catalog(run_dir)
    summary = catalog_summary(catalog)
    result = {key: value for key, value in summary.items() if key != "candidates"}
    result["rendered"] = sum(
        "catalog_id" in item.get("record", {}) and "bbox" in item
        for item in scene.get("assets", [])
    )
    result["rendered_by_kind"] = {
        kind: sum(
            a.get("record", {}).get("asset_type") == kind and "bbox" in a
            for a in scene.get("assets", [])
        )
        for kind in DOCUMENTARY_TYPES
    }
    try:
        audit_catalog(catalog, data, report, run_dir)
    except (OSError, ValidationError) as exc:
        return {"status": "fail", "reason": str(exc), **result}
    return {"status": "pass", **result}


def judge(data, scene, review, *, progress=None, run_dir=None):
    v = validate(data)
    g = check_scene(scene, data)
    profile = scene.get("image_profile", "distribution")
    task = illustration_task(progress) if progress else None
    report = (task or {}).get("illustration_report", {})
    catalog = _catalog_qa(data, scene, report, run_dir)
    if task is not None and any("image_use" in asset for asset in data["assets"]):
        projected_task = {
            **task,
            "illustration_report": scoped_report(report, data, profile),
        }
        progress = {
            **progress,
            "tasks": [projected_task if t is task else t for t in progress["tasks"]],
        }
    data = scoped_data(data, profile)
    illustrations = _illustration_qa(data, scene, review, progress, run_dir)
    representatives = _representative_qa(data, review, progress)
    chronology = _chronology_qa(data, review)
    figures = _figure_qa(data, review, progress, illustrations)
    photos = _photo_qa(data, scene, review, progress, illustrations, representatives)
    unfinished_tasks = [
        {key: task[key] for key in ("task_id", "topic", "status")}
        for task in (progress or {}).get("tasks", [])
        if task["status"] not in ("completed", "superseded")
    ]
    st = classify(
        review.get("research_complete") is True and not unfinished_tasks,
        all(
            check["status"] == "pass"
            for check in (
                illustrations,
                representatives,
                chronology,
                figures,
                photos,
                catalog,
            )
        ),
        review.get("semantic_pass") is True,
        review.get("artifact_pass") is True,
        review.get("independent_review", "not_run"),
    )
    return {
        "status": st,
        "graph": v,
        "unfinished_tasks": unfinished_tasks,
        "scene": g,
        "illustrations": illustrations,
        "representatives": representatives,
        "chronology": chronology,
        "figures": figures,
        "photos": photos,
        "image_catalog": catalog,
        "review": review,
    }
