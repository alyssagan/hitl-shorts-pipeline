"""Reading `RELEVANCE_LABELS.jsonl` across one or more projects, and the holdout-set discipline (docs/EVALUATION.md).

`RELEVANCE_LABELS.jsonl` (written by `Orchestrator._write_label`, `pipeline/core/orchestrator.py`) is APPEND-ONLY:
relabeling an asset adds a new row rather than replacing the old one, so nothing already written is ever
overwritten. That means "the current label for this asset" is defined here as the LATEST row (by `at`) per
(job, asset_id) -- `latest_per_asset()` is the one place that dedup rule lives, so `scripts/evaluate_relevance.py`
and anything else reading these files agrees on what "current" means.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LABELS_FILENAME = "RELEVANCE_LABELS.jsonl"
HOLDOUT_FILENAME = "HOLDOUT.json"


def discover_job_dirs(projects_root: Path) -> list[Path]:
    """Every project folder under `projects_root` that looks like a real job (has a job.json), sorted by name
    for a stable, reproducible order."""
    return sorted({p.parent for p in Path(projects_root).glob("*/job.json")})


def read_label_rows(job_dir: Path) -> list[dict[str, Any]]:
    """Every row ever written to this job's RELEVANCE_LABELS.jsonl, oldest first, exactly as logged -- including
    every relabel of the same asset. Returns [] if the file doesn't exist yet (most jobs won't, until someone
    clicks Use/Duplicate/Irrelevant in the review page). A malformed line is skipped, not fatal, since this file
    is a growing log a person might be tailing/editing by hand; skipped lines are reported by the caller if it
    cares (see `read_all_label_rows`)."""
    path = Path(job_dir) / LABELS_FILENAME
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


@dataclass
class LoadedLabels:
    rows: list[dict[str, Any]] = field(default_factory=list)                 # every row, every job, oldest first
    jobs_scanned: int = 0
    jobs_with_labels: int = 0
    holdout_job_dirs: set[str] = field(default_factory=set)                  # str(path) of jobs marked holdout


def read_all_label_rows(projects_root: Path, *, job_ids: list[str] | None = None) -> LoadedLabels:
    """Reads RELEVANCE_LABELS.jsonl from every job under `projects_root` (or just the given `job_ids`, matched
    against the trailing `-<id>` of each project folder name -- the same id the API/orchestrator uses). Each row
    is annotated with `_job_dir` (str) so callers can look up holdout status / go back to the job. Does NOT dedupe
    to latest-per-asset -- call `latest_per_asset()` on the result when you want "current" labels only; some
    callers (e.g. an audit of relabeling history) want every row."""
    out = LoadedLabels()
    for job_dir in discover_job_dirs(projects_root):
        job_id = job_dir.name.rsplit("-", 1)[-1]
        if job_ids and job_id not in job_ids:
            continue
        out.jobs_scanned += 1
        if is_holdout(job_dir):
            out.holdout_job_dirs.add(str(job_dir))
        rows = read_label_rows(job_dir)
        if rows:
            out.jobs_with_labels += 1
        for r in rows:
            r = dict(r)
            r["_job_dir"] = str(job_dir)
            out.rows.append(r)
    return out


def latest_per_asset(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapses possibly-many relabels of the same asset down to the latest one, by `at` (ISO 8601 timestamps,
    so a plain string comparison sorts correctly) -- ties broken by file order (later write wins). The full
    history is never discarded on disk; this is just what "current" means when reporting (docs/EVALUATION.md)."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r.get("_job_dir", r.get("job", "")), r.get("asset_id", ""))
        prev = best.get(key)
        if prev is None or (r.get("at", "") >= prev.get("at", "")):
            best[key] = r
    return list(best.values())


def is_holdout(job_dir: Path) -> dict[str, Any] | None:
    """Returns the holdout marker (`{"marked_at", "by", "reason"}`) if this job/case has been set aside as a
    stable evaluation holdout (docs/EVALUATION.md), else None. Being a holdout doesn't stop anyone from labeling
    it -- it's a flag for the REPORT to respect (excluded from the normal comparison by default), not a lock on
    the job itself."""
    path = Path(job_dir) / HOLDOUT_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def mark_holdout(job_dir: Path, *, by: str, reason: str) -> dict[str, Any]:
    """Marks a job/case as a stable holdout: scripts/evaluate_relevance.py excludes it from the normal report by
    default so nobody can (even accidentally) tune a scoring change against it and then present it as an
    independent test (docs/EVALUATION.md). This is a marker file, not an append-only record like decisions.jsonl
    or RELEVANCE_LABELS.jsonl -- it records a current fact about the job ("this is held out"), not a history of
    decisions, so re-marking it (e.g. to fix the reason text) simply overwrites it; `already` on the returned
    dict says whether one already existed."""
    path = Path(job_dir) / HOLDOUT_FILENAME
    from datetime import datetime, timezone
    existing = is_holdout(job_dir)
    marker = {"marked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by, "reason": reason}
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    marker["already"] = existing is not None
    return marker


def unmark_holdout(job_dir: Path) -> bool:
    """Removes the holdout marker. Returns False if there wasn't one."""
    path = Path(job_dir) / HOLDOUT_FILENAME
    if not path.exists():
        return False
    path.unlink()
    return True
