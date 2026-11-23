"""Generation-based durable graph snapshots and resumable task state."""

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .illustrations import TASK_KIND, TASK_TOPIC, validate_search
from .model import FILES, ValidationError, dump_records, load_generation, validate

JST = timezone(timedelta(hours=9))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def progress_lock(base):
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    lock_path = base / ".progress.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def sanitize_folder_theme(query, max_len=30):
    if not query:
        return "topic"
    cleaned = re.sub(r"[\r\n\t]+", " ", str(query)).strip()
    cleaned = re.sub(r'[\x00-\x1f\x7f\\/:*?"<>|！？：＊？＜＞｜／＼]', "_", cleaned)
    cleaned = re.sub(r"[\s\u3000]+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._ ")
    shortened = cleaned[:max_len].rstrip("._ ")
    return shortened or "topic"


def generate_run_folder_name(query, dt=None, suffix_id=None):
    if dt is None:
        dt = datetime.now(JST)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(JST)
    else:
        dt = dt.astimezone(JST)
    ts = dt.strftime("%Y%m%d_%H%M")
    theme = sanitize_folder_theme(query, max_len=30)
    rand = suffix_id or uuid4().hex[:6]
    return f"{ts}_{theme}_{rand}"


def read_manifest(base):
    path = Path(base) / "manifest.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def update_manifest(base, **fields):
    base = Path(base)
    mf = read_manifest(base) or {}
    mf.update(fields)
    mf["updated_at"] = utcnow()
    atomic_json(base / "manifest.json", mf)
    return mf


def create_run(root, query, run_id=None, folder_name=None, *, plan_illustrations=False):
    run_id = run_id or ("run_" + uuid4().hex[:12])
    folder_name = folder_name or generate_run_folder_name(query)
    base = Path(root) / folder_name
    if base.exists():
        raise FileExistsError(base)
    (base / "snapshots").mkdir(parents=True)
    for d in ("staging", "render", "qa"):
        (base / d).mkdir()
    now = utcnow()
    initial = {
        "schema_version": "0.5",
        "run_id": run_id,
        "query": query,
        "run_status": "paused",
        "active_generation": "g000000",
        "tasks": [],
        "frontier_task_ids": [],
        "completed_task_ids": [],
        "blocked_task_ids": [],
        "last_successful_phase": "initialized",
        "checkpoint_at": now,
    }
    if plan_illustrations:
        _ensure_illustration_task(initial)
    commit_generation(base, {k: [] for k in FILES}, progress=initial)
    atomic_json(
        base / "manifest.json",
        {
            "run_id": run_id,
            "query": query,
            "folder_name": folder_name,
            "created_at": now,
            "updated_at": now,
            "artifacts": [],
            "status": "paused",
        },
    )
    return base


def read_progress(base):
    return json.loads((Path(base) / "progress.json").read_text(encoding="utf-8"))


def commit_generation(base, data, progress=None):
    base = Path(base)
    with progress_lock(base):
        check = validate(data)
        old = read_progress(base) if (base / "progress.json").exists() else None
        number = int(old["active_generation"][1:]) + 1 if old else 0
        gen = f"g{number:06d}"
        tmp = base / "snapshots" / ("_" + gen + "_" + uuid4().hex)
        tmp.mkdir(parents=True, exist_ok=False)
        for name in FILES:
            dump_records(tmp / (name + ".jsonl"), data.get(name, []))
        digest = {
            name: hashlib.sha256((tmp / (name + ".jsonl")).read_bytes()).hexdigest()
            for name in FILES
        }
        atomic_json(
            tmp / "index.json",
            {"generation": gen, "sha256": digest, "counts": check["counts"]},
        )
        validate(load_generation(tmp))
        dst = base / "snapshots" / gen
        os.replace(tmp, dst)
        pr = dict(progress or old or {})
        pr["active_generation"] = gen
        pr["checkpoint_at"] = utcnow()
        atomic_json(base / "progress.json", pr)
        if (base / "manifest.json").exists():
            update_manifest(base)
        return gen


def verify_generation(base, gen):
    root = Path(base) / "snapshots" / gen
    idx = json.loads((root / "index.json").read_text(encoding="utf-8"))
    for name, expected in idx["sha256"].items():
        if (
            hashlib.sha256((root / (name + ".jsonl")).read_bytes()).hexdigest()
            != expected
        ):
            raise ValidationError("snapshot checksum mismatch " + name)
    return validate(load_generation(root))


