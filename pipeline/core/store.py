"""File-backed project store: one folder per project.

    projects/<slug>-<id>/
        job.json              the Job (source of truth for state)
        decisions.jsonl       append-only log of every decision (human, AI, machine)
        DECISIONS.md          readable version of the log
        CREDITS.md            attribution for every approved asset (written at render)
        sources/<name>/       everything pulled from ONE source, kept together:
            requests.jsonl        every web request made (URL, params, status, purpose)
            manifest.json         every file kept: URL, license, author, hash, ...
            files/                the downloaded files
        assets/               render outputs and scratch files for this project

Writes are atomic (temp file + rename) so a crash never leaves half a job.json.
Swap this class for SQLite/Postgres later; the orchestrator only uses the
methods below.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .decisions import DecisionLog
from .models import Job


class JobNotFound(KeyError):
    pass


class JobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._dirs: dict[str, Path] = {}

    # ---------------------------------------------------------------- locations
    def _find_dir(self, job_id: str) -> Path | None:
        cached = self._dirs.get(job_id)
        if cached and cached.exists():
            return cached
        for p in self.root.glob(f"*-{job_id}"):
            if (p / "job.json").exists():
                self._dirs[job_id] = p
                return p
        return None

    def job_dir(self, job_id: str, slug: str | None = None) -> Path:
        found = self._find_dir(job_id)
        if found:
            return found
        if slug is None:
            raise JobNotFound(job_id)
        p = self.root / f"{slug}-{job_id}"
        self._dirs[job_id] = p
        return p

    def assets_dir(self, job_id: str) -> Path:
        p = self.job_dir(job_id) / "assets"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def source_dir(self, job_id: str, source: str) -> Path:
        """The folder that holds everything pulled from one source."""
        p = self.job_dir(job_id) / "sources" / source
        (p / "files").mkdir(parents=True, exist_ok=True)
        return p

    def decisions(self, job_id: str) -> DecisionLog:
        return DecisionLog(self.job_dir(job_id))

    # --------------------------------------------------------------------- crud
    def save(self, job: Job) -> None:
        d = self.job_dir(job.id, job.slug)
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(job.model_dump_json(indent=2))
            os.replace(tmp, d / "job.json")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def load(self, job_id: str) -> Job:
        d = self._find_dir(job_id)
        if d is None:
            raise JobNotFound(job_id)
        return Job.model_validate_json((d / "job.json").read_text(encoding="utf-8"))

    def list(self) -> list[Job]:
        jobs = []
        for p in sorted(self.root.glob("*/job.json")):
            jobs.append(Job.model_validate_json(p.read_text(encoding="utf-8")))
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)
