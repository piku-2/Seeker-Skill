"""Dependencies and recovery for generation-bound output."""

import hashlib
import json
from pathlib import Path

from .execution import scene_metadata
from .images import check_catalog_assets, read_catalog, scoped_data
from .layout_quality import layout_metrics
from .model import ensure, load_generation
from .render import _check_bundle, emit, emit_one, fingerprint, scene_fingerprint
from .scene import _asset_image, _registered_assets, check_scene, layout
from .store import (
    atomic_json,
    progress_lock,
    read_manifest,
    read_progress,
    update_manifest,
    verify_generation,
)


def output_directory(run, profile):
    return Path(run) / ("render-local" if profile == "local" else "render")


def _inputs(run, progress, profile, data):
    generation = progress["active_generation"]
    rasters = {}
    adopted = {
        node["id"] for node in data["nodes"] if node["adoption_status"] == "adopted"
    }
    for item in _registered_assets(scoped_data(data, profile), adopted):
        if item.get("asset_type") in ("generated", "external_reference"):
            continue
        if item.get("local_path"):
            path = (run / item["local_path"]).resolve()
            ensure(
                path.is_relative_to(run.resolve()), "render: image is outside the run"
            )
            rasters[item["id"]] = fingerprint(path)
        else:
            raw, _, _ = _asset_image(item, run)
            rasters[item["id"]] = hashlib.sha256(raw).hexdigest()
    history = (read_manifest(run) or {}).get("execution_history")
    return {
        **(
            {
                "execution_sha256": hashlib.sha256(
                    json.dumps(history, sort_keys=True).encode()
                ).hexdigest()
            }
            if history
            else {}
        ),
        "run_id": progress["run_id"],
        "generation": generation,
        "profile": profile,
        "snapshot_sha256": fingerprint(run / "snapshots" / generation / "index.json"),
        "raster_sha256": rasters,
        "layout_contract": "explorer",
    }


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_scene(directory, inputs, data, *, pending=False, repair_format=None):
    scene = _read(directory / ("scene-pending.json" if pending else "scene.json"))
    receipt = _read(
        directory / ("layout_result.json" if pending else "render_result.json")
    )
    ensure(
        scene.get("graph_generation") == inputs["generation"],
        "render: saved scene belongs to a different generation",
    )
    ensure(
        scene.get("image_profile", "distribution") == inputs["profile"],
        "render: saved scene belongs to a different image profile",
    )
    ensure(
        scene.get("render_inputs") == inputs,
        "render: saved dependencies changed; recalculate the layout",
    )
    ensure(
        receipt.get("scene_sha256") == scene_fingerprint(scene),
        "render: saved scene is modified or has no receipt",
    )
    if not pending:
        if repair_format:
            name = f"map.{repair_format}"
            # A missing or damaged target can be repaired only while sibling
            # artifacts and the scene still match the same receipt.
            recorded = receipt["artifact_sha256"]
            present = {
                f"map.{kind}"
                for kind in ("pdf", "svg", "html")
                if (directory / f"map.{kind}").is_file()
            }
            ensure(
                present - {name} == set(recorded) - {name},
                "render: sibling artifact set differs; regenerate all formats",
            )
            for artifact, digest in recorded.items():
                if artifact != name:
                    ensure(
                        fingerprint(directory / artifact) == digest,
                        f"render: sibling artifact {artifact} is stale; regenerate all formats",
                    )
            ensure(
                fingerprint(directory / "scene.json") == receipt["scene_sha256"],
                "render: saved scene bytes differ",
            )
        else:
            _check_bundle(scene, directory, receipt)
    check_scene(scene, data)
    return scene


def render_run(
    run, profile, fmt, *, from_scene=False, importance=None, force_pdf_error=False
):
    run = Path(run)
    destination = output_directory(run, profile)
    ensure(
        importance is None or (fmt in ("all", "layout") and not from_scene),
        "render: importance changes require a fresh layout",
    )
    ensure(
        not (fmt == "layout" and from_scene),
        "render: --from-scene requires an output format",
    )
    with progress_lock(run):
        progress = read_progress(run)
        generation = progress["active_generation"]
        verify_generation(run, generation)
        data = load_generation(run / "snapshots" / generation)
        check_catalog_assets(read_catalog(run), data)
        inputs = _inputs(run, progress, profile, data)
        if fmt in ("pdf", "svg", "html"):
            scene = load_scene(destination, inputs, data, repair_format=fmt)
            result = emit_one(scene, destination, fmt, force_pdf_error=force_pdf_error)
        else:
            if from_scene:
                pending = (destination / "scene-pending.json").is_file()
                scene = load_scene(destination, inputs, data, pending=pending)
            else:
                scene = layout(
                    data,
                    progress["run_id"],
                    progress["query"],
                    importance,
                    run_dir=run,
                    image_profile=profile,
                    execution=scene_metadata(progress, read_manifest(run) or {}),
                )
                scene.update(graph_generation=generation, render_inputs=inputs)
            if fmt == "layout":
                atomic_json(destination / "scene-pending.json", scene)
                result = {
                    "status": "layout_saved",
                    "generation": generation,
                    "image_profile": profile,
                    "scene_sha256": scene_fingerprint(scene),
                    "scene_path": str(destination / "scene-pending.json"),
                    "metrics": layout_metrics(scene),
                }
                atomic_json(destination / "layout_result.json", result)
                return result
            result = emit(scene, destination, force_pdf_error)
            # A successfully published full render supersedes any draft.
            (destination / "scene-pending.json").unlink(missing_ok=True)
            (destination / "layout_result.json").unlink(missing_ok=True)
        if profile == "distribution" and (run / "manifest.json").is_file():
            artifacts = [
                "render/scene.json",
                *[f"render/{name}" for name in result["artifact_sha256"]],
            ]
            update_manifest(run, artifacts=artifacts)
        return result
