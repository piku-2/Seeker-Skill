"""Run completion gate for graph, artifact and agent review."""

import base64
import xml.etree.ElementTree as ET
from pathlib import Path

from .illustrations import validate_search, validate_visual_review
from .model import ensure, validate
from .render import check_saved_artifacts
from .scene import _asset_image, check_scene
from .store import illustration_task


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
    count = sum("bbox" in asset for asset in scene.get("assets", []))
    result = {
        "status": "fail",
        "image_count": count,
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
            asset["id"]: asset for asset in scene.get("assets", []) if "bbox" in asset
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
            raw, mime, _ = _asset_image(asset["record"], run_dir)
            ensure(
                mime == asset["mime"]
                and raw == base64.b64decode(asset["image_data"], validate=True),
                f"illustrations: saved payload for {key} differs from the rendered image",
            )
        result["formats"] = check_saved_artifacts(scene, Path(run_dir) / "render")
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


def judge(data, scene, review, *, progress=None, run_dir=None):
    v = validate(data)
    g = check_scene(scene, data)
    illustrations = _illustration_qa(data, scene, review, progress, run_dir)
    st = classify(
        review.get("research_complete") is True,
        illustrations["status"] == "pass",
        review.get("semantic_pass") is True,
        review.get("artifact_pass") is True,
        review.get("independent_review", "not_run"),
    )
    return {
        "status": st,
        "graph": v,
        "scene": g,
        "illustrations": illustrations,
        "review": review,
    }
