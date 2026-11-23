"""Command-line helper used by agent Skills. Research itself remains agent-led."""

import argparse
import json
import sys
from pathlib import Path

from .model import LayoutError, ValidationError, load_generation, validate
from .qa import judge
from .render import emit, layout
from .repair import RepairError, apply, inspect, staged_check
from .store import (
    add_task,
    atomic_json,
    commit_generation,
    create_run,
    read_progress,
    resume,
    update_manifest,
    update_task,
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
    a = s.add_parser("status")
    a.add_argument("run")
    a = s.add_parser("resume")
    a.add_argument("run")
    a = s.add_parser("validate")
    a.add_argument("generation")
    a = s.add_parser("merge")
    a.add_argument("run")
    a.add_argument(
        "generation_dir",
        help="A complete staged generation dir containing all JSONL files",
    )
    a = s.add_parser("render")
    a.add_argument("run")
    a.add_argument("--force-pdf-error", action="store_true")
    a.add_argument(
        "--importance",
        type=Path,
        help="Optional JSON mapping node IDs to importance records",
    )
    a = s.add_parser("qa")
    a.add_argument("run")
    a.add_argument(
        "--review",
        required=True,
        help="JSON file with research_complete, semantic_pass, artifact_pass, independent_review",
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
            run = create_run(args.runs, args.topic)
            output({"run": str(run), "status": "paused"})
        elif args.command == "task-add":
            output(add_task(args.run, args.topic, args.priority))
        elif args.command == "task-update":
            output(update_task(args.run, args.task_id, args.status))
        elif args.command == "status":
            output(read_progress(args.run))
        elif args.command == "resume":
            output(resume(args.run))
        elif args.command == "validate":
            output(validate(load_generation(args.generation)))
        elif args.command == "merge":
            data = load_generation(args.generation_dir)
            gen = commit_generation(args.run, data)
            output({"generation": gen, "validation": validate(data)})
        elif args.command == "render":
            progress = read_progress(args.run)
            data = load_generation(
                Path(args.run) / "snapshots" / progress["active_generation"]
            )
            importance = (
                json.loads(args.importance.read_text(encoding="utf-8"))
                if args.importance
                else None
            )
            scene = layout(data, progress["run_id"], progress["query"], importance)
            result = emit(scene, Path(args.run) / "render", args.force_pdf_error)
            artifacts = [
                f"render/{f}"
                for f in ("scene.json", "map.svg", "map.html", "map.pdf")
                if (Path(args.run) / "render" / f).exists()
            ]
            if (Path(args.run) / "manifest.json").exists():
                update_manifest(args.run, artifacts=artifacts)
            output(result)
        elif args.command == "qa":
            progress = read_progress(args.run)
            data = load_generation(
                Path(args.run) / "snapshots" / progress["active_generation"]
            )
            scene = json.loads(
                (Path(args.run) / "render" / "scene.json").read_text(encoding="utf-8")
            )
            review = json.loads(Path(args.review).read_text(encoding="utf-8"))
            qa = judge(data, scene, review)
            status = qa["status"]
            progress["run_status"] = status
            atomic_json(Path(args.run) / "progress.json", progress)
            if (Path(args.run) / "manifest.json").exists():
                update_manifest(args.run, status=status)
            output(
                {
                    "status": status,
                    "scene": qa["scene"],
                    "note": "Review flags must be supported by actual agent evidence, not invented",
                }
            )
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
