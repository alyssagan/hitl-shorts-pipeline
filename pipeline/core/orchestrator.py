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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import joblog
from . import state_machine as sm
from . import usage
from .decisions import Actor, actor_from, ai, human, machine
from .models import (
    Asset, CaseFact, ChecklistStatus, FactKind, FactStatus, Job, JobState, Keyword, ProviderChoice,
    QueryGroup, RUNNING_STATES, Scene, SceneCrop, VisualChecklistItem, _now,
)
from .store import JobStore
from ..stages.base import RenderResult, StageContext
from ..stages.registry import Registry
from ..stages.sourcing import write_credits, write_manifests
from ..sources.folder import normalize_selection as normalize_folder_entry
from ..sources.urls import UrlListSource, normalize as normalize_url_entry, parse_url_lines
from ..vetting.rules import RELEVANCE_MIN, RULES, STOPWORDS, VERSION as VETTING_VERSION, asset_text, clean_term, vet_all, vet_asset
from ..vetting.tfidf_relevance import VERSION as TFIDF_VERSION, tfidf_scores
from ..vetting.llm_relevance import VERSION as LLM_SEMANTIC_VERSION
from ..vetting.method_registry import METHOD_VERSIONS

VETTER = machine("vetting-rules", VETTING_VERSION)
MATCHER = machine("clip-matcher", "1")

# The three human review labels (docs/EVALUATION.md). A duplicate may still be relevant to the topic -- it's an
# independent axis from `decision` (approve/reject, which drives the pipeline's approved pool), not a synonym for
# "irrelevant". SUGGESTED_LABEL_REASONS is UI guidance only (the review page's reason dropdown, GET /label-reasons)
# -- `reason` itself is free text, never validated against this list server-side, so it's trivial to extend: edit
# this one list and nothing else needs to change.
LABELS = ("use", "duplicate", "irrelevant")
SUGGESTED_LABEL_REASONS = [
    "wrong case/person", "keyword-only match", "generic imagery", "wrong era",
    "poor visual quality", "unreliable source", "exact duplicate", "near duplicate",
]

# The five things a reviewer can do about a visual checklist item still sitting at "needed"/"candidates_found"
# when Gate 3's pre-render coverage check (#12) flags it -- UI guidance for check_visual_coverage()'s report,
# not itself enforced here (the actual enforcement is: update_checklist_item requires a note for not_available/
# skipped and an asset_id for fulfilled, and approve_scenes refuses to render past an unresolved item without
# an explicit override_note). Never silently substituted -- every path forward is a recorded human decision.
VISUAL_COVERAGE_REMEDIATION_OPTIONS = [
    {"action": "fulfill", "label": "Use a candidate already found",
     "how": "PATCH /jobs/{id}/visual-checklist/{item_id} {status: \"fulfilled\", asset_id, reviewer}",
     "description": "Pick one of the assets already sourced for this and mark it as fulfilling this need. "
                     "For a case-group item, that asset should actually be categorized verified_case/"
                     "unverified_case_candidate -- fulfilling with anything else is flagged as a category "
                     "mismatch, not treated as done (#12)."},
    {"action": "search_more", "label": "Search again / add a link / add your own footage",
     "how": "Gate 2: Next batch, Search again, Add links, or Add your own footage",
     "description": "Go back to sourcing for this specific need before deciding anything."},
    {"action": "not_available", "label": "Mark not available",
     "how": "PATCH /jobs/{id}/visual-checklist/{item_id} {status: \"not_available\", note, reviewer}",
     "description": "Explicitly record that no authentic visual could be found for this -- requires a note "
                     "saying why. Never promised that every case will have accessible footage (#12)."},
    {"action": "skip", "label": "Mark skipped",
     "how": "PATCH /jobs/{id}/visual-checklist/{item_id} {status: \"skipped\", note, reviewer}",
     "description": "Explicitly decide this isn't actually needed for the final video after all -- requires a note."},
    {"action": "render_with_override", "label": "Approve and render anyway",
     "how": "POST /jobs/{id}/scenes/approve {override_note, reviewer}",
     "description": "Proceed without resolving every item right now -- requires a written reason, which is "
                     "recorded in the decision log alongside exactly which items were left unresolved."},
]

# Every relevance-scoring method's CURRENT version -> the formula/prompt it actually uses (pipeline/vetting/
# method_registry.py is the single source of truth; docs/SCORING_CHANGELOG.md has the fuller history). Used by
# _apply_vetting() to record, in each run's `relevance_scoring` decision-log entry, the real formula for whichever
# method(s) actually scored assets that round -- never a stale, one-size-fits-all description (see
# docs/SCORING_CHANGELOG.md for why that used to be wrong).
RELEVANCE_FORMULA_BY_METHOD = {v: d.formula_or_prompt for v, d in METHOD_VERSIONS.items()}

