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


def apply(root, proposal_file, approval=False, timeout=120):
    if not approval:
        raise RepairError("Explicit user approval required")
    root = Path(root).resolve()
    proposal_file = Path(proposal_file)
    d = json.loads(proposal_file.read_text(encoding="utf-8"))
    if d["status"] != "proposed":
        raise RepairError("Proposal already used")
    for item in d["changes"]:
        p = safe_path(root, item["path"])
        if hash_file(p) != item["before_sha256"]:
            raise RepairError("Changed since proposal: " + item["path"])
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
    try:
        for item in d["changes"]:
            p = safe_path(root, item["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".repair_tmp")
            tmp.write_text(item["new_content"], encoding="utf-8")
            os.replace(tmp, p)
        for argv in d["test_commands"]:
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(x, str) for x in argv)
            ):
                raise RepairError("test command must be argv array")
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
        for item in d["changes"]:
            p = safe_path(root, item["path"])
            b = backups / item["path"]
            # Do not overwrite an externally modified file if detected.
            if p.exists() and hash_file(p) != item["after_sha256"]:
                result["rollback"] = "conflict"
                continue
            if existed[item["path"]]:
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(b, p)
            elif p.exists():
                p.unlink()
    d["status"] = result["status"]
    atomic_json(proposal_file, d)
    atomic_json(backups / "result.json", result)
    return result


SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token)\s*[=:]\s*[^\s]+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def staged_check(root):
    root = Path(root).resolve()
    cmd = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"], cwd=root, capture_output=True
    )
    if cmd.returncode:
        raise RepairError("git staging unavailable")
    paths = [x.decode("utf-8", errors="replace") for x in cmd.stdout.split(b"\0") if x]
    banned = (
        "/runs/",
        "/outputs/",
        "/artifacts/",
        "/experience/local/",
        "/repair_backups/",
        "/cache/",
        "/assets/downloaded/",
    )
    problems = []
    for rel in paths:
        if any("/" + rel.startswith(b[1:]) for b in []):
            pass
        if any(("/" + rel).startswith(b) for b in banned) or Path(
            rel
        ).suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".html"):
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
