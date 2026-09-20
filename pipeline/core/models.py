"""Domain models for a human-in-the-loop short-video job."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    return uuid.uuid4().hex[:12]


class JobState(str, Enum):
    CREATED = "created"
    KEYWORDS_RUNNING = "keywords_running"   # machine: keyword / SEO / ranking analysis
    KEYWORDS_REVIEW = "keywords_review"     # HUMAN GATE 1: approve keywords
    SCENES_RUNNING = "scenes_running"       # machine: script + audio + scene assets
    SCENES_REVIEW = "scenes_review"         # HUMAN GATE 2: approve scenes
    RENDERING = "rendering"                 # machine: final render
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUNNING_STATES = {JobState.KEYWORDS_RUNNING, JobState.SCENES_RUNNING, JobState.RENDERING}
REVIEW_STATES = {JobState.KEYWORDS_REVIEW, JobState.SCENES_REVIEW}
TERMINAL_STATES = {JobState.COMPLETED, JobState.CANCELLED}


class Keyword(BaseModel):
    id: str = Field(default_factory=_id)
    term: str
    source: str = "unknown"                 # which provider produced it
    search_volume: int | None = None
    difficulty: float | None = None         # 0-100, provider defined
    rank: int | None = None                 # ranking position, if the provider has one
    meta: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False


class Scene(BaseModel):
    id: str = Field(default_factory=_id)
    index: int
    narration: str
    search_terms: list[str] = Field(default_factory=list)
    clip_path: str | None = None            # video asset for this scene
    audio_path: str | None = None           # narration audio for this scene
    duration: float | None = None
    approved: bool = False


class Event(BaseModel):
    at: str = Field(default_factory=_now)
    kind: str                               # "transition", "note", "error"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class ProviderChoice(BaseModel):
    """Which pluggable implementation each stage should use. Names map to the
    registries in pipeline/stages/registry.py."""
    keywords: str = "llm"
    scenes: str = "mpt"
    render: str = "mpt"
    # Free-form per-job overrides passed to the stage (e.g. language, aspect).
    options: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    id: str = Field(default_factory=_id)
    subject: str
    state: JobState = JobState.CREATED
    providers: ProviderChoice = Field(default_factory=ProviderChoice)

    keywords: list[Keyword] = Field(default_factory=list)
    script: str = ""
    scenes: list[Scene] = Field(default_factory=list)
    output_path: str | None = None

    # Human feedback given when rejecting a gate; fed into the re-run.
    keyword_feedback: list[str] = Field(default_factory=list)
    scene_feedback: list[str] = Field(default_factory=list)

    error: str | None = None
    failed_from: JobState | None = None     # running state to resume on retry
    events: list[Event] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    # ---- convenience -------------------------------------------------
    @property
    def approved_keywords(self) -> list[Keyword]:
        return [k for k in self.keywords if k.approved]

    def log(self, kind: str, message: str, **data: Any) -> None:
        self.events.append(Event(kind=kind, message=message, data=data))
        self.updated_at = _now()