STAGE_CATEGORY = {
    JobState.KEYWORDS_RUNNING: "keywords", JobState.SOURCING_RUNNING: "sourcing",
    JobState.VETTING_RUNNING: "vetting", JobState.SCENES_RUNNING: "scenes",
}  # JobState.RENDERING (and anything else) falls back to "render" -- see run_pending()


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
        usage.configure_prices(self.settings)  # [usage] price table / free-quota info from config/pipeline.toml
        # Where an approved keyword set is copied so a later job on the same subject can reuse it
        # (docs/RUNNING.md "Keyword files, always") -- [library] keywords_dir in config/pipeline.toml, same
        # convention as [library] clips_dir. Deliberately None (feature off) rather than defaulting to a
        # hardcoded "library/keywords" when the key is simply absent from `settings`: every unit test builds
        # its own Orchestrator with settings={} (or without a [library] section), and a hardcoded CWD-relative
        # fallback would have those tests writing real files into this repo's own library/keywords/ every time
        # they run. Production always has the key -- config/pipeline.toml ships it under [library].
        lib_cfg = self.settings.get("library", {})
        self.keywords_library_dir = Path(lib_cfg["keywords_dir"]) if lib_cfg.get("keywords_dir") else None

    # ------------------------------------------------------------------ helpers
    def _who(self, reviewer: str = "", require: bool = False) -> Actor:
        name = (reviewer or "").strip() or self.settings.get("review", {}).get("default_reviewer", "")
        if require and not name.strip():
            raise ValueError("a reviewer name is required for this decision (it is written to the decision log)")
        return human(name)

    QUIET_ACTIONS = {"vetted_asset", "asset_kept", "asset_reviewed", "text_kept", "llm_call"}     # one per asset/call: DEBUG only

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
            who = self._who(reviewer)
            self._rec(job.id, "keywords", "approved_keywords", who, decision="approve",
                      reason=note or "Selected the keywords to build the video around.",
                      subject={"approved": [k.term for k in job.approved_keywords]},
                      outputs={"rejected_by_omission": [k.term for k in job.keywords if not k.approved], "added_by_human": added,
                               "next": "sourcing" if job.uses_sources else "scenes"})
            self._write_keywords_approved_file(job, reviewer=who.name, note=note, added=added)
        return await self._mutate(job_id, fn)

    def _write_keywords_approved_file(self, job: Job, *, reviewer: str, note: str, added: list[str]) -> None:
        """keywords_approved.json in the project folder: the final, human-approved list once Gate 1 closes --
        what sourcing actually searches with (job.approved_keywords). Companion to keywords_proposed.json
        above; see docs/RUNNING.md "Keyword files, always".

        Also copies that same list into `self.keywords_library_dir/<subject slug>/<job id>.json` when that's
        configured (production only -- see __init__) -- a small, topic-organised library a LATER job on the
        same subject can offer to reuse (scripts/poc.py's `choose_library_set`), without ever touching an
        earlier job's own copy: every job has its own id, so nothing already saved here is ever overwritten
        or modified by a later run picking it up."""
        d = self.store.job_dir(job.id)
        data = {
            "job_id": job.id, "subject": job.subject, "approved_at": _now(), "reviewer": reviewer, "note": note,
            "keywords": [k.term for k in job.approved_keywords], "added_by_human": added,
            "rejected_by_omission": [k.term for k in job.keywords if not k.approved],
        }
        (d / "keywords_approved.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        if self.keywords_library_dir is not None:
            try:
                lib_dir = self.keywords_library_dir / job.slug
                lib_dir.mkdir(parents=True, exist_ok=True)
                (lib_dir / f"{job.id}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            except OSError as e:
                joblog.warn("keywords", f"couldn't save a reusable copy of the approved keywords: {e}")

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
        """Record approve/reject for assets. `decisions` = {asset_id: {"decision": "approve"|"reject", "note": "...",
        "label": "use"|"duplicate"|"irrelevant"?, "label_reason": "..."?, "label_note": "..."?, "duplicate_of_asset_id": "..."?}}.
        Approving a HIGH-risk asset requires a note saying why it is acceptable. `label` is optional and independent
        of `decision` -- a duplicate may still be relevant to the topic, so labelling something "duplicate" does not
        by itself change whether it's approved or rejected (docs/EVALUATION.md); set `decision` for that as usual."""
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
                if d.get("label") and d.get("label") not in LABELS:
                    raise ValueError(f"label for {aid} must be one of {LABELS} (or omitted)")
                dup_id = (d.get("duplicate_of_asset_id") or "").strip()
                if dup_id and dup_id not in by_id:
                    raise ValueError(f"duplicate_of_asset_id '{dup_id}' for {aid} is not a known asset in this job")
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
                label = d.get("label") if d.get("label") in LABELS else ""
                dup_id = (d.get("duplicate_of_asset_id") or "").strip()
                if label:
                    self._write_label(job, a, label, actor.name, reason=(d.get("label_reason") or "").strip(),
                                       note=(d.get("label_note") or "").strip(), duplicate_of_asset_id=dup_id)
                v = a.vetting
                self._rec(job.id, "assets", "asset_reviewed", actor, decision=d["decision"],
                          reason=a.decision_note or "(no note)",
                          subject={"asset_id": a.id, "title": a.title, "source": a.source, "url": a.page_url or a.source_url},
                          logic={"machine_risk": v.risk if v else "unvetted", "machine_summary": v.summary if v else "",
                                 "flags_shown_to_reviewer": [f"{f.rule}:{f.severity}" for f in (v.flags if v else [])],
                                 "high_risk_acknowledged": bool(v and v.risk == "high" and d["decision"] == "approve"),
                                 "relevance_label_saved": label or None, "label_reason": (d.get("label_reason") or "").strip() or None,
                                 "duplicate_of_asset_id": dup_id or None})
        return await self._mutate(job_id, fn)

    async def label_asset(self, job_id: str, asset_id: str, label: str, *, reviewer: str = "",
                           reason: str = "", note: str = "", duplicate_of_asset_id: str = "") -> Job:
        """Save a Use/Duplicate/Irrelevant label for one asset, independent of the approve/reject decision gate --
        unlike review_assets() above, this does NOT require JobState.ASSETS_REVIEW. This is what makes it possible
        to label assets in a job that has already moved past asset review (docs/EVALUATION.md, scripts/
        sample_for_review.py) without reopening or re-running anything. It only ever appends a row to
        RELEVANCE_LABELS.jsonl and notes it in the decision log; it never touches the asset's status/decision or
        the pipeline's approved pool."""
        actor = self._who(reviewer, require=True)
        if label not in LABELS:
            raise ValueError(f"label must be one of {LABELS}")
        dup_id = (duplicate_of_asset_id or "").strip()

        def fn(job: Job) -> None:
            a = next((x for x in job.assets if x.id == asset_id), None)
            if a is None:
                raise ValueError(f"unknown asset id {asset_id}")
            if dup_id and not any(x.id == dup_id for x in job.assets):
                raise ValueError(f"duplicate_of_asset_id '{dup_id}' is not a known asset in this job")
            self._write_label(job, a, label, actor.name, reason=reason.strip(), note=note.strip(), duplicate_of_asset_id=dup_id)
            self._rec(job.id, "assets", "label_saved", actor, decision=label, reason=note.strip() or "(no note)",
                      subject={"asset_id": a.id, "title": a.title, "source": a.source},
                      logic={"reason": reason.strip() or None, "duplicate_of_asset_id": dup_id or None,
                             "outside_normal_review": job.state is not JobState.ASSETS_REVIEW,
                             "job_state_at_label_time": job.state.value})
        return await self._mutate(job_id, fn)

    def _write_label(self, job: Job, a: Asset, label: str, who: str, *, reason: str = "", note: str = "",
                      duplicate_of_asset_id: str = "") -> None:
        """Append one labelled example to RELEVANCE_LABELS.jsonl (docs/EVALUATION.md) -- the ground truth
        scripts/evaluate_relevance.py compares the machine's score against. APPEND-ONLY: relabeling an asset adds
        a new row rather than replacing the old one, so nothing here is ever silently overwritten -- a reader
        wanting "the current label" takes the latest row per asset_id by `at`; the full relabeling history stays
        on disk regardless. Snapshots the threshold/decision/method/version AS THEY WERE when this asset was last
        scored -- a later report must never recompute a historical decision against today's config."""
        v = a.vetting
        row = {
            "at": _now(), "job": job.id, "subject": job.subject, "reviewer": who,
            "asset_id": a.id, "label": label, "reason": reason or None, "note": note or None,
            "duplicate_of_asset_id": duplicate_of_asset_id or None,
            "source": a.source, "kind": a.kind, "title": a.title, "description": a.description[:500],
            "page_url": a.page_url, "sha256": a.sha256, "found_by_query": a.query,
            "scored_text": asset_text(a)[:500] if v else "",
            "machine_score": v.relevance if v else None, "machine_why": v.relevance_why if v else "",
            "machine_decision": v.relevance_decision if v else "",
            "relevance_threshold": v.relevance_threshold if v else None,
            "scoring_method": v.scoring_method if v else "", "method_version": v.method_version if v else "",
            "duplicate_flag_fired": bool(v and any(f.rule == "DUPLICATE" for f in v.flags)),
            "keywords": [k.term for k in job.approved_keywords],
        }
        path = self.store.job_dir(job.id) / "RELEVANCE_LABELS.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def score_relevance(self, job_id: str, *, reviewer: str = "") -> Job:
        """Gate 2's "Score relevance now" (requested directly, alongside `defer_relevance` and the stock
        photo budget: "let's pull stock photos first and score relevance later"). A `defer_relevance` round
        never scores relevance on its own -- this is the only thing that ever does it for those pending
        assets, whenever the reviewer is actually ready, not automatically the moment they're pulled. Runs
        the real two-tier TF-IDF/LLM scoring (_score_relevance) against the job's real approved-keyword
        terms and always applies it (`force_score=True` on _apply_vetting) -- `defer_relevance` only silences
        the AUTOMATIC trigger after sourcing, never this explicit one, so calling this always scores for
        real even on a job that still has the option set (it simply won't get auto-rescored again on its
        own after this). Works at any job state with pending assets, same as label_asset()/
        set_asset_identity() above -- not gated to sitting at ASSETS_REVIEW, so re-scoring after the job has
        moved on (a later "search again" round, say) is possible too."""
        actor = self._who(reviewer, require=True)
        job = self.get(job_id)
        pending = sum(1 for a in job.assets if a.status == "pending")
        if not pending:
            raise ValueError("no pending assets to score")
        scores = await self._score_relevance(job)

        def fn(j: Job) -> None:
            self._apply_vetting(j, scores, force_score=True)
        result = await self._mutate(job_id, fn)
        self._rec(job_id, "vetting", "relevance_scored_on_demand", actor, decision="score",
                  reason="Reviewer explicitly asked for relevance scoring now, rather than waiting for it to run "
                         "automatically after the next sourcing round.",
                  outputs={"pending_assets_scored": pending, "job_state_at_score_time": job.state.value})
        return result

    async def set_asset_identity(self, job_id: str, asset_id: str, *, status: str, depicts: str = "",
                                  case_connection: str = "", identity_evidence: str = "", notes: str = "",
                                  reviewer: str = "") -> Job:
        """Record a human's identity determination for one asset (#7, #13): whether it actually shows who/what
        it's claimed to, kept fully independent of relevance score, rights and your use/reject decision. This
        is the ONLY thing that ever moves `identity_status` -- a keyword match, an AI similarity score, or a
        `case`-group search finding it never does, by design. Works at any job state, same as label_asset(),
        so identity review isn't locked to the Gate 2 window; a later pass at this material (or a fact check
        against the case reference sheet, docs/CASE_REFERENCE.md) can still update it."""
        actor = self._who(reviewer, require=True)
        if status not in ("unverified", "verified", "disputed"):
            raise ValueError("status must be one of unverified/verified/disputed")

        def fn(job: Job) -> None:
            a = next((x for x in job.assets if x.id == asset_id), None)
            if a is None:
                raise ValueError(f"unknown asset id {asset_id}")
            before = a.identity_status
            a.identity_status = status
            if depicts.strip():
                a.depicts = depicts.strip()
            if case_connection.strip():
                a.case_connection = case_connection.strip()
            if identity_evidence.strip():
                a.identity_evidence = identity_evidence.strip()
            if notes.strip():
                a.identity_notes = notes.strip()
            a.identity_reviewer, a.identity_reviewed_at = actor.name, _now()
            self._rec(job.id, "assets", "identity_set", actor, decision=status,
                      reason=notes.strip() or identity_evidence.strip() or "(no note)",
                      subject={"asset_id": a.id, "title": a.title, "source": a.source},
                      logic={"before": before, "after": status})
        return await self._mutate(job_id, fn)

    async def set_asset_rights(self, job_id: str, asset_id: str, *, status: str, evidence: str = "",
                                notes: str = "", reviewer: str = "") -> Job:
        """Record a human's rights determination for one asset (#8, #13) -- independent of identity and of
        your use/reject decision. Otherwise `rights_status` is only ever a machine reading of the source's own
        license text (classify_rights_status() in pipeline/vetting/rules.py); this is the one thing that turns
        it into a human-signed-off determination (rights_reviewer/rights_reviewed_at). Never a legal
        conclusion -- just what a human looked at and decided, with their evidence recorded next to it."""
        actor = self._who(reviewer, require=True)
        valid = ("public_domain", "cc0", "open_license", "paid_license", "unresolved")
        if status not in valid:
            raise ValueError(f"status must be one of {valid}")

        def fn(job: Job) -> None:
            a = next((x for x in job.assets if x.id == asset_id), None)
            if a is None:
                raise ValueError(f"unknown asset id {asset_id}")
            before = a.rights_status
            a.rights_status = status
            if evidence.strip():
                a.rights_evidence = evidence.strip()
            if notes.strip():
                a.rights_notes = notes.strip()
            a.rights_reviewer, a.rights_reviewed_at = actor.name, _now()
            self._rec(job.id, "assets", "rights_set", actor, decision=status,
                      reason=notes.strip() or evidence.strip() or "(no note)",
                      subject={"asset_id": a.id, "title": a.title, "source": a.source},
                      logic={"before": before, "after": status})
        return await self._mutate(job_id, fn)

    async def set_asset_category(self, job_id: str, asset_id: str, *, category: str, reviewer: str = "") -> Job:
        """Move an asset's category (#7, #13). Sourcing may propose unverified_case_candidate/
        historical_context/illustrative_stock from the search group that found it (never verified_case) --
        promoting something to `verified_case`, or flagging it as a `reconstruction`, is always this explicit
        human action, never inferred from a score or a search group."""
        actor = self._who(reviewer, require=True)
        valid = ("verified_case", "unverified_case_candidate", "historical_context", "illustrative_stock", "reconstruction")
        if category not in valid:
            raise ValueError(f"category must be one of {valid}")

        def fn(job: Job) -> None:
            a = next((x for x in job.assets if x.id == asset_id), None)
            if a is None:
                raise ValueError(f"unknown asset id {asset_id}")
            before = a.category
            a.category = category
            self._rec(job.id, "assets", "category_set", actor, decision=category,
                      subject={"asset_id": a.id, "title": a.title, "source": a.source},
                      logic={"before": before, "after": category})
        return await self._mutate(job_id, fn)

    def asset_report(self, job_id: str) -> dict[str, Any]:
        """Per-job/per-source breakdown for Gate 2 (#13): how many photos/videos/research items each source
        actually contributed (so "kept: 4" from one source can't be misread as 4 photos when some are video or
        research-only), where every asset currently stands on category/identity/rights, and the job's running
        LLM cost (usage_summary()) -- so "how much has this job cost so far" lives next to everything else you'd
        check at Gate 2 rather than a separate report you have to remember exists. Read-only, computed on
        demand from the job and its decision log; nothing here is a separate source of truth."""
        job = self.get(job_id)

        def counts(field: str) -> dict[str, int]:
            out: dict[str, int] = {}
            for a in job.assets:
                key = getattr(a, field) or "(none)"
                out[key] = out.get(key, 0) + 1
            return out

        by_source: dict[str, dict[str, int]] = {}
        for a in job.assets:
            row = by_source.setdefault(a.source, {"photos": 0, "videos": 0, "research": 0})
            row["photos" if a.kind == "image" else "videos"] += 1
        for r in job.references:
            row = by_source.setdefault(r.source, {"photos": 0, "videos": 0, "research": 0})
            row["research"] += 1
        return {
            "total_assets": len(job.assets), "total_references": len(job.references),
            "by_source": by_source, "by_category": counts("category"),
            "by_identity_status": counts("identity_status"), "by_rights_status": counts("rights_status"),
            "usage": self.usage_summary(job_id),
        }

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

    async def reject_assets(self, job_id: str, feedback: str = "", extra_queries: list[str] | None = None, *,
                             reviewer: str = "", max_queries: int | None = None, per_query: int | None = None,
                             videos_per_query: int | None = None, extra_urls_text: str = "",
                             folder_files: list | None = None, stock_limit: int | None = None,
                             defer_relevance: bool | None = None) -> Job:
        """Send the batch back to sourcing, optionally with new search terms and/or new per-job overrides:
        `max_queries` (how many approved keywords the next round, and every round after it, searches) and
        `per_query`/`videos_per_query` (how many photos/videos each source keeps PER keyword) -- see
        queries_for()/SourcingStage.run in pipeline/stages/sourcing.py. This is what backs both the review
        page's "Search again" (feedback + extra_queries) and its "Next batch" (max_queries only, no feedback
        needed -- it just pulls more of the keywords already approved).

        `stock_limit` (requested directly, "stop go limits depending how much we've pulled already") caps
        how many stock-source assets (pexels/pixabay/unsplash/nasa) the job pulls in total, cumulatively
        across every round from here on -- SourcingStage.run stops/truncates once it's reached. Pass a
        higher number than before to explicitly "continue" pulling once you've seen the scores and decided
        you need more; 0 (or never setting it) stays unlimited, same as every job before this option existed.
        `defer_relevance` (paired with it: "let's pull stock photos first and score relevance later") skips
        relevance scoring for the next round -- risk/license rules still run -- until score_relevance() is
        called explicitly. Both persist in job.providers.options (like max_queries/per_query already do)
        until changed again, not just for one round.

        `extra_urls_text` is the review page's "Add links" box: one URL per line, same `url | note | position`
        format scripts/poc.py's --urls file uses (parsed by pipeline/sources/urls.py's parse_url_lines). New
        links are merged into job.providers.options["urls"] (deduped by URL, existing entries kept), and the
        "urls" source is added to job.providers.sources if this job wasn't already pulling from it -- so a
        job that started with only keyword-based sources can still have a link pasted into it mid-review. The
        next sourcing round re-runs every configured source as usual; UrlListSource's own dedup (known_urls)
        means already-downloaded links are skipped, not re-fetched.

        `folder_files` is the same idea for your own scraped/uploaded material (#10): a list of relative paths
        (each a plain string, or {"path": ..., "note": ...}) under the server's configured `library/scraped`
        folder -- see `pipeline.sources.folder.list_available()` / `GET /jobs/{id}/folder-files` for what's
        available to pick from. Merged into job.providers.options["folder_files"] the same way (deduped by
        path, existing notes kept), and "folder" is added to job.providers.sources if needed. Unlike a pasted
        URL, nothing is downloaded here -- these files already sit on disk -- so the same "queue for the next
        round" approach that's fine for URLs (`base.HttpSource`'s per-source trace note reports what was kept/
        skipped) is fine here too; there's no network failure mode that needs synchronous feedback. Every
        matching file is imported as `owner_submitted=True` with your note as `owner_note` -- picking it for
        this job IS the explicit "this is mine" action (pipeline/sources/folder.py's FolderSource no longer
        imports a folder's entire contents into any job that merely lists `folder` as a source)."""
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            if feedback.strip():
                job.asset_feedback.append(feedback.strip())
            extra = [clean_term(q) for q in (extra_queries or []) if clean_term(q)]
            if extra:
                job.providers.options["extra_queries"] = list(dict.fromkeys(job.providers.options.get("extra_queries", []) + extra))
            if max_queries is not None:
                job.providers.options["max_queries"] = int(max_queries)
            if per_query is not None:
                job.providers.options["per_query"] = int(per_query)
            if videos_per_query is not None:
                job.providers.options["videos_per_query"] = int(videos_per_query)
            if stock_limit is not None:
                job.providers.options["stock_limit"] = int(stock_limit)
            if defer_relevance is not None:
                job.providers.options["defer_relevance"] = bool(defer_relevance)
            new_urls = [e for e in parse_url_lines(extra_urls_text) if e["url"]]
            if new_urls:
                existing = list(job.providers.options.get("urls", []))
                seen = {normalize_url_entry(e, i)["url"] for i, e in enumerate(existing)}
                for e in new_urls:
                    if e["url"] not in seen:
                        existing.append(e)
                        seen.add(e["url"])
                job.providers.options["urls"] = existing
                if "urls" not in job.providers.sources:
                    job.providers.sources = [*job.providers.sources, "urls"]
            new_files = [normalize_folder_entry(e) for e in (folder_files or [])]
            new_files = [e for e in new_files if e["path"]]
            if new_files:
                existing_f = list(job.providers.options.get("folder_files", []))
                seen_f = {normalize_folder_entry(e)["path"] for e in existing_f}
                for e in new_files:
                    if e["path"] not in seen_f:
                        existing_f.append(e)
                        seen_f.add(e["path"])
                job.providers.options["folder_files"] = existing_f
                if "folder" not in job.providers.sources:
                    job.providers.sources = [*job.providers.sources, "folder"]
            sm.apply(job, "reject_assets", note=feedback)
            self._rec(job.id, "assets", "rejected_asset_pool", actor, decision="reject",
                      reason=feedback or "(no reason given)",
                      outputs={"extra_queries": extra, "max_queries": max_queries, "per_query": per_query,
                               "videos_per_query": videos_per_query, "extra_urls": [e["url"] for e in new_urls] or None,
                               "folder_files": [e["path"] for e in new_files] or None,
                               "stock_limit": stock_limit, "defer_relevance": defer_relevance})
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
        """Reorder scenes and/or edit narration/clip/approved/note. Only
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
                for field in ("narration", "clip_path", "approved", "search_terms", "note"):
                    if field in changes:
                        old = getattr(by_id[sid], field)
                        setattr(by_id[sid], field, changes[field])
                        if field == "clip_path":
                            by_id[sid].asset_id = next((a.id for a in job.approved_assets if a.path == changes[field]), None)
                            by_id[sid].clip_reason = "chosen by a human"
                            by_id[sid].crop = None   # a crop chosen for the old clip doesn't apply to the new one
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

    async def add_scene_asset(self, job_id: str, scene_id: str, asset: Asset, *, reviewer: str = "", note: str = "") -> Job:
        """Add one asset -- already downloaded/uploaded to disk by the caller (pipeline/api/app.py's
        scene_upload/scene_from_url; see pipeline/sources/upload.py and UrlListSource.fetch_one) -- and, if it's
        usable, assign it to `scene_id`. This is the backend for Gate 3's "drag a file or a link onto a scene":
        it runs the SAME vet_asset() every sourced asset gets, so a dragged-in clip doesn't skip the risk check
        a searched one would get, only the sourcing round.

        A HIGH-risk asset (an uploaded file with no license info, or a platform video pulled by URL) is still
        added to job.assets as-is when no `note` is given, just left `pending` and NOT assigned to the scene --
        never silently dropped, so nothing already downloaded is lost. Call approve_pending_scene_asset() with a
        note once you have one to finish approving and assigning it. Only allowed while the job is waiting in
        SCENES_REVIEW, same as edit_scenes()."""
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            if job.state is not JobState.SCENES_REVIEW:
                raise sm.TransitionError("footage can only be added during scene review")
            scene = next((s for s in job.scenes if s.id == scene_id), None)
            if scene is None:
                raise ValueError(f"unknown scene id {scene_id}")
            terms = [k.term for k in job.approved_keywords]
            min_rel = float(job.providers.options.get("min_relevance", RELEVANCE_MIN))
            asset.vetting = vet_asset(asset, job.assets + [asset], terms, min_rel)
            job.assets.append(asset)             # always kept, even if it ends up pending -- see docstring
            if not asset.vetting.usable:
                self._rec(job.id, "scenes", "added_scene_asset_unusable", actor, decision="add",
                          reason="; ".join(f.message for f in asset.vetting.flags if f.rule == "LOW_RES") or "not usable",
                          subject={"scene_id": scene_id, "asset_id": asset.id, "source": asset.source},
                          outputs={"risk": asset.vetting.risk})
                return
            if asset.vetting.risk == "high" and not note.strip():
                self._rec(job.id, "scenes", "added_scene_asset_pending", actor, decision="add",
                          reason=asset.vetting.summary,
                          subject={"scene_id": scene_id, "asset_id": asset.id, "source": asset.source},
                          outputs={"risk": asset.vetting.risk, "assigned": False})
                return
            asset.status = "approved"
            asset.decision_note = note.strip()
            asset.reviewer, asset.reviewed_at = actor.name, _now()
            scene.clip_path, scene.asset_id, scene.clip_reason = asset.path, asset.id, "dragged in by a human"
            scene.crop = None   # a crop chosen for whatever was here before doesn't apply to this new clip
            self._rec(job.id, "scenes", "added_scene_asset", actor, decision="add",
                      reason=note.strip() or "(no note)",
                      subject={"scene_id": scene_id, "asset_id": asset.id, "source": asset.source},
                      outputs={"risk": asset.vetting.risk, "assigned": True})
        return await self._mutate(job_id, fn)

    async def add_reviewable_asset(self, job_id: str, asset: Asset, *, reviewer: str = "", note: str = "") -> Job:
        """Add one asset -- already downloaded by the caller (pipeline/api/app.py's assets_add_url; see
        UrlListSource.fetch_one) -- to the Gate 2 pool for normal review (#9). This is the backend for Gate 2's
        "Add links": it runs the SAME vet_asset() every sourced asset gets, so a pasted link doesn't skip the
        risk check a searched one would get, only the sourcing round -- same principle as add_scene_asset(),
        just one gate earlier.

        Unlike add_scene_asset(), this never auto-approves or assigns anything: Gate 2 IS the review step, so
        the asset always lands `pending` and flows through the normal Use/Duplicate/Irrelevant review below,
        whatever its risk. `note` is only recorded in the decision log here, not written to the asset -- that
        happens when you actually decide on it via review_assets(), same as every sourced asset. Only allowed
        while the job is waiting in ASSETS_REVIEW."""
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            if job.state is not JobState.ASSETS_REVIEW:
                raise sm.TransitionError("links can only be added during asset review")
            terms = [k.term for k in job.approved_keywords]
            min_rel = float(job.providers.options.get("min_relevance", RELEVANCE_MIN))
            asset.vetting = vet_asset(asset, job.assets + [asset], terms, min_rel)
            job.assets.append(asset)             # always pending -- Gate 2 itself is the review, see docstring
            self._rec(job.id, "assets", "added_gate2_asset", actor, decision="add",
                      reason=note.strip() or "(no note)",
                      subject={"asset_id": asset.id, "source": asset.source, "url": asset.source_url},
                      outputs={"risk": asset.vetting.risk if asset.vetting else None,
                               "usable": asset.vetting.usable if asset.vetting else None})
        return await self._mutate(job_id, fn)

    async def approve_pending_scene_asset(self, job_id: str, asset_id: str, scene_id: str, *,
                                           reviewer: str = "", note: str = "") -> Job:
        """Finish approving an asset add_scene_asset() left pending (high risk, no note yet) and assign it to
        `scene_id`. Mirrors review_assets()' high-risk-needs-a-note rule, just for one asset outside Gate 2."""
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            if job.state is not JobState.SCENES_REVIEW:
                raise sm.TransitionError("footage can only be approved during scene review")
            asset = next((a for a in job.assets if a.id == asset_id), None)
            if asset is None:
                raise ValueError(f"unknown asset id {asset_id}")
            scene = next((s for s in job.scenes if s.id == scene_id), None)
            if scene is None:
                raise ValueError(f"unknown scene id {scene_id}")
            if asset.vetting and not asset.vetting.usable:
                raise ValueError(f"asset {asset_id} can't be used: "
                                  f"{'; '.join(f.message for f in asset.vetting.flags if f.rule == 'LOW_RES')}")
            if asset.vetting and asset.vetting.risk == "high" and not note.strip():
                raise ValueError(f"'{asset.title or asset.source}' is HIGH risk ({asset.vetting.summary}). "
                                  "Add a note explaining why approving it is OK.")
            asset.status = "approved"
            asset.decision_note = note.strip()
            asset.reviewer, asset.reviewed_at = actor.name, _now()
            scene.clip_path, scene.asset_id, scene.clip_reason = asset.path, asset.id, "dragged in by a human"
            scene.crop = None   # a crop chosen for whatever was here before doesn't apply to this new clip
            self._rec(job.id, "scenes", "added_scene_asset", actor, decision="add",
                      reason=note.strip() or "(no note)",
                      subject={"scene_id": scene_id, "asset_id": asset.id, "source": asset.source},
                      outputs={"risk": asset.vetting.risk if asset.vetting else "unvetted", "assigned": True, "was_pending": True})
        return await self._mutate(job_id, fn)

    async def set_scene_crop(self, job_id: str, scene_id: str, *, center_x: float = 0.5, center_y: float = 0.5,
                              zoom: float = 1.0, reviewer: str = "") -> Job:
        """Manually override the framing MoneyPrinterTurbo's own automatic center-crop would otherwise
        pick for this scene's clip (see pipeline/stages/render/crop.py's module docstring for why MPT
        itself has no hook for this). center_x/center_y are 0..1 fractions of the source frame -- 0.5,
        0.5 is exactly MPT's own default centering; zoom is >=1.0, tightening the window. This only
        records the choice; the render stage bakes an actual cropped file from it. Only allowed during
        SCENES_REVIEW, same as edit_scenes()."""
        actor = self._who(reviewer, require=True)
        if not (0.0 <= center_x <= 1.0) or not (0.0 <= center_y <= 1.0):
            raise ValueError("center_x and center_y must each be between 0 and 1")
        if zoom < 1.0:
            raise ValueError("zoom must be at least 1.0 (1.0 is the widest crop that still fills the frame)")

        def fn(job: Job) -> None:
            if job.state is not JobState.SCENES_REVIEW:
                raise sm.TransitionError("crop can only be set during scene review")
            scene = next((s for s in job.scenes if s.id == scene_id), None)
            if scene is None:
                raise ValueError(f"unknown scene id {scene_id}")
            if not scene.clip_path:
                raise ValueError("this scene has no clip yet -- assign one before cropping it")
            scene.crop = SceneCrop(center_x=center_x, center_y=center_y, zoom=zoom,
                                    updated_by=actor.name, updated_at=_now())
            self._rec(job.id, "scenes", "scene_crop_set", actor, decision="edit",
                      subject={"scene_id": scene_id, "index": scene.index},
                      outputs={"center_x": center_x, "center_y": center_y, "zoom": zoom})
        return await self._mutate(job_id, fn)

    async def remove_scene_crop(self, job_id: str, scene_id: str, *, reviewer: str = "") -> Job:
        """Go back to MoneyPrinterTurbo's own automatic center-crop for this scene."""
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            if job.state is not JobState.SCENES_REVIEW:
                raise sm.TransitionError("crop can only be changed during scene review")
            scene = next((s for s in job.scenes if s.id == scene_id), None)
            if scene is None:
                raise ValueError(f"unknown scene id {scene_id}")
            if scene.crop is not None:
                scene.crop = None
                self._rec(job.id, "scenes", "scene_crop_removed", actor, decision="edit",
                          subject={"scene_id": scene_id, "index": scene.index})
        return await self._mutate(job_id, fn)

    async def approve_scenes(self, job_id: str, approve_all: bool = True, *, reviewer: str = "",
                              override_note: str = "") -> Job:
        actor = self._who(reviewer)

        def fn(job: Job) -> None:
            if job.uses_sources:
                ok = {a.path for a in job.approved_assets}
                bad = [s.index for s in job.scenes if s.clip_path and s.clip_path not in ok]
                if bad:
                    raise ValueError(f"scenes {bad} use a file that is not an approved asset")
            # #12: never silently substitute -- if visual_checklist items are still sitting at needed/
            # candidates_found, or a "fulfilled" case-group item points at a non-case-categorized asset,
            # rendering can't proceed on its own; it either needs every item resolved (fulfilled with real
            # case material/not_available/skipped, each already forced through an explicit note/asset_id by
            # update_checklist_item) or an explicit, recorded reason for rendering past them anyway. A job
            # that never used the checklist (has_checklist False) is never gated -- opt-in, not a new
            # requirement forced onto jobs that don't use this feature.
            coverage = self._visual_coverage(job)
            if not coverage["ready"] and not override_note.strip():
                bits = [f'"{i["label"]}" ({i["status"]})' for i in coverage["unresolved"]]
                bits += [f'"{m["label"]}" (fulfilled with a {m["asset_category"] or "uncategorized"} asset, not case material)'
                         for m in coverage["category_mismatches"]]
                raise ValueError(
                    f"{len(bits)} visual checklist item(s) aren't resolved yet: {'; '.join(bits)}. "
                    "Fulfill each with real case material, mark it not available or skipped (with a note), or "
                    "approve with an explicit override_note explaining why it's OK to render without them.")
            if approve_all:
                for s in job.scenes:
                    s.approved = True
            sm.apply(job, "approve_scenes")
            reason = "Script, order and clips accepted for rendering."
            outputs = {"scenes": len(job.scenes), "order": [s.id for s in job.scenes]}
            if not coverage["ready"]:
                reason += f" Rendered with unresolved visual needs -- override: {override_note.strip()}"
                outputs["rendered_with_unresolved_visual_needs"] = [i["label"] for i in coverage["unresolved"]]
                outputs["rendered_with_category_mismatches"] = [m["label"] for m in coverage["category_mismatches"]]
                outputs["override_note"] = override_note.strip()
            self._rec(job.id, "scenes", "approved_scenes", actor, decision="approve",
                      reason=reason, outputs=outputs)
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

    # ------------------------------------------------------------------ case reference sheet (#6)
    # Ground truth about the case, entirely separate from the pipeline's state machine -- editable at any
    # point in the job's life, never written to by sourcing/vetting/scoring. A search result or an AI
    # similarity score is never treated as confirmation of a fact; only these methods, called by a human
    # through the API, ever create or change one.
    async def set_case_canonical_name(self, job_id: str, canonical_name: str, *, reviewer: str = "") -> Job:
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            job.case_reference.canonical_name = canonical_name.strip()
            job.case_reference.updated_at = _now()
            self._rec(job.id, "case_reference", "canonical_name_set", actor, decision="set",
                      subject={"canonical_name": job.case_reference.canonical_name})
        return await self._mutate(job_id, fn)

    async def add_case_fact(self, job_id: str, kind: str, text: str, *, detail: str = "",
                            source_links: list[str] | None = None, status: str = "confirmed",
                            conflict_note: str = "", reviewer: str = "") -> Job:
        actor = self._who(reviewer, require=True)
        text = text.strip()
        if not text:
            raise ValueError("fact text is required")
        if kind not in FactKind.__args__:
            raise ValueError(f"kind must be one of {FactKind.__args__}")
        if status not in FactStatus.__args__:
            raise ValueError(f"status must be one of {FactStatus.__args__}")

        def fn(job: Job) -> None:
            fact = CaseFact(kind=kind, text=text, detail=detail.strip(), source_links=list(source_links or []),
                            status=status, conflict_note=conflict_note.strip(), added_by=actor.name)
            job.case_reference.facts.append(fact)
            job.case_reference.updated_at = _now()
            self._rec(job.id, "case_reference", "fact_added", actor, decision="add",
                      subject={"fact_id": fact.id, "kind": kind, "text": text}, reason=conflict_note.strip() or None)
        return await self._mutate(job_id, fn)

    async def edit_case_fact(self, job_id: str, fact_id: str, *, text: str | None = None, detail: str | None = None,
                             source_links: list[str] | None = None, status: str | None = None,
                             conflict_note: str | None = None, reviewer: str = "") -> Job:
        actor = self._who(reviewer, require=True)
        if status is not None and status not in FactStatus.__args__:
            raise ValueError(f"status must be one of {FactStatus.__args__}")

        def fn(job: Job) -> None:
            fact = next((f for f in job.case_reference.facts if f.id == fact_id), None)
            if fact is None:
                raise ValueError(f"unknown case fact id {fact_id}")
            if text is not None:
                stripped = text.strip()
                if not stripped:
                    raise ValueError("fact text cannot be blanked out -- remove the fact instead")
                fact.text = stripped
            if detail is not None:
                fact.detail = detail.strip()
            if source_links is not None:
                fact.source_links = list(source_links)
            if status is not None:
                fact.status = status
            if conflict_note is not None:
                fact.conflict_note = conflict_note.strip()
            fact.updated_by = actor.name
            fact.updated_at = _now()
            job.case_reference.updated_at = _now()
            self._rec(job.id, "case_reference", "fact_edited", actor, decision="edit", subject={"fact_id": fact.id})
        return await self._mutate(job_id, fn)

    async def remove_case_fact(self, job_id: str, fact_id: str, *, reviewer: str = "") -> Job:
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            fact = next((f for f in job.case_reference.facts if f.id == fact_id), None)
            if fact is None:
                raise ValueError(f"unknown case fact id {fact_id}")
            job.case_reference.facts = [f for f in job.case_reference.facts if f.id != fact_id]
            job.case_reference.updated_at = _now()
            self._rec(job.id, "case_reference", "fact_removed", actor, decision="remove",
                      subject={"fact_id": fact.id, "text": fact.text})
        return await self._mutate(job_id, fn)

    # ------------------------------------------------------------------ visual checklist (#6)
    # What the video still needs a visual for. generate_visual_checklist() seeds DRAFT items from approved
    # keywords' visual_needed/entity text -- a starting point, not a claim anything was found -- and every
    # item stays fully human-editable afterward. Nothing here is ever marked "fulfilled"/"not_available" by
    # an automated process; finding candidates only moves a status to "candidates_found" at most.
    async def generate_visual_checklist(self, job_id: str, *, reviewer: str = "") -> Job:
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            existing_terms = {i.linked_keyword_term for i in job.visual_checklist if i.linked_keyword_term}
            added = []
            for k in job.approved_keywords:
                if k.term in existing_terms:
                    continue   # don't duplicate an item a previous generate (or a human) already made for this term
                label = k.visual_needed.strip() or k.term
                item = VisualChecklistItem(label=label, linked_keyword_term=k.term, group=k.group, added_by=actor.name)
                job.visual_checklist.append(item)
                existing_terms.add(k.term)
                added.append(item.id)
            self._rec(job.id, "case_reference", "visual_checklist_generated", actor, decision="generate",
                      subject={"added_count": len(added)}, logic={"added_item_ids": added})
        return await self._mutate(job_id, fn)

    async def add_checklist_item(self, job_id: str, label: str, *, linked_keyword_term: str = "",
                                 group: str = "historical", reviewer: str = "") -> Job:
        actor = self._who(reviewer, require=True)
        label = label.strip()
        if not label:
            raise ValueError("label is required")
        if group not in QueryGroup.__args__:
            raise ValueError(f"group must be one of {QueryGroup.__args__}")

        def fn(job: Job) -> None:
            item = VisualChecklistItem(label=label, linked_keyword_term=linked_keyword_term.strip(),
                                       group=group, added_by=actor.name)
            job.visual_checklist.append(item)
            self._rec(job.id, "case_reference", "visual_checklist_item_added", actor, decision="add",
                      subject={"item_id": item.id, "label": label})
        return await self._mutate(job_id, fn)

    async def update_checklist_item(self, job_id: str, item_id: str, *, status: str | None = None,
                                    note: str | None = None, asset_id: str | None = None, reviewer: str = "") -> Job:
        actor = self._who(reviewer, require=True)
        if status is not None and status not in ChecklistStatus.__args__:
            raise ValueError(f"status must be one of {ChecklistStatus.__args__}")

        def fn(job: Job) -> None:
            item = next((i for i in job.visual_checklist if i.id == item_id), None)
            if item is None:
                raise ValueError(f"unknown visual checklist item id {item_id}")
            if asset_id and not any(a.id == asset_id for a in job.assets):
                raise ValueError(f"asset_id '{asset_id}' is not a known asset in this job")
            # #12: not_available/skipped are only ever an explicit human call, never a silent default -- a note
            # saying why has to travel with the status change, not get added later (or never). Same for
            # fulfilled: it has to name which specific asset actually satisfies the need.
            effective_note = note.strip() if note is not None else item.note
            effective_asset_id = asset_id if asset_id is not None else item.asset_id
            if status in ("not_available", "skipped") and not effective_note:
                raise ValueError(f"status={status} needs a note explaining why -- see docs/CASE_REFERENCE.md#12.")
            if status == "fulfilled" and not effective_asset_id:
                raise ValueError("status=fulfilled needs an asset_id -- which approved asset actually fulfills this.")
            if status is not None:
                item.status = status
            if note is not None:
                item.note = note.strip()
            if asset_id is not None:
                item.asset_id = asset_id
            item.updated_by = actor.name
            item.updated_at = _now()
            self._rec(job.id, "case_reference", "visual_checklist_item_updated", actor, decision="update",
                      subject={"item_id": item.id, "status": item.status})
        return await self._mutate(job_id, fn)

    async def remove_checklist_item(self, job_id: str, item_id: str, *, reviewer: str = "") -> Job:
        actor = self._who(reviewer, require=True)

        def fn(job: Job) -> None:
            item = next((i for i in job.visual_checklist if i.id == item_id), None)
            if item is None:
                raise ValueError(f"unknown visual checklist item id {item_id}")
            job.visual_checklist = [i for i in job.visual_checklist if i.id != item_id]
            self._rec(job.id, "case_reference", "visual_checklist_item_removed", actor, decision="remove",
                      subject={"item_id": item.id, "label": item.label})
        return await self._mutate(job_id, fn)

    # ------------------------------------------------------------------ pre-render visual coverage check (#12)
    @staticmethod
    def _visual_coverage(job: Job) -> dict[str, Any]:
        """Never silently substitute generic footage for case material (#12): a read-only report of where
        job.visual_checklist stands, cross-referenced with which scenes reference each item's keyword (best
        effort, via Scene.search_terms). An item is "unresolved" while it's still at needed/candidates_found --
        the two statuses that mean "nobody has actually decided what happens here yet". fulfilled/not_available/
        skipped are all explicit human calls (enforced by update_checklist_item), so once every item lands on
        one of those three, coverage is ready. A job that never used the visual checklist at all (has_checklist
        False) is never gated on this -- it's an opt-in feature, not a new requirement forced onto every job.

        Resolving an item isn't automatically the end of the story, either: `update_checklist_item` will happily
        let "fulfilled" point at ANY approved asset, with no check that it's actually case material -- that's
        the exact silent-substitution risk #12 exists to catch, just one step later than "still unresolved".
        So a `case`-group item marked fulfilled with an asset whose `category` isn't verified_case/
        unverified_case_candidate (or that lost its category, or its asset entirely) is flagged as a
        `category_mismatch` -- not blocked outright (a human may have deliberately decided a historical photo
        is the best available stand-in), but never silent: it counts toward `ready` the same as an unresolved
        item, so it still needs a resolve (repoint it at real case material, or an override note) before
        rendering goes ahead."""
        counts = {s: 0 for s in ChecklistStatus.__args__}
        for item in job.visual_checklist:
            counts[item.status] = counts.get(item.status, 0) + 1
        assets_by_id = {a.id: a for a in job.assets}
        unresolved = []
        mismatches = []
        for item in job.visual_checklist:
            linked_scene_ids = [s.id for s in job.scenes
                                 if item.linked_keyword_term and item.linked_keyword_term in (s.search_terms or [])]
            if item.status in ("needed", "candidates_found"):
                unresolved.append({"id": item.id, "label": item.label, "status": item.status, "group": item.group,
                                    "linked_keyword_term": item.linked_keyword_term, "linked_scene_ids": linked_scene_ids})
            elif item.status == "fulfilled" and item.group == "case":
                asset = assets_by_id.get(item.asset_id)
                category = asset.category if asset else None
                if category not in ("verified_case", "unverified_case_candidate"):
                    mismatches.append({"id": item.id, "label": item.label, "asset_id": item.asset_id,
                                        "asset_category": category, "linked_scene_ids": linked_scene_ids})
        return {"has_checklist": bool(job.visual_checklist), "ready": not unresolved and not mismatches,
                "counts": counts, "unresolved": unresolved, "category_mismatches": mismatches,
                "remediation_options": VISUAL_COVERAGE_REMEDIATION_OPTIONS}

    def check_visual_coverage(self, job_id: str) -> dict[str, Any]:
        """Read-only -- callable at any job state, not just scenes_review, so Gate 3's review page can show
        this before the reviewer even reaches "Approve and render" (see _visual_coverage's docstring)."""
        return self._visual_coverage(self.get(job_id))

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
            usage_token = usage.bind()   # collects every LLM call's tokens/cost made during this stage (core/usage.py)
            t0 = time.monotonic()
            joblog.info("stage", f"START {state.value}", job=job_id, providers=job.providers.model_dump_json()[:200])
            # Provenance (docs/LOGGING.md "Timing"): every stage's start/finish/duration is written to the
            # tamper-evident decision log, not just the ephemeral activity log, so "how long did this take" is
            # part of the permanent, auditable record and survives log rotation. `stage_category` picks which
            # existing decision-log section (Keywords/Sourcing/Vetting/Scenes/Render) it belongs next to.
            stage_category = STAGE_CATEGORY.get(state, "render")
            self._rec(job_id, stage_category, "stage_started", machine("orchestrator"), subject={"stage": state.value})
            try:
                if state is JobState.KEYWORDS_RUNNING:
                    stage = self.registry.keyword_stage(job.providers.keywords)
                    result = await stage.run(job, ctx)
                    result_job = await self._commit(job_id, state, "keywords_ready",
                                                     lambda j: self._apply_keywords(j, stage, result))
                elif state is JobState.SOURCING_RUNNING:
                    stage = self.registry.sourcing_stage()
                    res = await stage.run(job, ctx)
                    job = await self._commit(job_id, state, "sourcing_done", lambda j: self._apply_sourcing(j, stage, res))
                    # Vetting is quick and always follows sourcing: do it right away. The relevance scorer (if
                    # configured) needs an LLM call, so it runs here, before the (sync) vetting commit -- unless
                    # `defer_relevance` is set (requested directly: "pull stock photos first, score relevance
                    # later"), in which case that LLM/TF-IDF call is skipped entirely for now -- there's nothing
                    # to spend it on until a human explicitly asks for it via score_relevance() below. Risk/
                    # license rules still run either way; only the relevance score/threshold-hiding step waits.
                    defer = bool(job.providers.options.get("defer_relevance"))
                    scores = ({}, None) if defer else await self._score_relevance(job)
                    result_job = await self._commit(job_id, JobState.VETTING_RUNNING, "vetting_done", lambda j: self._apply_vetting(j, scores))
                elif state is JobState.VETTING_RUNNING:
                    defer = bool(job.providers.options.get("defer_relevance"))
                    scores = ({}, None) if defer else await self._score_relevance(job)
                    result_job = await self._commit(job_id, state, "vetting_done", lambda j: self._apply_vetting(j, scores))
                elif state is JobState.SCENES_RUNNING:
                    stage = self.registry.scene_stage(job.providers.scenes)
                    res = await stage.run(job, ctx)
                    result_job = await self._commit(job_id, state, "scenes_ready", lambda j: self._apply_scenes(j, stage, res))
                else:
                    # RENDERING
                    if job.uses_sources:
                        credits = write_credits(job, self.store.job_dir(job_id))
                        self._rec(job_id, "render", "credits_written", MATCHER, decision="write",
                                  reason="Attribution for every approved asset and text source.", outputs={"file": str(credits)})
                    stage = self.registry.render_stage(job.providers.render)
                    self._rec(job_id, "render", "render_started", actor_from(stage, machine("renderer")), decision="start",
                              outputs={"scenes": [{"index": s.index, "clip": s.clip_path} for s in job.scenes]})
                    out = await stage.run(job, ctx)
                    result_job = await self._commit(job_id, state, "render_done", lambda j: self._apply_render(j, stage, out))
                duration = round(time.monotonic() - t0, 1)
                self._record_llm_usage(job_id, stage_category)
                self._rec(job_id, stage_category, "stage_finished", machine("orchestrator"), decision="ok",
                          subject={"stage": state.value}, outputs={"duration_seconds": duration})
                if result_job.state in (JobState.COMPLETED, JobState.FAILED):
                    self._write_timing_summary(job_id)
                    self._write_usage_summary(job_id)
                return result_job
            except Exception as exc:  # noqa: BLE001 - any stage failure must land in FAILED
                duration = round(time.monotonic() - t0, 1)
                joblog.error("stage", f"FAILED {state.value} after {duration:.1f}s: {type(exc).__name__}: {exc}")
                joblog.error("stage", "traceback: " + " | ".join(traceback.format_exception(exc)).replace("\n", " ")[-1500:])
                self._record_llm_usage(job_id, stage_category)
                failed_job = await self._fail(job_id, state, exc, duration_seconds=duration)
                if failed_job.state is JobState.FAILED:
                    self._write_timing_summary(job_id)
                    self._write_usage_summary(job_id)
                return failed_job
            finally:
                joblog.info("stage", f"END {state.value} ({time.monotonic() - t0:.1f}s)")
                joblog.unbind(token)
                usage.unbind(usage_token)
        finally:
            self._running.discard(job_id)

    # ---- what each finished stage writes to the job and the decision log ----------
    def _apply_keywords(self, job: Job, stage: Any, result: list[Keyword]) -> None:
        job.keywords = result
        trace = getattr(stage, "last_trace", {})
        self._rec(job.id, "keywords", "proposed_keywords", actor_from(stage, machine("keyword-stage")), decision="propose",
                  logic=trace,
                  outputs={"count": len(result), "keywords": [
                      {"id": k.id, "term": k.term, "rank": k.rank, "volume": k.search_volume, "difficulty": k.difficulty,
                       "why": k.meta.get("why", "")} for k in result]})
        self._write_keywords_proposed_file(job, trace, result)

    def _write_keywords_proposed_file(self, job: Job, trace: dict, result: list[Keyword]) -> None:
        """keywords_proposed.json in the project folder: the candidate list exactly as this round's keyword
        stage produced it -- an LLM's suggestions, seeds read from --keywords-file, a reused library set, or
        whatever a human typed into an editable draft file (scripts/poc.py) -- BEFORE any human decision at
        Gate 1 (docs/RUNNING.md "Keyword files, always"). Always written, for every provider, not just `llm`:
        this is what lets `manual` runs be reviewed/reused the same way `llm` ones are.

        Written every time this stage runs, so after a Gate-1 rejection and re-run it reflects the CURRENT
        round only -- the full history of every round already lives in decisions.jsonl (`proposed_keywords`,
        above), which is append-only and never overwritten."""
        path = self.store.job_dir(job.id) / "keywords_proposed.json"
        data = {
            "generated_at": _now(), "provider": job.providers.keywords, "logic": trace,
            "keywords": [{"id": k.id, "term": k.term, "rank": k.rank, "search_volume": k.search_volume,
                          "difficulty": k.difficulty, "why": k.meta.get("why", "")} for k in result],
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

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
            if a.vetting is not None and a.vetting.scoring_method == "llm-semantic":
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

    def _apply_vetting(self, job: Job, scores: tuple[dict[str, tuple[float, str]], dict[str, tuple[float, str]] | None] | None = None,
                        *, force_score: bool = False) -> None:
        # `defer_relevance` (requested directly, alongside the stock photo budget): skip relevance scoring
        # for this round, but never the risk/license rules -- vet_asset()'s other RULES don't depend on
        # topic_terms at all, so passing an empty list here only suppresses relevance() (and the RELEVANCE_LOW
        # flag it can add); PLATFORM_SOURCE/LIC_*/etc. still fire exactly as if this option weren't set. An
        # asset with no relevance score is never hidden by the review page's min-score slider (below() only
        # hides a *scored* item under the threshold), so a deferred round's pending assets simply all show up,
        # unfiltered, until score_relevance() (an explicit human action, force_score=True) scores them for
        # real. force_score always wins over defer_relevance -- that's what makes it "on demand" rather than
        # a second, conflicting auto-trigger.
        defer = bool(job.providers.options.get("defer_relevance")) and not force_score
        tfidf_scores_batch, llm_scores = ({}, None) if defer else (scores if scores is not None else ({}, None))
        terms = [] if defer else [k.term for k in job.approved_keywords] + list(job.providers.options.get("extra_queries", []))
        min_rel = float(job.providers.options.get("min_relevance", RELEVANCE_MIN))
        vet_all(job.assets, terms, min_rel, llm_scores, tfidf_scores_batch,
                llm_version=LLM_SEMANTIC_VERSION, tfidf_version=TFIDF_VERSION)
        by_risk: dict[str, int] = {}
        for a in job.assets:
            r = a.vetting.risk if a.vetting else "unvetted"
            by_risk[r] = by_risk.get(r, 0) + 1
        methods = {}
        for a in job.assets:
            mv = a.vetting.method_version if a.vetting else ""
            if mv:
                methods[mv] = methods.get(mv, 0) + 1
        joblog.write(self.store.job_dir(job.id), "INFO", "vetting", f"vetted {len(job.assets)} assets", risk=by_risk,
                     scored_against=terms, min_relevance=min_rel, method_versions=methods or None)
        hidden = sum(1 for a in job.assets if a.status == "pending" and a.vetting and a.vetting.relevance is not None and a.vetting.relevance < min_rel)
        # Only the method(s) that actually scored an asset THIS round get their formula listed here -- e.g. a run
        # with no LLM configured only ever shows tfidf-vN's formula, never a stale, one-size-fits-all description
        # (docs/SCORING_CHANGELOG.md explains why that used to be wrong: the formula text never moved when the
        # TF-IDF math was rewritten). Cross-reference a version against docs/SCORING_CHANGELOG.md for its full history.
        methods_used = sorted(methods)
        formulas_used = {m: RELEVANCE_FORMULA_BY_METHOD.get(m, "(unknown method version -- formula not on record; check docs/SCORING_CHANGELOG.md)")
                          for m in methods_used}
        self._rec(job.id, "vetting", "relevance_scoring", VETTER,
                  decision="deferred (relevance scoring skipped this round)" if defer else f"{hidden} hidden below {round(min_rel * 100)}%",
                  reason=("Relevance scoring was deferred for this round ('defer_relevance') -- risk/license rules "
                          "still ran on every asset, but nothing was scored against your keywords, so nothing is "
                          "hidden by the threshold yet. Use 'Score relevance now' when you're ready to score them."
                          if defer else
                          "Every asset got a 0-100% relevance score against the approved keywords. Assets under the threshold are hidden from "
                          "the default review list (still saved on disk and approvable by asking to show hidden)."),
                  logic={"methods_used_this_round": methods_used, "formulas_by_method": formulas_used,
                         "keywords_scored_against": terms, "threshold": min_rel, "deferred": defer,
                         "filler_words_ignored": sorted(STOPWORDS), "changelog": "docs/SCORING_CHANGELOG.md", "doc": "docs/SCORING.md"},
                  outputs={"hidden_count": hidden, "shown_count": sum(1 for a in job.assets if a.status == "pending") - hidden})
        for a in job.assets:
            if a.status != "pending":
                continue                     # already decided by a human in an earlier round
            v = a.vetting
            score = "n/a" if v.relevance is None else f"{round(v.relevance * 100)}%"
            self._rec(job.id, "vetting", "vetted_asset", VETTER, decision=f"risk {v.risk}, relevance {score}", reason=v.summary,
                      subject={"asset_id": a.id, "title": a.title, "source": a.source},
                      logic={"method": v.method, "scoring_method": v.scoring_method or None,
                             "method_version": v.method_version or None, "scoring_fallback_note": v.scoring_fallback_note or None,
                             "relevance_threshold": v.relevance_threshold, "relevance_decision": v.relevance_decision or None,
                             "contribution_note": v.contribution_note or None,
                             "contributions": [c.model_dump() for c in v.contributions],
                             "rules_checked": [r[0] for r in RULES] + ["RELEVANCE_LOW"],
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

    def _apply_render(self, job: Job, stage: Any, out: RenderResult) -> None:
        job.output_path = out.output_path
        job.social_metadata = out.social_metadata
        digest = _sha256(out.output_path) if Path(out.output_path).exists() else ""
        self._rec(job.id, "render", "render_completed", actor_from(stage, machine("renderer")), decision="complete",
                  outputs={"file": out.output_path, "sha256": digest, "social_platforms": sorted(out.social_metadata)})

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

    async def _fail(self, job_id: str, expected: JobState, exc: Exception, duration_seconds: float | None = None) -> Job:
        async with self._locks[job_id]:
            job = self.store.load(job_id)
            if job.state is not expected:
                return job
            job.error = f"{type(exc).__name__}: {exc}"
            job.log("error", job.error)
            sm.apply(job, "fail")
            self.store.save(job)
            self._rec(job_id, "project", "stage_failed", machine("orchestrator"), decision="fail",
                      reason=job.error, subject={"stage": expected.value},
                      outputs={"duration_seconds": duration_seconds} if duration_seconds is not None else {})
            return job

    def timing_summary(self, job_id: str) -> dict[str, Any]:
        """Provenance rollup, computed on demand from the decision log (never a separate source of truth):
        total wall time so far, time spent in machine work per stage (summed across every round -- a stage
        that ran more than once, e.g. after "search again", is counted every time), and time spent waiting on
        a human at each review gate. Works on a job that's still running (the "so far" numbers just stop at
        "now") as well as a finished one. Safe to call anytime, including from the API (`GET /jobs/{id}/timing`)."""
        entries = self.store.decisions(job_id).entries()
        if not entries:
            return {"total_wall_seconds": 0.0, "time_per_stage_seconds": {}, "time_waiting_on_you_seconds": 0.0,
                     "waits": []}

        def parse(ts: str) -> datetime:
            return datetime.fromisoformat(ts)

        started = parse(entries[0]["at"])
        ended = parse(entries[-1]["at"])
        stage_totals: dict[str, float] = {}
        stage_runs: dict[str, int] = {}
        waits: list[dict[str, Any]] = []
        waiting_total = 0.0
        gate_opened_at: str | None = None
        for e in entries:
            if e.get("action") == "stage_finished":
                dur = (e.get("outputs") or {}).get("duration_seconds")
                stg = (e.get("subject") or {}).get("stage", "?")
                if isinstance(dur, (int, float)):
                    stage_totals[stg] = stage_totals.get(stg, 0.0) + dur
                    stage_runs[stg] = stage_runs.get(stg, 0) + 1
                gate_opened_at = e["at"]                       # a human review gate may open right after this
            elif e.get("actor", {}).get("type") == "human" and gate_opened_at is not None:
                wait_s = (parse(e["at"]) - parse(gate_opened_at)).total_seconds()
                waits.append({"closed_by": e.get("action"), "at": e["at"], "waited_seconds": round(wait_s, 1)})
                waiting_total += wait_s
                gate_opened_at = None                          # consumed -- only the FIRST human action closes a gate
        return {
            "total_wall_seconds": round((ended - started).total_seconds(), 1),
            "time_per_stage_seconds": {k: round(v, 1) for k, v in stage_totals.items()},
            "stage_run_counts": stage_runs,
            "time_waiting_on_you_seconds": round(waiting_total, 1),
            "waits": waits,
        }

    def _record_llm_usage(self, job_id: str, stage_category: str) -> None:
        """Turns every LLM call collected (core/usage.py) during the stage that just ran into its own `llm_call`
        decision-log entry -- tokens in/out and an estimated $ cost, next to the timing/decision entries for the
        same stage. A stage that made no LLM calls (e.g. sourcing, rendering) writes nothing. Called on both the
        success and the failure path, so a call made just before a stage crashed (e.g. the script writer got a
        reply, then MoneyPrinterTurbo failed) is still counted, never silently dropped."""
        for c in usage.collect():
            self._rec(job_id, stage_category, "llm_call", ai(c["what"], model=c["model"]), decision="call",
                      subject={"what": c["what"], "model": c["model"]},
                      outputs={"prompt_tokens": c["prompt_tokens"], "completion_tokens": c["completion_tokens"],
                               "total_tokens": c["total_tokens"], "cost_usd": c["cost_usd"]})

    def usage_summary(self, job_id: str) -> dict[str, Any]:
        """Provenance rollup for tokens and $ cost (docs/LOGGING.md "Tokens / cost"), computed on demand from
        every `llm_call` entry this job's decision log has ever recorded -- never a separate source of truth,
        same principle as timing_summary(). Works on a job that's still running as well as a finished one.
        Safe to call anytime, including from the API (`GET /jobs/{id}/usage`)."""
        entries = self.store.decisions(job_id).entries()
        calls = [{"prompt_tokens": (e.get("outputs") or {}).get("prompt_tokens", 0),
                  "completion_tokens": (e.get("outputs") or {}).get("completion_tokens", 0),
                  "total_tokens": (e.get("outputs") or {}).get("total_tokens", 0),
                  "cost_usd": (e.get("outputs") or {}).get("cost_usd", 0.0),
                  "model": (e.get("subject") or {}).get("model", "")}
                 for e in entries if e.get("action") == "llm_call"]
        out = usage.rollup(calls)
        out["free_quota"] = {model: usage.quota_for(model) for model in out["by_model"] if usage.quota_for(model)}
        return out

    def _write_usage_summary(self, job_id: str) -> None:
        summary = self.usage_summary(job_id)
        self._rec(job_id, "project", "usage_summary", machine("orchestrator"), decision="summary",
                  reason="Provenance: every LLM call this job made, tokens in/out, and an estimated $ cost "
                         "(free-tier models cost $0; see [usage] in config/pipeline.toml for the price table), "
                         "computed from this log (docs/LOGGING.md 'Tokens / cost').",
                  outputs=summary)

    def _write_timing_summary(self, job_id: str) -> None:
        summary = self.timing_summary(job_id)
        self._rec(job_id, "project", "job_summary", machine("orchestrator"), decision="summary",
                  reason="Provenance: total time, time per stage, and time spent waiting on a human review, "
                         "computed from this log (docs/LOGGING.md).",
                  outputs=summary)

    def resume_all(self) -> list[str]:
        """Call on startup: restart jobs that were mid-stage when we stopped."""
        ids = [j.id for j in self.store.list() if j.state in RUNNING_STATES]
        if self.on_running:
            for i in ids:
                self.on_running(i)
        return ids
