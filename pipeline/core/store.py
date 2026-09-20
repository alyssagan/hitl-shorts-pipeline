"""File-backed job store: one folder per job.

    data/jobs/<job_id>/job.json     the Job (source of truth)
    data/jobs/<job_id>/assets/      audio, clips, final render for that job

Writes are atomic (temp file + rename) so a crash never leaves half a job.json.
Swap this class for SQLite/Postgres later; the orchestrator only uses the
methods below.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .models import Job


class JobNotFound(KeyError):
    pass


class JobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.root / "jobs" / job_id

    def assets_dir(self, job_id: str) -> Path:
        p = self.job_dir(job_id) / "assets"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save(self, job: Job) -> None:
        d = self.job_dir(job.id)
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
        path = self.job_dir(job_id) / "job.json"
        if not path.exists():
            raise JobNotFound(job_id)
        return Job.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[Job]:
        jobs = []
        for p in sorted((self.root / "jobs").glob("*/job.json")):
            jobs.append(Job.model_validate_json(p.read_text(encoding="utf-8")))
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)
