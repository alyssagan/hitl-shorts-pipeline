"""Pure state machine for the job lifecycle. No I/O, no side effects beyond
mutating the Job passed in, so it is trivial to test and reason about.

    CREATED
      | start
      v
    KEYWORDS_RUNNING --keywords_ready--> KEYWORDS_REVIEW  (human gate 1)
      ^                                     |  approve_keywords (>=1 approved)
      |  reject_keywords (+feedback)        v
      +-------------------------------- SCENES_RUNNING --scenes_ready--> SCENES_REVIEW (gate 2)
                                              ^                             |  approve_scenes
                                              |  reject_scenes (+feedback)  v
                                              +------------------------- RENDERING --render_done--> COMPLETED

    Any running state --fail--> FAILED --retry--> the running state it failed in
    Any non-terminal state --cancel--> CANCELLED
"""
from __future__ import annotations

from .models import Job, JobState, RUNNING_STATES, TERMINAL_STATES

S = JobState

# (from_state, event) -> to_state.  `fail`, `retry` and `cancel` are handled
# separately because their targets depend on the job.
TRANSITIONS: dict[tuple[JobState, str], JobState] = {
    (S.CREATED, "start"): S.KEYWORDS_RUNNING,
    (S.KEYWORDS_RUNNING, "keywords_ready"): S.KEYWORDS_REVIEW,
    (S.KEYWORDS_REVIEW, "approve_keywords"): S.SCENES_RUNNING,
    (S.KEYWORDS_REVIEW, "reject_keywords"): S.KEYWORDS_RUNNING,
    (S.SCENES_RUNNING, "scenes_ready"): S.SCENES_REVIEW,
    (S.SCENES_REVIEW, "approve_scenes"): S.RENDERING,
    (S.SCENES_REVIEW, "reject_scenes"): S.SCENES_RUNNING,
    # A reviewer who decides the keywords were wrong can go back a whole gate.
    (S.SCENES_REVIEW, "back_to_keywords"): S.KEYWORDS_REVIEW,
    (S.RENDERING, "render_done"): S.COMPLETED,
}

EVENTS = {e for _, e in TRANSITIONS} | {"fail", "retry", "cancel"}


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

    try:
        target = TRANSITIONS[(state, event)]
    except KeyError:
        raise TransitionError(f"event '{event}' not allowed in state '{state.value}'") from None

    # ---- guards ------------------------------------------------------
    if event == "approve_keywords" and not job.approved_keywords:
        raise TransitionError("approve at least one keyword before continuing")
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
    elif event == "keywords_ready" or event == "scenes_ready":
        job.error = None

    job.state = target
    job.log("transition", f"{source.value} -> {target.value}", event=event, note=note)
    return job
