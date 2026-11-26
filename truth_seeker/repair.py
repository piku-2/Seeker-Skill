"""Personal, approval-gated direct-worktree repair with file backups."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from .store import atomic_json, utcnow


class RepairError(Exception):
    pass


TEST_COMMAND_PREFIXES = (
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("uv", "run", "pytest"),
    ("uv", "run", "python", "-m", "pytest"),
    ("uv", "run", "python3", "-m", "pytest"),
)

BANNED_PREFIXES = (
    "runs/",
    "outputs/",
    "artifacts/",
    "cache/",
    "assets/downloaded/",
    "data/runs/",
    "data/checkpoints/",
    "data/staging/",
    "experience/local/",
    "repair_backups/",
)

SECRET_RE = re.compile(
    r"(?:"
    r"(?i:(?:api[_-]?key|secret|password|token)\s*[=:]\s*\S+)"
    r"|[A-Z][A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN)[A-Z0-9_]*\s*[=:]\s*\S+"
    r"|[A-Z][A-Z0-9_]*_KEY\s*[=:]\s*\S+"
    r"|sk-[A-Za-z0-9_-]{16,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH |OPEN )?PRIVATE KEY-----"
    r")"
)


def hash_file(p):
    return (
        hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else None
    )


def safe_path(root, relative):
    root = Path(root).resolve()
    p = (root / relative).resolve()
    if p != root and root not in p.parents:
        raise RepairError("outside repository")
    if ".git" in p.relative_to(root).parts:
        raise RepairError("cannot modify .git")
    return p


def check_test_command(argv):
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(x, str) for x in argv)
    ):
        raise RepairError("test command must be argv array")
    if any("\x00" in x or "\n" in x or "\r" in x for x in argv):
        raise RepairError("test command arguments cannot contain control characters")
    for prefix in TEST_COMMAND_PREFIXES:
        if len(argv) >= len(prefix) and tuple(argv[: len(prefix)]) == prefix:
            return
    raise RepairError(
        "test command must be python -m pytest or uv run pytest, with pytest arguments only"
    )


def proposal(root, changes, reason, tests=None):
    root = Path(root).resolve()
    items = []
    for rel, new in changes.items():
        p = safe_path(root, rel)
        if p.exists() and not p.is_file():
            raise RepairError("target not regular file")
        if str(rel).startswith(("runs/", "experience/local/", "repair_backups/")):
            raise RepairError("excluded runtime path")
        items.append(
            {
                "path": str(p.relative_to(root)),
                "before_sha256": hash_file(p),
                "after_sha256": hashlib.sha256(new.encode()).hexdigest(),
                "new_content": new,
            }
        )
    for argv in tests or []:
        check_test_command(argv)
    rid = "repair_" + uuid4().hex[:12]
    record = {
        "repair_id": rid,
        "reason": reason,
        "changes": items,
        "test_commands": tests or [],
        "created_at": utcnow(),
        "status": "proposed",
    }
    out = root / "experience" / "local" / "proposals" / f"{rid}.json"
    atomic_json(out, record)
    return out


def inspect(root, proposal_file):
    p = Path(proposal_file)
    d = json.loads(p.read_text(encoding="utf-8"))
    return {
        "repair_id": d["repair_id"],
        "reason": d["reason"],
        "files": [i["path"] for i in d["changes"]],
        "test_commands": d["test_commands"],
        "status": d["status"],
    }


def apply(root, proposal_file, approval=None, timeout=120):
    root = Path(root).resolve()
    proposal_file = Path(proposal_file)
    d = json.loads(proposal_file.read_text(encoding="utf-8"))
    if approval != d.get("repair_id"):
        raise RepairError("Explicit user approval of this repair_id is required")
    if d["status"] != "proposed":
        raise RepairError("Proposal already used")
    for item in d["changes"]:
        p = safe_path(root, item["path"])
        if hash_file(p) != item["before_sha256"]:
            raise RepairError("Changed since proposal: " + item["path"])
    for argv in d["test_commands"]:
        check_test_command(argv)
    backups = root / "repair_backups" / d["repair_id"]
    backups.mkdir(parents=True, exist_ok=False)
    existed = {}
    for item in d["changes"]:
        p = safe_path(root, item["path"])
        existed[item["path"]] = p.exists()
        if p.exists():
            b = backups / item["path"]
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, b)
    atomic_json(
        backups / "manifest.json",
        {"files": existed, "repair_id": d["repair_id"], "created_at": utcnow()},
    )
    result = {
        "repair_id": d["repair_id"],
        "test_results": [],
        "status": "failed",
        "backup": str(backups),
    }
    applied = []
    try:
        for item in d["changes"]:
            p = safe_path(root, item["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".repair_tmp")
            # Exclusive creation keeps a pre-existing temporary file intact.
            stream = tmp.open("x", encoding="utf-8")
            try:
                with stream:
                    stream.write(item["new_content"])
                os.replace(tmp, p)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            applied.append((p, item))
        for argv in d["test_commands"]:
            process = subprocess.run(
                argv, cwd=root, capture_output=True, text=True, timeout=timeout
            )
            result["test_results"].append(
                {
                    "argv": argv,
                    "returncode": process.returncode,
                    "output": (process.stdout + process.stderr)[-5000:],
                }
            )
            if process.returncode != 0:
                raise RepairError("regression failure: " + " ".join(argv))
        result["status"] = "passed"
    except Exception as e:
        result["failure"] = str(e)
        result["rollback"] = "restored"
        conflicts = [
            item["path"]
            for p, item in applied
            if not _matches_applied_file(p, item["after_sha256"])
        ]
        if conflicts:
            result["rollback"] = "conflict"
            result["rollback_conflicts"] = conflicts
        else:
            for p, item in reversed(applied):
                rel = item["path"]
                if not _matches_applied_file(p, item["after_sha256"]):
                    result["rollback"] = "conflict"
                    result["rollback_conflicts"] = [rel]
                    break
                try:
                    if existed[rel]:
                        shutil.copy2(backups / rel, p)
                    else:
                        p.unlink()
                except OSError as exc:
                    result["rollback"] = "failed"
                    result["rollback_failure"] = str(exc)
                    break
    d["status"] = result["status"]
    atomic_json(proposal_file, d)
    atomic_json(backups / "result.json", result)
    return result


def _matches_applied_file(path, expected_hash):
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and hash_file(path) == expected_hash
        )
    except OSError:
        return False


def _posix_rel(rel):
    posix = rel.replace("\\", "/")
    while posix.startswith("./"):
        posix = posix[2:]
    return posix.lstrip("/")


def ignored_runtime_path(rel):
    posix = _posix_rel(rel)
    name = Path(posix).name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    parts = Path(posix).parts
    if ".venv" in parts or "__pycache__" in parts or ".pytest_cache" in parts:
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if Path(posix).suffix.lower() in {".pyc", ".pyo", ".pyd"}:
        return True
    return any(posix == prefix.rstrip("/") or posix.startswith(prefix) for prefix in BANNED_PREFIXES)


def staged_check(root):
    root = Path(root).resolve()
    cmd = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"], cwd=root, capture_output=True
    )
    if cmd.returncode:
        raise RepairError("git staging unavailable")
    paths = [x.decode("utf-8", errors="replace") for x in cmd.stdout.split(b"\0") if x]
    problems = []
    for rel in paths:
        if ignored_runtime_path(rel) or Path(rel).suffix.lower() in (
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".svg",
            ".html",
        ):
            problems.append(f"generated/private file staged: {rel}")
        blob = subprocess.run(["git", "show", ":" + rel], cwd=root, capture_output=True)
        if blob.returncode == 0:
            content = blob.stdout.decode("utf-8", errors="ignore")
            if SECRET_RE.search(content):
                problems.append(f"possible secret in {rel}")
    return {
        "safe": not problems,
        "staged_paths": paths,
        "problems": problems,
        "note": "Heuristic scanner; explicit user review remains required",
    }