def latest_good(base):
    base = Path(base)
    for d in sorted((base / "snapshots").glob("g[0-9]*"), reverse=True):
        try:
            verify_generation(base, d.name)
            return d.name
        except (ValidationError, FileNotFoundError, ValueError, KeyError):
            continue
    raise ValidationError("no valid snapshot")


def illustration_task(progress):
    return next(
        (task for task in progress["tasks"] if task.get("kind") == TASK_KIND), None
    )


def _new_task(progress, topic, priority=0, parent_task_id=None):
    task = dict(
        task_id="task_" + uuid4().hex[:12],
        run_id=progress["run_id"],
        topic=topic,
        parent_task_id=parent_task_id,
        priority=priority,
        status="pending",
        attempts=0,
        assigned_worker=None,
        input_refs=[],
        output_refs=[],
        discovered_task_ids=[],
        last_error=None,
        updated_at=utcnow(),
    )
    progress["tasks"].append(task)
    progress["frontier_task_ids"].append(task["task_id"])
    return task


def _ensure_illustration_task(progress):
    task = illustration_task(progress)
    if task is None:
        task = _new_task(progress, TASK_TOPIC)
        task["kind"] = TASK_KIND
    return task


def save_illustrations(base, report, complete=False):
    """Checkpoint a draft or finalize it against the active graph."""
    if not isinstance(report, dict):
        raise ValidationError("illustrations: expected a JSON object")
    base = Path(base)
    with progress_lock(base):
        progress = read_progress(base)
        task = _ensure_illustration_task(progress)
        generation = progress["active_generation"]
        if complete:
            verify_generation(base, generation)
            validate_search(report, load_generation(base / "snapshots" / generation))
        task["illustration_report"] = report
        task["illustration_generation"] = generation if complete else None
        status = "completed" if complete else "running"
        _set_task_status(progress, task, status)
        atomic_json(base / "progress.json", progress)
        if (base / "manifest.json").exists():
            update_manifest(base)
        return task


def resume(base):
    base = Path(base)
    with progress_lock(base):
        pr = read_progress(base)
        try:
            verify_generation(base, pr["active_generation"])
        except (ValidationError, FileNotFoundError, ValueError, KeyError):
            pr["active_generation"] = latest_good(base)
        illustrations = _ensure_illustration_task(pr)
        for t in pr.get("tasks", []):
            if t.get("status") == "running":
                _set_task_status(pr, t, "retryable")
                t["last_error"] = "Interrupted; reclaimed on resume"
        if (
            illustrations["status"] == "completed"
            and illustrations.get("illustration_generation") != pr["active_generation"]
        ):
            _set_task_status(pr, illustrations, "retryable")
            illustrations["last_error"] = (
                "Active generation changed; recheck the illustration report"
            )
        pr["run_status"] = "paused"
        atomic_json(base / "progress.json", pr)
        if (base / "manifest.json").exists():
            update_manifest(base, status="paused")
        return pr


def add_task(base, topic, priority=0, parent_task_id=None):
    base = Path(base)
    with progress_lock(base):
        pr = read_progress(base)
        if any(
            t["topic"] == topic and t["status"] != "superseded" for t in pr["tasks"]
        ):
            return False
        task = _new_task(pr, topic, priority, parent_task_id)
        atomic_json(base / "progress.json", pr)
        if (base / "manifest.json").exists():
            update_manifest(base)
        return task


def update_task(base, task_id, status, **extra):
    base = Path(base)
    with progress_lock(base):
        pr = read_progress(base)
        matched = [t for t in pr["tasks"] if t["task_id"] == task_id]
        if len(matched) != 1:
            raise ValueError("unknown task")
        t = matched[0]
        t.update(extra)
        if t.get("kind") == TASK_KIND and status == "completed":
            raise ValidationError(
                "finalize illustration searches with illustrations-save --complete"
            )
        _set_task_status(pr, t, status)
        atomic_json(base / "progress.json", pr)
        if (base / "manifest.json").exists():
            update_manifest(base)
        return t


def _set_task_status(progress, task, status):
    task["status"] = status
    task["updated_at"] = utcnow()
    task_id = task["task_id"]
    destinations = {
        "frontier_task_ids": ("pending", "retryable"),
        "completed_task_ids": ("completed",),
        "blocked_task_ids": ("blocked",),
    }
    for field, statuses in destinations.items():
        progress[field] = [value for value in progress[field] if value != task_id]
        if status in statuses:
            progress[field].append(task_id)
