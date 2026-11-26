"""Summarize saved progress without assigning a speculative completion percentage."""

import json
from collections import Counter
from pathlib import Path

from .images import catalog_summary, read_catalog
from .model import load_generation
from .photos import DOCUMENTARY_TYPES
from .render import _check_bundle
from .render_run import _inputs
from .store import (
    illustration_task,
    latest_good,
    read_manifest,
    read_progress,
    verify_generation,
)


def _saved_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _output_status(base, progress, profile, data):
    generation = progress["active_generation"]
    folder = base / ("render-local" if profile == "local" else "render")
    result = _saved_json(folder / "render_result.json") or {}
    scene = _saved_json(folder / "scene.json")
    current = bool(
        scene
        and scene.get("graph_generation") == generation
        and scene.get("image_profile", "distribution") == profile
    )
    state = "current_generation" if current else "stale" if scene else "missing"
    integrity_error = None
    if current and "render_inputs" in scene:
        try:
            _check_bundle(scene, folder, result)
            if not result or "scene_sha256" not in result:
                raise ValueError("missing render receipt")
            if not data or scene["render_inputs"] != _inputs(
                base, progress, profile, data
            ):
                state = "stale"
        except (OSError, ValueError, KeyError) as exc:
            state = "damaged"
            integrity_error = str(exc)
    qa_root = base / "qa"
    qa_path = "local/result.json" if profile == "local" else "result.json"
    qa = _saved_json(qa_root / generation / qa_path)
    if qa is None:
        for folder_path in sorted(qa_root.glob("g[0-9]*"), reverse=True):
            qa = _saved_json(folder_path / qa_path)
            if qa is not None:
                break
    if qa is None and profile != "local":
        qa = _saved_json(qa_root / "illustration-result.json")
    rasters = (
        [item for item in scene.get("assets", []) if "bbox" in item] if current else []
    )
    return {
        "state": state,
        "integrity_error": integrity_error,
        "formats_present": [
            fmt for fmt in ("pdf", "html", "svg") if (folder / f"map.{fmt}").is_file()
        ],
        "published_generation": scene.get("graph_generation") if scene else None,
        "generated_at": scene.get("execution", {}).get("generated_at")
        if scene
        else None,
        "published_at": result.get("published_at"),
        "pending_layout": (folder / "scene-pending.json").is_file(),
        "placed_images": len(rasters) if current else None,
        "generated_figures": sum("figure_bbox" in a for a in scene.get("assets", []))
        if current
        else None,
        "pdf_warning": result.get("pdf_fallback_reason")
        or result.get("pdf_render_error"),
        "qa_matches_published_scene": bool(
            qa
            and qa.get("scene_sha256")
            and qa["scene_sha256"] == result.get("scene_sha256")
        )
        and state == "current_generation",
        "qa": qa,
        "qa_matches_active_generation": bool(qa and qa.get("generation") == generation),
    }


def status(run):
    base = Path(run)
    progress = read_progress(base)
    generation = progress["active_generation"]
    manifest = read_manifest(base) or {}
    try:
        counts = verify_generation(base, generation)
        recoverable = generation
    except (OSError, KeyError, ValueError):
        counts = None
        try:
            recoverable = latest_good(base)
        except ValueError:
            recoverable = None
    data = load_generation(base / "snapshots" / generation) if counts else None
    illustration = illustration_task(progress)
    report = (illustration or {}).get("illustration_report", {})
    candidates = [
        c
        for c in report.get("candidates", [])
        if c.get("media_kind") in DOCUMENTARY_TYPES
    ]
    catalog = catalog_summary(read_catalog(base))
    linked = {c["catalog_id"] for c in candidates if c.get("catalog_id")}
    unlinked = [c for c in catalog["candidates"] if c["id"] not in linked]
    # Use catalog acquisition state for linked candidates even if the task report is a draft.
    current_acquisitions = {c["id"]: c["acquisition"] for c in catalog["candidates"]}
    acquired = sum(
        current_acquisitions.get(c.get("catalog_id"), c.get("acquisition"))
        == "verified"
        for c in candidates
    )
    acquired += sum(c["acquisition"] == "verified" for c in unlinked)
    tasks = progress.get("tasks", [])
    task_counts = dict.fromkeys(
        (
            "completed",
            "pending",
            "running",
            "retryable",
            "blocked",
            "failed",
            "superseded",
        ),
        0,
    )
    task_counts.update(Counter(t["status"] for t in tasks))
    interrupted = [
        t["task_id"]
        for t in tasks
        if t["status"] == "retryable"
        and (t.get("last_error") or "").startswith("Interrupted;")
    ]
    unresolved = [
        {
            key: t.get(key)
            for key in ("task_id", "topic", "priority", "status", "last_error")
        }
        for t in tasks
        if t["status"] not in ("completed", "superseded")
    ]
    outputs = {
        profile: _output_status(base, progress, profile, data)
        for profile in ("distribution", "local")
    }
    return {
        **progress,
        "summary": {
            "execution_history": manifest.get("execution_history", []),
            "last_updated_at": max(
                [
                    value
                    for value in (
                        progress.get("checkpoint_at"),
                        manifest.get("updated_at"),
                        progress.get("created_at"),
                    )
                    if value
                ]
                + [t["updated_at"] for t in tasks if t.get("updated_at")],
                default=None,
            ),
            "tasks": {"total": len(tasks), **task_counts},
            "interrupted_task_ids": interrupted,
            "unresolved_tasks": sorted(unresolved, key=lambda t: -(t["priority"] or 0)),
            "unresolved_image_candidates": [
                c["id"] for c in candidates if c.get("decision") == "pending"
            ]
            + [c["id"] for c in unlinked],
            "adopted_nodes": len(counts["adopted_node_ids"]) if counts else None,
            "adopted_edges": len(counts["adopted_edge_ids"]) if counts else None,
            "evidence_records": len(data["evidence"]) if data else None,
            "distinct_source_urls": len(
                {e["source_url"] for e in data["evidence"] if e.get("source_url")}
            )
            if data
            else None,
            "image_candidates": len(candidates) + len(unlinked),
            "acquired_images": acquired,
            "outputs": outputs,
            "resume_from_generation": recoverable,
            "resume_command": ["python", "-m", "truth_seeker", "resume", str(run)]
            if recoverable
            else None,
        },
    }
