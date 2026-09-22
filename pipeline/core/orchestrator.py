"""Orchestrator: the only place that combines the state machine, the store and
the pluggable stages, and the one place every decision gets logged.

Human actions (approve/reject/edit) are quick, lock-protected mutations.
Machine work happens in `run_pending(job_id)`, which looks at the job's state
and runs the matching stage. Because it is driven purely by persisted state it
is idempotent and resumable: after a crash, `resume_all()` restarts any job
that was mid-stage.

Every decision (by a human, an AI model, or a plain rule) is appended to the
project's `decisions.jsonl`. See core/decisions.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from . import joblog
from . import state_machine as sm
from .decisions import Actor, actor_from, human, machine
from .models import Asset, Job, JobState, Keyword, ProviderChoice, RUNNING_STATES, Scene, _now
from .store import JobStore
from ..stages.base import StageContext
from ..stages.registry import Registry
from ..stages.sourcing import write_credits, write_manifests
from ..vetting.rules import RELEVANCE_MIN, RULES, SCORING_FORMULA, STOPWORDS, VERSION as VETTING_VERSION, clean_term, vet_all
from ..vetting.tfidf_relevance import tfidf_scores

VETTER = machine("vetting-rules", VETTING_VERSION)
MATCHER = machine("clip-matcher", "1")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
    def _who(self, reviewer: str = "", require: bool = False) -> Actor:
        name = (reviewer or "").strip() or self.settings.get("review", {}).get("default_reviewer", "")
        if require and not name.strip():
            raise ValueError("a reviewer name is required for this decision (it is written to the decision log)")
        return human(name)

    QUIET_ACTIONS = {"vetted_asset", "asset_kept", "asset_reviewed", "text_kept"}     # one per asset: DEBUG only

    def _rec(self, job_id: str, stage: str, action: str, actor: Actor, **kw: Any) -> None:
        self.store.decisions(job_id).record(job_id=job_id, stage=stage, action=action, actor=actor, **kw)
        subj = kw.get("subject") or {}
        joblog.write(self.store.job_dir(job_id), "DEBUG" if action in self.QUIET_ACTIONS else "INFO", "decision",
                     f"{action}: {kw.get('decision', '')}", by=getattr(actor, "name", ""), area=stage,
                     what=subj.get("title") or subj.get("source") or "", why=(kw.get("reason") or "")[:160])

    async def _mutate(self, job_id: str, fn: Callable[[Job], None]) -> Job:
        async with self._locks[job_id]:
            job = self.store.load(job_id)
            fn(job)
            self.store.save(job)
            write_manifests(job, self.store.job_dir(job_id))
        if job.state in RUNNING_STATES and self.on_running:
            self.on_running(job_id)
        return job

    def get(self, job_id: str) -> Job:
        return self.store.load(job_id)

    # ------------------------------------------------------------------ creation
    async def create_job(self, subject: str, providers: ProviderChoice | None = None, *, reviewer: str = "") -> Job:
        job = Job(subject=subject.strip(), providers=providers or ProviderChoice())
        if not job.subject:
            raise ValueError("subject is required")
        job.log("note", "job created")
        self.store.save(job)
        joblog.write(self.store.job_dir(job.id), "INFO", "project", f"created '{job.subject}'", job=job.id,
                     folder=job.slug, sources=",".join(job.providers.sources), keywords=job.providers.keywords)
        self._rec(job.id, "project", "created", self._who(reviewer), decision="create", subject={"subject": job.subject, "folder": job.slug},
                  reason="Project started by a person.",
                  logic={"providers_chosen": job.providers.model_dump()},
                  outputs={"project_dir": str(self.store.job_dir(job.id))})
        return job

    async def start(self, job_id: str, *, reviewer: str = "") -> Job:
        def fn(job: Job) -> None:
            sm.apply(job, "start")
            self._rec(job_id, "project", "started", self._who(reviewer), decision="start")
        return await self._mutate(job_id, fn)

    # ------------------------------------------------------------------ gate 1: keywords
    async def review_keywords(
        self,
        job_id: str,
        approved_ids: list[str],
        extra_terms: list[str] | None = None,
        *,
        reviewer: str = "",
        note: str = "",
    ) -> Job:
        """Approve exactly `approved_ids` (plus any terms the reviewer typed in)."""
        def fn(job: Job) -> None:
            ids = set(approved_ids)
            unknown = ids - {k.id for k in job.keywords}
            if unknown:
                raise ValueError(f"unknown keyword ids: {sorted(unknown)}")
            for k in job.keywords:
                k.approved = k.id in ids
            added = []
            for raw in extra_terms or []:
                term = clean_term(raw)
                if term:
                    job.keywords.append(Keyword(term=term, source="human", approved=True))
                    added.append(term)
            sm.apply(job, "approve_keywords")
            self._rec(job.id, "keywords", "approved_keywords", self._who(reviewer), decision="approve",
                      reason=note or "Selected the keywords to build the video around.",
                      subject={"approved": [k.term for k in job.approved_keywords]},
                      outputs={"rejected_by_omission": [k.term for k in job.keywords if not k.approved], "added_by_human": added,
                               "next": "sourcing" if job.uses_sources else "scenes"})
        return await self._mutate(job_id, fn)

    async def reject_keywords(self, job_id: str, feedback: str = "", *, reviewer: str = "") -> Job:
        def fn(job: Job) -> None:
            if feedback.strip():
                job.keyword_feedback.append(feedback.strip())
            self._rec(job.id, "keywords", "rejected_keywords", self._who(reviewer), decision="reject",
                      reason=feedback or "(no reason given)",
                      outputs={"rejected": [k.term for k in job.keywords]})
            sm.apply(job, "reject_keywords", note=feedback)
            job.keywords = []
        return await self._mutate(job_id, fn)

    # ------------------------------------------------------------------ gate 2: assets
    async def review_assets(self, job_id: str, decisions: dict[str, dict[str, Any]], *, reviewer: str = "") -> Job:
        """Record approve/reject for assets. `decisions` = {asset_id: {"decision": "approve"|"reject", "note": "..."}}.
        Approving a HIGH-risk asset requires a note saying why it is acceptable."""
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            if job.state is not JobState.ASSETS_REVIEW:
                raise sm.TransitionError("assets can only be reviewed during asset review")
            by_id = {a.id: a for a in job.assets}
            for aid, d in decisions.items():
                if aid not in by_id:
                    raise ValueError(f"unknown asset id {aid}")
                if d.get("decision") not in ("approve", "reject"):
                    raise ValueError(f"decision for {aid} must be 'approve' or 'reject'")
                a = by_id[aid]
                risk = a.vetting.risk if a.vetting else "unvetted"
                note = (d.get("note") or "").strip()
                if d["decision"] == "approve":
                    if a.vetting and not a.vetting.usable:
                        raise ValueError(f"asset {aid} can't be used: {'; '.join(f.message for f in a.vetting.flags if f.rule == 'LOW_RES')}")
                    if risk == "high" and not note:
                        raise ValueError(f"asset {aid} ({a.title or a.source}) is HIGH risk. Add a note explaining why approving it is OK.")
            for aid, d in decisions.items():
                a = by_id[aid]
                a.status = "approved" if d["decision"] == "approve" else "rejected"
                a.decision_note = (d.get("note") or "").strip()
                a.reviewer, a.reviewed_at = actor.name, _now()
                label = d.get("label") if d.get("label") in ("relevant", "irrelevant") else ""
                if label:
                    self._write_label(job, a, label, actor.name)
                v = a.vetting
                self._rec(job.id, "assets", "asset_reviewed", actor, decision=d["decision"],
                          reason=a.decision_note or "(no note)",
                          subject={"asset_id": a.id, "title": a.title, "source": a.source, "url": a.page_url or a.source_url},
                          logic={"machine_risk": v.risk if v else "unvetted", "machine_summary": v.summary if v else "",
                                 "flags_shown_to_reviewer": [f"{f.rule}:{f.severity}" for f in (v.flags if v else [])],
                                 "high_risk_acknowledged": bool(v and v.risk == "high" and d["decision"] == "approve"),
                                 "relevance_label_saved": label or None})
        return await self._mutate(job_id, fn)

    def _write_label(self, job: Job, a: Asset, label: str, who: str) -> None:
        """Keep every explicit Use / Irrelevant click as a labelled example (RELEVANCE_LABELS.jsonl in the project folder).
        These are the training/tuning data for relevance scoring: what the machine scored vs. what a human decided."""
        v = a.vetting
        row = {"job": job.id, "subject": job.subject, "label": label, "reviewer": who, "at": _now(),
               "asset_id": a.id, "source": a.source, "kind": a.kind, "title": a.title, "description": a.description[:500],
               "page_url": a.page_url, "sha256": a.sha256, "found_by_query": a.query,
               "machine_score": v.relevance if v else None, "machine_why": v.relevance_why if v else "",
               "keywords": [k.term for k in job.approved_keywords]}
        path = self.store.job_dir(job.id) / "RELEVANCE_LABELS.jsonl"
        keep = []
        if path.exists():                     # one row per asset: a later click replaces an earlier one
            keep = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and json.loads(ln).get("asset_id") != a.id]
        keep.append(json.dumps(row, ensure_ascii=False))
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")

    async def approve_assets(self, job_id: str, *, reviewer: str = "", note: str = "") -> Job:
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            sm.apply(job, "approve_assets")
            approved, rejected = job.approved_assets, [a for a in job.assets if a.status == "rejected"]
            self._rec(job.id, "assets", "approved_asset_pool", actor, decision="approve",
                      reason=note or "Every asset has a decision. The approved set is what the scenes may use.",
                      outputs={"approved": len(approved), "rejected": len(rejected),
                               "approved_high_risk": [a.id for a in approved if a.vetting and a.vetting.risk == "high"]})
        return await self._mutate(job_id, fn)

    async def reject_assets(self, job_id: str, feedback: str = "", extra_queries: list[str] | None = None, *, reviewer: str = "") -> Job:
        """Send the batch back to sourcing, optionally with new search terms."""
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            if feedback.strip():
                job.asset_feedback.append(feedback.strip())
            extra = [clean_term(q) for q in (extra_queries or []) if clean_term(q)]
            if extra:
                job.providers.options["extra_queries"] = list(dict.fromkeys(job.providers.options.get("extra_queries", []) + extra))
            sm.apply(job, "reject_assets", note=feedback)
            self._rec(job.id, "assets", "rejected_asset_pool", actor, decision="reject",
                      reason=feedback or "(no reason given)", outputs={"extra_queries": extra})
        return await self._mutate(job_id, fn)

    # ------------------------------------------------------------------ gate 3: scenes
    async def edit_scenes(
        self,
        job_id: str,
        order: list[str] | None = None,
        edits: dict[str, dict[str, Any]] | None = None,
        *,
        reviewer: str = "",
    ) -> Job:
        """Reorder scenes and/or edit narration/clip/approved flags. Only
        allowed while the job is waiting in SCENES_REVIEW."""
        actor = self._who(reviewer)

        def fn(job: Job) -> None:
            if job.state is not JobState.SCENES_REVIEW:
                raise sm.TransitionError("scenes can only be edited during scene review")
            by_id = {s.id: s for s in job.scenes}
            before = [s.id for s in job.scenes]
            allowed_paths = {a.path for a in job.approved_assets}
            for sid, changes in (edits or {}).items():
                if sid not in by_id:
                    raise ValueError(f"unknown scene id {sid}")
                if job.uses_sources and changes.get("clip_path") and changes["clip_path"] not in allowed_paths:
                    raise ValueError("clip_path must be one of the approved assets")
                for field in ("narration", "clip_path", "approved", "search_terms"):
                    if field in changes:
                        old = getattr(by_id[sid], field)
                        setattr(by_id[sid], field, changes[field])
                        if field == "clip_path":
                            by_id[sid].asset_id = next((a.id for a in job.approved_assets if a.path == changes[field]), None)
                            by_id[sid].clip_reason = "chosen by a human"
                        if field != "approved":
                            self._rec(job.id, "scenes", f"edited_scene_{field}", actor, decision="edit",
                                      subject={"scene_id": sid, "index": by_id[sid].index},
                                      outputs={"before": old, "after": changes[field]})
            if order is not None:
                if sorted(order) != sorted(by_id):
                    raise ValueError("order must list every scene id exactly once")
                job.scenes = [by_id[sid] for sid in order]
            for i, s in enumerate(job.scenes):
                s.index = i
            after = [s.id for s in job.scenes]
            if after != before:
                self._rec(job.id, "scenes", "reordered_scenes", actor, decision="reorder",
                          outputs={"before": before, "after": after})
            job.log("note", "scenes edited", order=after)
        return await self._mutate(job_id, fn)

    async def approve_scenes(self, job_id: str, approve_all: bool = True, *, reviewer: str = "") -> Job:
        actor = self._who(reviewer)

        def fn(job: Job) -> None:
            if job.uses_sources:
                ok = {a.path for a in job.approved_assets}
                bad = [s.index for s in job.scenes if s.clip_path and s.clip_path not in ok]
                if bad:
                    raise ValueError(f"scenes {bad} use a file that is not an approved asset")
            if approve_all:
                for s in job.scenes:
                    s.approved = True
            sm.apply(job, "approve_scenes")
            self._rec(job.id, "scenes", "approved_scenes", actor, decision="approve",
                      reason="Script, order and clips accepted for rendering.",
                      outputs={"scenes": len(job.scenes), "order": [s.id for s in job.scenes]})
        return await self._mutate(job_id, fn)

    async def reject_scenes(self, job_id: str, feedback: str = "", *, reviewer: str = "") -> Job:
        actor = self._who(reviewer)

        def fn(job: Job) -> None:
            if feedback.strip():
                job.scene_feedback.append(feedback.strip())
            sm.apply(job, "reject_scenes", note=feedback)
            self._rec(job.id, "scenes", "rejected_scenes", actor, decision="reject", reason=feedback or "(no reason given)")
        return await self._mutate(job_id, fn)

    async def back_to_keywords(self, job_id: str, *, reviewer: str = "") -> Job:
        def fn(job: Job) -> None:
            sm.apply(job, "back_to_keywords")
            self._rec(job.id, "project", "went_back_to_keywords", self._who(reviewer), decision="go back")
        return await self._mutate(job_id, fn)

    async def back_to_assets(self, job_id: str, *, reviewer: str = "") -> Job:
        def fn(job: Job) -> None:
            sm.apply(job, "back_to_assets")
            self._rec(job.id, "project", "went_back_to_assets", self._who(reviewer), decision="go back")
        return await self._mutate(job_id, fn)

    # ------------------------------------------------------------------ lifecycle
    async def cancel(self, job_id: str, *, reviewer: str = "") -> Job:
        def fn(job: Job) -> None:
            sm.apply(job, "cancel")
            self._rec(job.id, "project", "cancelled", self._who(reviewer), decision="cancel")
        return await self._mutate(job_id, fn)

    async def retry(self, job_id: str, *, reviewer: str = "") -> Job:
        def fn(job: Job) -> None:
            sm.apply(job, "retry")
            self._rec(job.id, "project", "retried", self._who(reviewer), decision="retry")
        return await self._mutate(job_id, fn)

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
            ctx = StageContext(assets_dir=self.store.assets_dir(job_id), settings=self.settings,
                               project_dir=self.store.job_dir(job_id))
            token = joblog.bind(self.store.job_dir(job_id))
            t0 = time.monotonic()
            joblog.info("stage", f"START {state.value}", job=job_id, providers=job.providers.model_dump_json()[:200])
            try:
                if state is JobState.KEYWORDS_RUNNING:
                    stage = self.registry.keyword_stage(job.providers.keywords)
                    result = await stage.run(job, ctx)
                    return await self._commit(job_id, state, "keywords_ready",
                                              lambda j: self._apply_keywords(j, stage, result))
                if state is JobState.SOURCING_RUNNING:
                    stage = self.registry.sourcing_stage()
                    res = await stage.run(job, ctx)
                    job = await self._commit(job_id, state, "sourcing_done", lambda j: self._apply_sourcing(j, stage, res))
                    # Vetting is quick and always follows sourcing: do it right away. The relevance scorer (if
                    # configured) needs an LLM call, so it runs here, before the (sync) vetting commit.
                    scores = await self._score_relevance(job)
                    return await self._commit(job_id, JobState.VETTING_RUNNING, "vetting_done", lambda j: self._apply_vetting(j, scores))
                if state is JobState.VETTING_RUNNING:
                    scores = await self._score_relevance(job)
                    return await self._commit(job_id, state, "vetting_done", lambda j: self._apply_vetting(j, scores))
                if state is JobState.SCENES_RUNNING:
                    stage = self.registry.scene_stage(job.providers.scenes)
                    res = await stage.run(job, ctx)
                    return await self._commit(job_id, state, "scenes_ready", lambda j: self._apply_scenes(j, stage, res))
                # RENDERING
                if job.uses_sources:
                    credits = write_credits(job, self.store.job_dir(job_id))
                    self._rec(job_id, "render", "credits_written", MATCHER, decision="write",
                              reason="Attribution for every approved asset and text source.", outputs={"file": str(credits)})
                stage = self.registry.render_stage(job.providers.render)
                self._rec(job_id, "render", "render_started", actor_from(stage, machine("renderer")), decision="start",
                          outputs={"scenes": [{"index": s.index, "clip": s.clip_path} for s in job.scenes]})
                out = await stage.run(job, ctx)
                return await self._commit(job_id, state, "render_done", lambda j: self._apply_render(j, stage, out))
            except Exception as exc:  # noqa: BLE001 - any stage failure must land in FAILED
                joblog.error("stage", f"FAILED {state.value} after {time.monotonic() - t0:.1f}s: {type(exc).__name__}: {exc}")
                joblog.error("stage", "traceback: " + " | ".join(traceback.format_exception(exc)).replace("\n", " ")[-1500:])
                return await self._fail(job_id, state, exc)
            finally:
                joblog.info("stage", f"END {state.value} ({time.monotonic() - t0:.1f}s)")
                joblog.unbind(token)
        finally:
            self._running.discard(job_id)

    # ---- what each finished stage writes to the job and the decision log ----------
    def _apply_keywords(self, job: Job, stage: Any, result: list[Keyword]) -> None:
        job.keywords = result
        self._rec(job.id, "keywords", "proposed_keywords", actor_from(stage, machine("keyword-stage")), decision="propose",
                  logic=getattr(stage, "last_trace", {}),
                  outputs={"count": len(result), "keywords": [
                      {"id": k.id, "term": k.term, "rank": k.rank, "volume": k.search_volume, "difficulty": k.difficulty,
                       "why": k.meta.get("why", "")} for k in result]})

    def _apply_sourcing(self, job: Job, stage: Any, res: Any) -> None:
        job.assets.extend(res.assets)
        job.references.extend(res.references)
        job.source_notes.extend(res.trace)
        for t in res.trace:
            self._rec(job.id, "sourcing", "searched_source", stage.actor, decision="error" if t.get("error") else
                      ("skipped" if t.get("skipped_source") else "searched"),
                      subject={"source": t.get("source"), "query": t.get("query")},
                      reason=t.get("error") or t.get("skipped_source") or "",
                      outputs={k: t[k] for k in ("found", "kept", "skipped") if k in t},
                      logic={"queries_come_from": "keywords a human approved (plus extra terms from asset review)",
                             "request_log": f"sources/{t.get('source')}/requests.jsonl"})
        for a in res.assets:
            self._rec(job.id, "sourcing", "asset_kept", stage.actor, decision="keep",
                      subject={"asset_id": a.id, "title": a.title, "source": a.source, "kind": a.kind},
                      outputs={"file": a.rel_path, "sha256": a.sha256, "url": a.source_url, "page": a.page_url,
                               "license": a.license, "author": a.author, "found_by_query": a.query})
        for r in res.references:
            self._rec(job.id, "sourcing", "text_kept", stage.actor, decision="keep",
                      subject={"title": r.title, "source": r.source},
                      outputs={"file": r.rel_path, "url": r.url, "license": r.license, "chars": r.chars})

    async def _score_relevance(self, job: Job) -> tuple[dict[str, tuple[float, str]], dict[str, tuple[float, str]] | None]:
        """Two-tier relevance scoring (docs/SCORING.md): a deterministic TF-IDF baseline (pipeline/vetting/
        tfidf_relevance.py) is computed locally, for free, for every pending asset -- no network, no rate limit,
        no "tier" to run out of. The LLM (pipeline/vetting/llm_relevance.py), if configured, is then asked for a
        second, meaning-aware opinion only on the assets whose TF-IDF score is "borderline": close enough to the
        approval threshold (`[relevance] borderline_band` in config/pipeline.toml) that the cheap score alone
        isn't a confident call. A hard per-round cap (`max_llm_per_round`) bounds free-tier calls even if many
        assets are borderline at once; the closest-to-the-threshold ones win the cap, since those are the ones
        the algorithm is least sure about.

        As before, "search again" re-enters vetting with the same still-pending assets plus new ones, and an
        asset that already has a good LLM score from an earlier round is never resent or silently downgraded
        (vet_asset() in rules.py keeps its prior llm-semantic score when it isn't resent this round).

        Returns (tfidf_scores, llm_scores_or_None). llm_scores is None only when no LLM scorer is configured at
        all (vet_all() then labels every asset plain "tfidf", not framed as a failure); otherwise it's a dict
        (possibly {}) of just the borderline assets that were actually asked -- assets missing from it, including
        every non-borderline one, simply keep their TF-IDF score, which was always the intended outcome for them."""
        terms = [k.term for k in job.approved_keywords] + list(job.providers.options.get("extra_queries", []))
        pending = [a for a in job.assets if a.status == "pending"]
        if not terms or not pending:
            return {}, None
        tfidf = tfidf_scores(pending, terms)
        joblog.info("relevance", f"tfidf baseline scored {len(tfidf)} of {len(pending)} pending asset(s)")

        scorer = self.registry.relevance_scorer()
        if scorer is None:
            return tfidf, None

        rel_cfg = self.settings.get("relevance", {}) if isinstance(self.settings, dict) else {}
        band = float(rel_cfg.get("borderline_band", 0.15))
        cap = int(rel_cfg.get("max_llm_per_round", 40))
        min_rel = float(job.providers.options.get("min_relevance", RELEVANCE_MIN))

        need, reused, confident = [], 0, 0
        for a in pending:
            if a.vetting is not None and a.vetting.relevance_method == "llm-semantic":
                reused += 1
                continue
            score = tfidf.get(a.id, (None, ""))[0]
            if score is not None and abs(score - min_rel) > band:
                confident += 1                          # TF-IDF alone is confident enough; save the LLM call
                continue
            need.append(a)
        if reused:
            joblog.info("relevance", f"keeping {reused} previously LLM-scored asset(s) from an earlier round")
        if confident:
            joblog.info("relevance", f"{confident} asset(s) are clearly scored by TF-IDF (not within {band} of the "
                                     f"{round(min_rel * 100)}% threshold), skipping the LLM for them")
        if len(need) > cap:
            need.sort(key=lambda a: abs((tfidf.get(a.id, (min_rel, ""))[0]) - min_rel if tfidf.get(a.id) else 0.0))
            joblog.warn("relevance", f"{len(need)} borderline asset(s) exceed the per-round cap of {cap}; scoring the "
                                     f"{cap} closest to the threshold this round, the rest keep their TF-IDF score for now")
            need = need[:cap]
        if not need:
            return tfidf, {}
        try:
            llm_scores = await scorer.score(job, need, terms)
            return tfidf, llm_scores
        except Exception as exc:  # noqa: BLE001 - scoring must never fail a run; vet_all() falls back to tfidf per asset
            joblog.warn("relevance", f"LLM relevance scoring unavailable this round, using the TF-IDF score instead: {type(exc).__name__}: {exc}")
            return tfidf, {}

    def _apply_vetting(self, job: Job, scores: tuple[dict[str, tuple[float, str]], dict[str, tuple[float, str]] | None] | None = None) -> None:
        tfidf_scores_batch, llm_scores = scores if scores is not None else ({}, None)
        terms = [k.term for k in job.approved_keywords] + list(job.providers.options.get("extra_queries", []))
        min_rel = float(job.providers.options.get("min_relevance", RELEVANCE_MIN))
        vet_all(job.assets, terms, min_rel, llm_scores, tfidf_scores_batch)
        by_risk: dict[str, int] = {}
        for a in job.assets:
            r = a.vetting.risk if a.vetting else "unvetted"
            by_risk[r] = by_risk.get(r, 0) + 1
        methods = {}
        for a in job.assets:
            m = a.vetting.relevance_method if a.vetting else ""
            if m:
                methods[m.split(" (")[0]] = methods.get(m.split(" (")[0], 0) + 1
        joblog.write(self.store.job_dir(job.id), "INFO", "vetting", f"vetted {len(job.assets)} assets", risk=by_risk,
                     scored_against=terms, min_relevance=min_rel, relevance_method=methods or None)
        hidden = sum(1 for a in job.assets if a.status == "pending" and a.vetting and a.vetting.relevance is not None and a.vetting.relevance < min_rel)
        self._rec(job.id, "vetting", "relevance_scoring", VETTER, decision=f"{hidden} hidden below {round(min_rel * 100)}%",
                  reason="Every asset got a 0-100% relevance score against the approved keywords. Assets under the threshold are hidden from "
                         "the default review list (still saved on disk and approvable by asking to show hidden).",
                  logic={"formula": SCORING_FORMULA, "keywords_scored_against": terms, "threshold": min_rel,
                         "filler_words_ignored": sorted(STOPWORDS), "doc": "docs/SCORING.md"},
                  outputs={"hidden_count": hidden, "shown_count": sum(1 for a in job.assets if a.status == "pending") - hidden})
        for a in job.assets:
            if a.status != "pending":
                continue                     # already decided by a human in an earlier round
            v = a.vetting
            score = "n/a" if v.relevance is None else f"{round(v.relevance * 100)}%"
            self._rec(job.id, "vetting", "vetted_asset", VETTER, decision=f"risk {v.risk}, relevance {score}", reason=v.summary,
                      subject={"asset_id": a.id, "title": a.title, "source": a.source},
                      logic={"method": v.method, "rules_checked": [r[0] for r in RULES] + ["RELEVANCE_LOW"],
                             "fired": [f.model_dump() for f in v.flags],
                             "how_risk_is_set": "highest severity among fired rules; info flags don't raise it",
                             "auto_approves_or_rejects": False},
                      outputs={"usable_by_renderer": v.usable, "relevance": v.relevance})

    def _apply_scenes(self, job: Job, stage: Any, res: Any) -> None:
        job.script = res.script
        job.scenes = res.scenes
        self._rec(job.id, "scenes", "generated_script_and_scenes", actor_from(stage, machine("scene-stage")), decision="propose",
                  logic=getattr(stage, "last_trace", {}),
                  outputs={"script_chars": len(res.script), "scenes": len(res.scenes)})
        for s in res.scenes:
            self._rec(job.id, "scenes", "clip_assigned", MATCHER, decision="assign" if s.clip_path else "none available",
                      reason=s.clip_reason or ("no clip was available for this scene" if not s.clip_path else ""),
                      subject={"scene_id": s.id, "index": s.index, "narration": s.narration[:120]},
                      outputs={"clip": s.clip_path, "asset_id": s.asset_id})

    def _apply_render(self, job: Job, stage: Any, out: str) -> None:
        job.output_path = out
        digest = _sha256(out) if Path(out).exists() else ""
        self._rec(job.id, "render", "render_completed", actor_from(stage, machine("renderer")), decision="complete",
                  outputs={"file": out, "sha256": digest})

    async def _commit(self, job_id: str, expected: JobState, event: str, apply: Callable[[Job], None]) -> Job:
        async with self._locks[job_id]:
            job = self.store.load(job_id)
            if job.state is not expected:      # cancelled / changed while the stage ran
                job.log("note", f"discarded {expected.value} result; job is now {job.state.value}")
                self.store.save(job)
                self._rec(job_id, "project", "discarded_result", machine("orchestrator"), decision="discard",
                          reason=f"{expected.value} finished after the job moved to {job.state.value}")
                return job
            apply(job)
            sm.apply(job, event)
            self.store.save(job)
            write_manifests(job, self.store.job_dir(job_id))
            joblog.write(self.store.job_dir(job_id), "INFO", "state", f"{expected.value} -> {job.state.value}", event=event)
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
            self._rec(job_id, "project", "stage_failed", machine("orchestrator"), decision="fail",
                      reason=job.error, subject={"stage": expected.value})
            return job

    def resume_all(self) -> list[str]:
        """Call on startup: restart jobs that were mid-stage when we stopped."""
        ids = [j.id for j in self.store.list() if j.state in RUNNING_STATES]
        if self.on_running:
            for i in ids:
                self.on_running(i)
        return ids
