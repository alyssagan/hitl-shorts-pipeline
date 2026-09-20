"""Orchestrator: the only place that combines the state machine, the store and
the pluggable stages.

Human actions (approve/reject/edit) are quick, lock-protected mutations.
Machine work happens in `run_pending(job_id)`, which looks at the job's state
and runs the matching stage. Because it is driven purely by persisted state it
is idempotent and resumable: after a crash, `resume_all()` restarts any job
that was mid-stage.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable

from . import state_machine as sm
from .models import Job, JobState, Keyword, ProviderChoice, RUNNING_STATES, Scene
from .store import JobStore
from ..stages.base import StageContext
from ..stages.registry import Registry


class Orchestrator:
    def __init__(self, store: JobStore, registry: Registry, settings: dict[str, Any] | None = None):
        self.store = store
        self.registry = registry
        self.settings = settings or {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._running: set[str] = set()
        # Optional hook, e.g. the API uses it to launch run_pending in the background.
        self.on_running: Callable[[str], None] | None = None

    # ------------------------------------------------------------------ helpers
    async def _mutate(self, job_id: str, fn: Callable[[Job], None]) -> Job:
        async with self._locks[job_id]:
            job = self.store.load(job_id)
            fn(job)
            self.store.save(job)
        if job.state in RUNNING_STATES and self.on_running:
            self.on_running(job_id)
        return job

    def get(self, job_id: str) -> Job:
        return self.store.load(job_id)

    # ------------------------------------------------------------------ creation
    async def create_job(self, subject: str, providers: ProviderChoice | None = None) -> Job:
        job = Job(subject=subject.strip(), providers=providers or ProviderChoice())
        if not job.subject:
            raise ValueError("subject is required")
        job.log("note", "job created")
        self.store.save(job)
        return job

    async def start(self, job_id: str) -> Job:
        return await self._mutate(job_id, lambda j: sm.apply(j, "start"))

    # ------------------------------------------------------------------ gate 1: keywords
    async def review_keywords(
        self,
        job_id: str,
        approved_ids: list[str],
        extra_terms: list[str] | None = None,
    ) -> Job:
        """Approve exactly `approved_ids` (plus any terms the reviewer typed in)."""
        def fn(job: Job) -> None:
            ids = set(approved_ids)
            unknown = ids - {k.id for k in job.keywords}
            if unknown:
                raise ValueError(f"unknown keyword ids: {sorted(unknown)}")
            for k in job.keywords:
                k.approved = k.id in ids
            for term in extra_terms or []:
                if term.strip():
                    job.keywords.append(Keyword(term=term.strip(), source="human", approved=True))
            sm.apply(job, "approve_keywords")
        return await self._mutate(job_id, fn)

    async def reject_keywords(self, job_id: str, feedback: str = "") -> Job:
        def fn(job: Job) -> None:
            if feedback.strip():
                job.keyword_feedback.append(feedback.strip())
            sm.apply(job, "reject_keywords", note=feedback)
            job.keywords = []
        return await self._mutate(job_id, fn)

    # ------------------------------------------------------------------ gate 2: scenes
    async def edit_scenes(
        self,
        job_id: str,
        order: list[str] | None = None,
        edits: dict[str, dict[str, Any]] | None = None,
    ) -> Job:
        """Reorder scenes and/or edit narration/clip/approved flags. Only
        allowed while the job is waiting in SCENES_REVIEW."""
        def fn(job: Job) -> None:
            if job.state is not JobState.SCENES_REVIEW:
                raise sm.TransitionError("scenes can only be edited during scene review")
            by_id = {s.id: s for s in job.scenes}
            for sid, changes in (edits or {}).items():
                if sid not in by_id:
                    raise ValueError(f"unknown scene id {sid}")
                for field in ("narration", "clip_path", "approved", "search_terms"):
                    if field in changes:
                        setattr(by_id[sid], field, changes[field])
            if order is not None:
                if sorted(order) != sorted(by_id):
                    raise ValueError("order must list every scene id exactly once")
                job.scenes = [by_id[sid] for sid in order]
            for i, s in enumerate(job.scenes):
                s.index = i
            job.log("note", "scenes edited", order=[s.id for s in job.scenes])
        return await self._mutate(job_id, fn)

    async def approve_scenes(self, job_id: str, approve_all: bool = True) -> Job:
        def fn(job: Job) -> None:
            if approve_all:
                for s in job.scenes:
                    s.approved = True
            sm.apply(job, "approve_scenes")
        return await self._mutate(job_id, fn)

    async def reject_scenes(self, job_id: str, feedback: str = "") -> Job:
        def fn(job: Job) -> None:
            if feedback.strip():
                job.scene_feedback.append(feedback.strip())
            sm.apply(job, "reject_scenes", note=feedback)
        return await self._mutate(job_id, fn)

    async def back_to_keywords(self, job_id: str) -> Job:
        return await self._mutate(job_id, lambda j: sm.apply(j, "back_to_keywords"))

    # ------------------------------------------------------------------ lifecycle
    async def cancel(self, job_id: str) -> Job:
        return await self._mutate(job_id, lambda j: sm.apply(j, "cancel"))

    async def retry(self, job_id: str) -> Job:
        return await self._mutate(job_id, lambda j: sm.apply(j, "retry"))

    # ------------------------------------------------------------------ machine work
    async def run_pending(self, job_id: str) -> Job:
        """Run the stage for the job's current running state (if any)."""
        if job_id in self._running:      # already being worked on in this process
            return self.store.load(job_id)
        self._running.add(job_id)
        try:
            job = self.store.load(job_id)
            state = job.state
            if state not in RUNNING_STATES:
                return job
            ctx = StageContext(assets_dir=self.store.assets_dir(job_id), settings=self.settings)
            try:
                if state is JobState.KEYWORDS_RUNNING:
                    stage = self.registry.keyword_stage(job.providers.keywords)
                    result = await stage.run(job, ctx)
                    return await self._commit(job_id, state, "keywords_ready",
                                              lambda j: setattr(j, "keywords", result))
                if state is JobState.SCENES_RUNNING:
                    stage = self.registry.scene_stage(job.providers.scenes)
                    res = await stage.run(job, ctx)
                    def apply_scenes(j: Job) -> None:
                        j.script = res.script
                        j.scenes = res.scenes
                    return await self._commit(job_id, state, "scenes_ready", apply_scenes)
                # RENDERING
                stage = self.registry.render_stage(job.providers.render)
                out = await stage.run(job, ctx)
                return await self._commit(job_id, state, "render_done",
                                          lambda j: setattr(j, "output_path", out))
            except Exception as exc:  # noqa: BLE001 - any stage failure must land in FAILED
                return await self._fail(job_id, state, exc)
        finally:
            self._running.discard(job_id)

    async def _commit(self, job_id: str, expected: JobState, event: str, apply: Callable[[Job], None]) -> Job:
        async with self._locks[job_id]:
            job = self.store.load(job_id)
            if job.state is not expected:      # cancelled / changed while the stage ran
                job.log("note", f"discarded {expected.value} result; job is now {job.state.value}")
                self.store.save(job)
                return job
            apply(job)
            sm.apply(job, event)
            self.store.save(job)
            return job

    async def _fail(self, job_id: str, expected: JobState, exc: Exception) -> Job:
        async with self._locks[job_id]:
            job = self.store.load(job_id)
            if job.state is not expected:
                return job
            job.error = f"{type(exc).__name__}: {exc}"
            job.log("error", job.error)
            sm.apply(job, "fail")
            self.store.save(job)
            return job

    def resume_all(self) -> list[str]:
        """Call on startup: restart jobs that were mid-stage when we stopped."""
        ids = [j.id for j in self.store.list() if j.state in RUNNING_STATES]
        if self.on_running:
            for i in ids:
                self.on_running(i)
        return ids
