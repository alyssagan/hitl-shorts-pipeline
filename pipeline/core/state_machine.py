"""Pure state machine for the job lifecycle. No I/O, no side effects beyond
mutating the Job passed in, so it is trivial to test and reason about.

Reordered 2026-09-25 (docs/PIPELINE_STAGES.md): the script is written and approved FIRST; keywords
(one search term per scene) are derived from it, then auto-approved by default straight through
KEYWORDS_REVIEW -- folded into Gate 2 rather than their own screen (job.providers.options
["auto_approve_keywords"], default True; Orchestrator._auto_approve_keywords). The state and the
approve_keywords/reject_keywords events are unchanged and still fully work as a real human gate --
turning auto-approval off is enough to require one again, no other rewiring needed.

    CREATED
      | start
      v
    SCRIPT_RUNNING --script_ready--> SCRIPT_REVIEW  (human gate 1)
      ^                                  |  approve_script (script + scenes exist)
      |  reject_script (+feedback)       |
      +----------------------------------+
                                         v
    KEYWORDS_RUNNING --keywords_ready--> KEYWORDS_REVIEW  (auto-approved by default)
      ^                                     |  approve_keywords (>=1 approved)
      |  reject_keywords (+feedback)        |
      +-------------------------------------+
                                            v
             job has sources?  no ------------------------------+
                                yes                             |
                                 v                              |
    SOURCING_RUNNING --sourcing_done--> VETTING_RUNNING         |
      ^                                     | vetting_done      |
      |                                     v                   |
      |  reject_assets (+feedback)     ASSETS_REVIEW (human gate 2)
      +-------------------------------      | approve_assets    |
                                            v                   v
                                        SCENES_RUNNING --scenes_ready--> SCENES_REVIEW (human gate 3)
                                            ^                                |  approve_scenes
                                            |  reject_scenes (+feedback)     v
                                            +---------------------------  RENDERING --render_done--> COMPLETED

    Any running state --fail--> FAILED --retry--> the running state it failed in
    Any non-terminal state --cancel--> CANCELLED
"""
from __future__ import annotations

from .models import Job, JobState, RUNNING_STATES, TERMINAL_STATES

S = JobState

# (from_state, event) -> to_state.  `approve_keywords`, `approve_script`, `fail`, `retry` and `cancel`
# are handled separately because their targets depend on the job.
TRANSITIONS: dict[tuple[JobState, str], JobState] = {
    (S.CREATED, "start"): S.SCRIPT_RUNNING,
    (S.SCRIPT_RUNNING, "script_ready"): S.SCRIPT_REVIEW,
    (S.SCRIPT_REVIEW, "reject_script"): S.SCRIPT_RUNNING,
    (S.KEYWORDS_RUNNING, "keywords_ready"): S.KEYWORDS_REVIEW,
    (S.KEYWORDS_REVIEW, "reject_keywords"): S.KEYWORDS_RUNNING,
    (S.SOURCING_RUNNING, "sourcing_done"): S.VETTING_RUNNING,
    (S.VETTING_RUNNING, "vetting_done"): S.ASSETS_REVIEW,
    (S.ASSETS_REVIEW, "approve_assets"): S.SCENES_RUNNING,
    (S.ASSETS_REVIEW, "reject_assets"): S.SOURCING_RUNNING,
    (S.ASSETS_REVIEW, "back_to_keywords"): S.KEYWORDS_REVIEW,
    (S.SCENES_RUNNING, "scenes_ready"): S.SCENES_REVIEW,
    (S.SCENES_REVIEW, "approve_scenes"): S.RENDERING,
    (S.SCENES_REVIEW, "reject_scenes"): S.SCENES_RUNNING,
    # A reviewer who decides the keywords/assets were wrong can go back a whole gate.
    (S.SCENES_REVIEW, "back_to_keywords"): S.KEYWORDS_REVIEW,
    (S.SCENES_REVIEW, "back_to_assets"): S.ASSETS_REVIEW,
    (S.RENDERING, "render_done"): S.COMPLETED,
}

EVENTS = {e for _, e in TRANSITIONS} | {"approve_keywords", "approve_script", "fail", "retry", "cancel"}


class TransitionError(Exception):
    """The event is not allowed in the job's current state, or a guard failed."""


def can(job: Job, event: str) -> bool:
    try:
        _target(job, event)
        return True
    except TransitionError:
        return False


def allowed_events(job: Job) -> list[str]:
    return sorted(e for e in EVENTS if can(job, e))


def _target(job: Job, event: str) -> JobState:
    state = job.state
    if event == "fail":
        if state not in RUNNING_STATES:
            raise TransitionError(f"cannot fail from {state.value}")
        return S.FAILED
    if event == "retry":
        if state is not S.FAILED or job.failed_from is None:
            raise TransitionError("only a failed job can be retried")
        return job.failed_from
    if event == "cancel":
        if state in TERMINAL_STATES:
            raise TransitionError(f"job is already {state.value}")
        return S.CANCELLED
    if event == "approve_script":
        if state is not S.SCRIPT_REVIEW:
            raise TransitionError(f"event 'approve_script' not allowed in state '{state.value}'")
        if not job.scenes or not job.script.strip():
            raise TransitionError("there is no script to approve yet")
        return S.KEYWORDS_RUNNING
    if event == "approve_keywords":
        if state is not S.KEYWORDS_REVIEW:
            raise TransitionError(f"event 'approve_keywords' not allowed in state '{state.value}'")
        if not job.approved_keywords:
            raise TransitionError("approve at least one keyword before continuing")
        # Jobs with asset sources go through sourcing -> vetting -> asset review first.
        return S.SOURCING_RUNNING if job.uses_sources else S.SCENES_RUNNING

    try:
        target = TRANSITIONS[(state, event)]
    except KeyError:
        raise TransitionError(f"event '{event}' not allowed in state '{state.value}'") from None

    # ---- guards ------------------------------------------------------
    if event == "approve_assets":
        pending = [a for a in job.assets if a.status == "pending"]
        if pending:
            raise TransitionError(f"{len(pending)} asset(s) still need a decision (approve or reject each one)")
        if not job.approved_assets:
            raise TransitionError("approve at least one asset before continuing")
    if event in ("reject_assets", "back_to_assets") and not job.uses_sources:
        raise TransitionError("this job does not use asset sources")
    if event == "approve_scenes":
        if not job.scenes:
            raise TransitionError("there are no scenes to approve")
        if not all(s.approved for s in job.scenes):
            raise TransitionError("every scene must be approved before rendering")
    return target


def apply(job: Job, event: str, note: str = "") -> Job:
    """Validate and apply `event`, mutating and returning the job."""
    target = _target(job, event)
    source = job.state

    if event == "fail":
        job.failed_from = source
    elif event == "retry":
        job.error = None
        job.failed_from = None
    elif event in ("script_ready", "keywords_ready", "sourcing_done", "vetting_done", "scenes_ready"):
        job.error = None

    job.state = target
    job.log("transition", f"{source.value} -> {target.value}", event=event, note=note)
    return job
