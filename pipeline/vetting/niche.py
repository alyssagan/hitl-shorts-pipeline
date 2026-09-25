"""Stage 3's "Evaluation Engine Output Schema" (requested directly, full spec): for every asset the
risk/relevance vetting engine (rules.py) already processed this round, produce the requested JSON-shaped
record -- niche_evaluated / relevance_score (1-10) / aesthetic_fit / risk_assessment / reasoning / action
-- IF the job has a niche set (Job.niche, pipeline/core/models.py). A job with no niche gets no
niche_evaluation at all (None on every asset's Vetting), exactly as before this module existed.

This is deterministic and explainable, the same philosophy as rules.py's risk rules ("plain rules you can
read below") -- it does NOT add a new LLM call. It never re-judges risk or relevance itself: it only
re-expresses what vet_asset() already decided (Vetting.relevance/risk/summary) through the niche's
aesthetic lens (pipeline/niches.py, keyed on which source adapter the asset came from).

`action` is a SUGGESTED label for this schema only. It is NEVER applied to Asset.status -- Gate 2 still
requires an explicit human Approve/Reject on every asset, exactly as always (rules.py's own docstring:
"Nothing here approves or rejects an asset. Every asset still goes to a human."). Wiring `action` to
auto-approve/auto-reject would break that guarantee, so nothing in this module or its caller
(Orchestrator._apply_vetting) ever touches Asset.status.
"""
from __future__ import annotations

from typing import Any

from ..core.models import Asset, Job, NicheEvalAction, NicheEvaluation
from ..niches import NICHE_PENALIZE_SOURCES, NICHE_REWARD_SOURCES, niche_label

VERSION = "niche-eval-v1"


def _aesthetic_fit(source: str, niche: str) -> tuple[str, str]:
    label = niche_label(niche)
    if source in NICHE_REWARD_SOURCES.get(niche, set()):
        return "Excellent", f"source '{source}' is exactly the kind of material {label} content should draw from"
    if source in NICHE_PENALIZE_SOURCES.get(niche, set()):
        return "Jarring", f"source '{source}' clashes with the {label} aesthetic (see docs/NICHES.md)"
    return "Acceptable", f"source '{source}' is neither specifically rewarded nor penalized for {label}"


def _action(risk: str, relevance_decision: str, aesthetic_fit: str) -> NicheEvalAction:
    """Mirrors how a reviewer would actually triage: a high-risk flag or a confident "not relevant" call is
    always disqualifying; a middling risk, an unscored asset, or a clashing aesthetic all mean "look at this
    one yourself" rather than a clean pass; everything else is a clean pass -- SUGGESTED, never applied."""
    if risk == "high" or relevance_decision == "not_relevant":
        return "Rejected"
    if risk == "medium" or aesthetic_fit == "Jarring" or relevance_decision == "":
        return "Flagged for Review"
    return "Approved"


def evaluate_asset(a: Asset, niche: str | None) -> NicheEvaluation | None:
    """None when there's nothing to evaluate: no niche set on the job, or the asset hasn't been risk-vetted
    at all yet (a.vetting is None -- defensive; vet_all() always sets it once a round has run)."""
    if not niche or a.vetting is None:
        return None
    v = a.vetting
    relevance_score = None
    if v.relevance is not None:
        # 1-10 per the requested schema (never 0 -- a genuine 0.0 relevance still reports the floor, 1,
        # since the schema has no "0" rung; the real signal for "not relevant at all" is `action`, not a
        # score of zero).
        relevance_score = max(1, min(10, round(v.relevance * 10)))
    aesthetic_fit, aesthetic_why = _aesthetic_fit(a.source, niche)
    risk_assessment = v.summary or "not yet risk-vetted"
    reasoning = "; ".join([
        f"relevance {round(v.relevance * 100)}%" if v.relevance is not None else "not yet scored for relevance",
        f"risk {v.risk}",
        aesthetic_why,
    ])
    action = _action(v.risk, v.relevance_decision, aesthetic_fit)
    return NicheEvaluation(niche_evaluated=niche_label(niche), relevance_score=relevance_score,
                            aesthetic_fit=aesthetic_fit, risk_assessment=risk_assessment, reasoning=reasoning,
                            action=action, method=VERSION)


def apply_to_job(job: Job) -> None:
    """Called from Orchestrator._apply_vetting right after vet_all() -- (re)computes niche_evaluation for
    every asset that has a Vetting, every round, the same "recomputed but never overrides a human decision"
    pattern rules.py's rights_status classification uses (this has nothing to sign off that would outrank a
    re-run, since `action` is only ever a suggestion, never a decision)."""
    for a in job.assets:
        if a.vetting is not None:
            a.vetting.niche_evaluation = evaluate_asset(a, job.niche)


def evaluate_job(job: Job) -> list[dict[str, Any]]:
    """Every vetted asset's evaluation record, in the exact schema requested (JSON-serializable dicts:
    niche_evaluated/relevance_score/aesthetic_fit/risk_assessment/reasoning/action -- `method` is this
    pipeline's own bookkeeping, not part of the requested schema, so it's dropped here) plus `asset_id`,
    needed to tell one record apart from another in a per-job list the schema itself doesn't provide for.
    Used by GET /jobs/{id}/niche-evaluation (docs/NICHES.md). Empty list for a job with no niche set, or
    before any asset has been vetted."""
    out: list[dict[str, Any]] = []
    for a in job.assets:
        ev = a.vetting.niche_evaluation if a.vetting else None
        if ev is None:
            continue
        d = ev.model_dump()
        d.pop("method", None)
        d["asset_id"] = a.id
        out.append(d)
    return out
