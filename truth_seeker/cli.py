"""Command-line helper used by agent Skills. Research itself remains agent-led."""

import argparse
import json
import sys
from pathlib import Path

from .model import LayoutError, ValidationError, load_generation, validate
from .qa import judge
from .repair import RepairError, apply, inspect, staged_check
from .scene import check_font_coverage
from .store import (
    add_execution,
    add_task,
    atomic_json,
    commit_generation,
    create_run,
    progress_lock,
    read_progress,
    resume,
    save_illustrations,
    save_representatives,
    update_manifest,
    update_task,
    verify_generation,
)


def output(x):
    print(json.dumps(x, ensure_ascii=False, indent=2, default=str))


def build_parser():
    p = argparse.ArgumentParser(
        prog="truth-seeker-tool",
        description="Truth Seeker v1.1 engine; AI agent handles research and reasoning",
    )
    s = p.add_subparsers(dest="command", required=True)
    a = s.add_parser("init")
    a.add_argument("topic")
    a.add_argument("--runs", default="runs")
    a.add_argument("--model")
    a.add_argument("--harness")
    a = s.add_parser("task-add")
    a.add_argument("run")
    a.add_argument("topic")
    a.add_argument("--priority", type=int, default=0)
    a = s.add_parser("task-update")
    a.add_argument("run")
    a.add_argument("task_id")
    a.add_argument(
        "status",
        choices=(
            "pending",
            "running",
            "retryable",
            "blocked",
            "completed",
            "superseded",
        ),
    )
    a = s.add_parser(
        "illustrations-save",
        help="Checkpoint the illustration search in the current run",
    )
    a.add_argument("run")
    a.add_argument("report", type=Path)
    a.add_argument(
        "--complete", action="store_true", help="Finalize against the active generation"
    )
    a = s.add_parser(
        "representatives-save", help="Checkpoint representative-subject research"
    )
    a.add_argument("run")
    a.add_argument("report", type=Path)
    a.add_argument("--complete", action="store_true")
    a = s.add_parser("status")
    a.add_argument("run")
    a = s.add_parser("resume")
    a.add_argument("run")
    a.add_argument("--model")
    a.add_argument("--harness")
    from .execution import PHASES, ROLES

    a = s.add_parser("execution-add", help="Append a confirmed execution record")
    a.add_argument("run")
    a.add_argument("--model")
    a.add_argument("--harness")
    a.add_argument("--role", choices=ROLES, default="assistant")
    a.add_argument("--phase", choices=PHASES, default="research")
    a.add_argument("--task-id")
    a.add_argument("--generation")

    a = s.add_parser("validate")
    a.add_argument("generation")
    a = s.add_parser(
        "figure-candidates", help="List graph structures to review for figures"
    )
    a.add_argument("generation", type=Path, help="Staged or saved generation directory")
    a = s.add_parser(
        "photo-candidates",
        help="List important and representative subjects for documentary images",
    )
    a.add_argument("run")
    a = s.add_parser(
        "image-inspect",
        help="Decode a downloaded image and report its size and checksum",
    )
    a.add_argument("path", type=Path)
    a = s.add_parser(
        "images-save", help="Import image candidates and local files in a batch"
    )
    a.add_argument("run")
    a.add_argument("batch", type=Path)
    a = s.add_parser(
        "images-status", help="Show candidate sources, acquisition and use decisions"
    )
    a.add_argument("run")
    a = s.add_parser(
        "images-stage", help="Prepare approved images in a staging generation"
    )
    a.add_argument("run")
    a.add_argument("stage", type=Path)
    a = s.add_parser("font-check", help="Find unsupported characters before rendering")
    a.add_argument("generation", type=Path, help="Staged or saved generation directory")
    a.add_argument(
        "--title", default="歴史知識地図", help="Title to check with the graph"
    )
    a = s.add_parser("merge")
    a.add_argument("run")
    a.add_argument(
        "generation_dir",
        help="A complete staged generation dir containing all JSONL files",
    )
    a = s.add_parser(
        "layout-metrics",
        help="Measure crossings, collisions and coverage of a saved scene",
    )
    a.add_argument("scene", type=Path)
    a = s.add_parser("render")
    a.add_argument("run")
    a.add_argument(
        "--image-profile", choices=("distribution", "local"), default="distribution"
    )
    a.add_argument("--force-pdf-error", action="store_true")
    a.add_argument(
        "--format", choices=("all", "layout", "html", "pdf", "svg"), default="all"
    )
    a.add_argument(
        "--from-scene", action="store_true", help="Reuse a verified saved layout"
    )
    a.add_argument(
        "--importance",
        type=Path,
        help="Optional JSON mapping node or edge IDs to importance records",
    )
    a = s.add_parser("qa")
    a.add_argument("run")
    a.add_argument(
        "--image-profile", choices=("distribution", "local"), default="distribution"
    )
    a.add_argument(
        "--review",
        required=True,
        help="Review JSON including generation and illustration_review (see data contract)",
    )
    a = s.add_parser("repair-show")
    a.add_argument("proposal")
    a = s.add_parser("repair-apply")
    a.add_argument("proposal")
    a.add_argument("--approved", action="store_true")
    a.add_argument("--root", default=".")
    a = s.add_parser("git-check")
    a.add_argument("--root", default=".")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            run = create_run(
                args.runs,
                args.topic,
                plan_illustrations=True,
                model=args.model,
                harness=args.harness,
            )
            output({"run": str(run), "status": "paused"})
        elif args.command == "task-add":
            output(add_task(args.run, args.topic, args.priority))
        elif args.command == "task-update":
            output(update_task(args.run, args.task_id, args.status))
        elif args.command == "illustrations-save":
            report = json.loads(args.report.read_text(encoding="utf-8"))
            output(save_illustrations(args.run, report, complete=args.complete))
        elif args.command == "representatives-save":
            report = json.loads(args.report.read_text(encoding="utf-8"))
            output(save_representatives(args.run, report, complete=args.complete))
        elif args.command == "status":
            from .status import status

            output(status(args.run))
        elif args.command == "resume":
            output(resume(args.run, model=args.model, harness=args.harness))
        elif args.command == "execution-add":
            output(
                add_execution(
                    args.run,
                    model=args.model,
                    harness=args.harness,
                    phase=args.phase,
                    role=args.role,
                    task_id=args.task_id,
                    generation=args.generation,
                )
            )
        elif args.command == "validate":
            output(validate(load_generation(args.generation)))
        elif args.command == "figure-candidates":
            from .figure_plan import opportunities

            if not (args.generation / "nodes.jsonl").is_file():
                raise ValidationError(
                    f"Generation has no nodes.jsonl: {args.generation}"
                )
            data = load_generation(args.generation)
            validate(data)
            output({"candidates": opportunities(data)})
        elif args.command == "photo-candidates":
            from .photos import opportunities
            from .representatives import KIND
            from .store import research_task

            progress = read_progress(args.run)
            generation = progress["active_generation"]
            data = load_generation(Path(args.run) / "snapshots" / generation)
            task = research_task(progress, KIND) or {}
            output(
                {
                    "generation": generation,
                    "targets": opportunities(data, task.get("representative_report")),
                }
            )
        elif args.command == "image-inspect":
            from .scene import inspect_image

            output(inspect_image(args.path))
        elif args.command == "images-save":
            from .images import save_catalog

            output(
                save_catalog(
                    args.run, json.loads(args.batch.read_text(encoding="utf-8"))
                )
            )
        elif args.command == "images-status":
            from .images import catalog_summary, read_catalog

            output(catalog_summary(read_catalog(args.run)))
        elif args.command == "images-stage":
            from .images import stage_catalog

            output(stage_catalog(args.run, args.stage))
        elif args.command == "font-check":
            if not (args.generation / "nodes.jsonl").is_file():
                raise ValidationError(
                    f"Generation has no nodes.jsonl: {args.generation}"
                )
            data = load_generation(args.generation)
            validate(data)
            check_font_coverage(data, args.title)
            output({"status": "pass"})
        elif args.command == "merge":
            data = load_generation(args.generation_dir)
            gen = commit_generation(args.run, data)
            output({"generation": gen, "validation": validate(data)})
        elif args.command == "layout-metrics":
            from .layout_quality import layout_metrics

            output(layout_metrics(json.loads(args.scene.read_text(encoding="utf-8"))))
        elif args.command == "render":
            from .render_run import render_run

            importance = (
                json.loads(args.importance.read_text(encoding="utf-8"))
                if args.importance
                else None
            )
            output(
                render_run(
                    args.run,
                    args.image_profile,
                    args.format,
                    from_scene=args.from_scene,
                    importance=importance,
                    force_pdf_error=args.force_pdf_error,
                )
            )
        elif args.command == "qa":
            with progress_lock(args.run):
                progress = read_progress(args.run)
                generation = progress["active_generation"]
                verify_generation(args.run, generation)
                data = load_generation(Path(args.run) / "snapshots" / generation)
                directory = (
                    "render-local" if args.image_profile == "local" else "render"
                )
                scene = json.loads(
                    (Path(args.run) / directory / "scene.json").read_text(
                        encoding="utf-8"
                    )
                )
                review = json.loads(Path(args.review).read_text(encoding="utf-8"))
                if scene.get("image_profile", "distribution") != args.image_profile:
                    raise ValidationError(
                        "Saved scene belongs to a different image output profile"
                    )
                qa = judge(data, scene, review, progress=progress, run_dir=args.run)
                status = qa["status"]
                paused = progress["run_status"] == "paused" and qa["unfinished_tasks"]
                if args.image_profile == "distribution":
                    progress["run_status"] = (
                        "paused" if paused and status == "incomplete" else status
                    )
                    atomic_json(Path(args.run) / "progress.json", progress)
                    if (Path(args.run) / "manifest.json").exists():
                        update_manifest(args.run, status=progress["run_status"])
                result = {
                    "status": status,
                    "run_status": progress["run_status"],
                    "generation": generation,
                    "image_profile": args.image_profile,
                    "image_catalog": qa["image_catalog"],
                    "scene": qa["scene"],
                    "unfinished_tasks": qa["unfinished_tasks"],
                    "illustrations": qa["illustrations"],
                    "representatives": qa["representatives"],
                    "chronology": qa["chronology"],
                    "figures": qa["figures"],
                    "photos": qa["photos"],
                    "note": "Review flags must be supported by actual agent evidence, not invented",
                }
                if "render_inputs" in scene and qa["illustrations"]["status"] == "pass":
                    from .render import scene_fingerprint

                    result["scene_sha256"] = scene_fingerprint(scene)
                qa_dir = Path(args.run) / "qa"
                if args.image_profile == "local":
                    qa_dir = qa_dir / generation / "local"
                    atomic_json(qa_dir / "review.json", review)
                    atomic_json(qa_dir / "result.json", result)
                else:
                    atomic_json(qa_dir / generation / "review.json", review)
                    atomic_json(qa_dir / generation / "result.json", result)
                    atomic_json(qa_dir / "illustration-result.json", result)
            output(result)
        elif args.command == "repair-show":
            output(inspect(".", args.proposal))
        elif args.command == "repair-apply":
            output(apply(args.root, args.proposal, args.approved))
        elif args.command == "git-check":
            output(staged_check(args.root))
    except LayoutError as e:
        print(
            json.dumps({"status": "fail", "issues": e.issues}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    except (
        ValidationError,
        RepairError,
        ValueError,
        OSError,
        KeyError,
        ImportError,
    ) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
