"""Recorded execution identifiers, kept separate from historical research."""

import os
import unicodedata
from datetime import datetime, timezone

from .model import ensure
from .typography import missing_display_glyphs

ROLES = ("main", "assistant", "subagent", "reviewer", "renderer")
PHASES = ("init", "resume", "research", "review", "render")


def identifiers(model=None, harness=None):
    names = {
        "model": (model, "TRUTH_SEEKER_MODEL"),
        "harness": (harness, "TRUTH_SEEKER_HARNESS"),
    }
    record = {}
    for key, (value, variable) in names.items():
        origin = "explicit"
        if value is None:
            value, origin = os.environ.get(variable), "environment"
        if value is None:
            value, origin = "unknown", "unknown"
        ensure(
            isinstance(value, str)
            and 0 < len(value) <= 96
            and all(
                unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                for char in value
            )
            and bool(value.strip()),
            f"execution: {key} must be a printable identifier of 1–96 characters",
        )
        record[key] = unicodedata.normalize("NFC", value.strip())
        record[f"{key}_source"] = origin
    return record


def entry(identity, phase, role, generation, recorded_at, task_id=None):
    ensure(phase in PHASES and role in ROLES, "execution: invalid phase or role")
    return {
        **identity,
        "phase": phase,
        "role": role,
        "generation": generation,
        "recorded_at": recorded_at,
        **({"task_id": task_id} if task_id else {}),
    }


def scene_metadata(progress, manifest):
    return {
        "run_id": progress["run_id"],
        "topic": progress["query"],
        "started_at": manifest.get("created_at"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history": manifest.get("execution_history", []),
    }


def summary(metadata):
    latest = next(
        (item for item in reversed(metadata["history"]) if item["role"] == "main"), {}
    )
    return (
        latest.get("model", "unknown"),
        latest.get("harness", "unknown"),
        metadata["generated_at"],
    )


def map_labels(metadata):
    model, harness, generated = summary(metadata)

    def compact(text):
        safe = text if len(text) <= 36 else text[:35] + "…"
        missing = set(missing_display_glyphs(safe))
        return "".join("?" if c in missing else c for c in safe)

    clock = datetime.fromisoformat(generated).isoformat(timespec="seconds")
    return f"Model: {compact(model)}   Harness: {compact(harness)}   Generated: {clock}"
