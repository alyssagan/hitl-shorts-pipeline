"""Domain models for a human-in-the-loop short-video project (a "job")."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    return uuid.uuid4().hex[:12]


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:max_len].strip("-")) or "project"


class JobState(str, Enum):
    CREATED = "created"
    KEYWORDS_RUNNING = "keywords_running"   # machine: keyword / SEO / ranking analysis
    KEYWORDS_REVIEW = "keywords_review"     # HUMAN GATE 1: approve keywords
    SOURCING_RUNNING = "sourcing_running"   # machine: pull assets from Wikipedia, Pexels, ...
    VETTING_RUNNING = "vetting_running"     # machine: license + content risk check, with reasons
    ASSETS_REVIEW = "assets_review"         # HUMAN GATE 2: approve each asset
    SCENES_RUNNING = "scenes_running"       # machine: script + audio + scenes
    SCENES_REVIEW = "scenes_review"         # HUMAN GATE 3: approve scenes
    RENDERING = "rendering"                 # machine: final render
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUNNING_STATES = {
    JobState.KEYWORDS_RUNNING, JobState.SOURCING_RUNNING, JobState.VETTING_RUNNING,
    JobState.SCENES_RUNNING, JobState.RENDERING,
}
REVIEW_STATES = {JobState.KEYWORDS_REVIEW, JobState.ASSETS_REVIEW, JobState.SCENES_REVIEW}
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


Severity = Literal["info", "low", "medium", "high"]


class Flag(BaseModel):
    """One reason the machine thinks an asset needs a closer look."""
    rule: str                               # stable id, e.g. LIC_NC
    severity: Severity
    message: str                            # plain-English why
    evidence: str = ""                      # the exact text/field that triggered it


class Vetting(BaseModel):
    risk: Literal["low", "medium", "high"] = "low"
    flags: list[Flag] = Field(default_factory=list)
    method: str = "rules-v1"
    summary: str = ""                       # how the risk level was derived
    usable: bool = True                     # False when the renderer cannot use the file
    relevance: float | None = None          # 0..1 share of a keyword's words found in the asset's text; None = no topic given


class Asset(BaseModel):
    id: str = Field(default_factory=_id)
    source: str                             # adapter name: wikipedia_commons, pexels, ...
    kind: Literal["image", "video"] = "image"
    path: str                               # absolute path of the downloaded file
    rel_path: str = ""                      # path relative to the project folder
    source_url: str = ""                    # where the file bytes came from
    page_url: str = ""                      # human-readable page for the asset
    title: str = ""
    description: str = ""
    query: str = ""                         # search that found it
    author: str = ""
    license: str = ""
    license_url: str = ""
    attribution: str = ""                   # ready-to-paste credit line
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    mime: str = ""
    sha256: str = ""
    fetched_at: str = Field(default_factory=_now)
    meta: dict[str, Any] = Field(default_factory=dict)   # raw source metadata
    vetting: Vetting | None = None
    status: Literal["pending", "approved", "rejected"] = "pending"
    decision_note: str = ""
    reviewer: str = ""
    reviewed_at: str = ""


class TextRef(BaseModel):
    """Text pulled from a source (e.g. a Wikipedia article) to ground the script."""
    id: str = Field(default_factory=_id)
    source: str
    title: str
    url: str
    path: str
    rel_path: str = ""
    license: str = ""
    license_url: str = ""
    query: str = ""
    chars: int = 0
    retrieved_at: str = Field(default_factory=_now)


class Scene(BaseModel):
    id: str = Field(default_factory=_id)
    index: int
    narration: str
    search_terms: list[str] = Field(default_factory=list)
    clip_path: str | None = None            # video/image asset for this scene
    asset_id: str | None = None             # which sourced asset, if any
    clip_reason: str = ""                   # why this clip was chosen
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
    # Asset sources to pull from after keywords are approved, e.g.
    # ["wikipedia", "commons", "pexels"]. Empty = skip sourcing and use library/clips.
    sources: list[str] = Field(default_factory=list)
    # Free-form per-job overrides passed to the stage (e.g. language, aspect).
    options: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    id: str = Field(default_factory=_id)
    subject: str
    slug: str = ""
    state: JobState = JobState.CREATED
    providers: ProviderChoice = Field(default_factory=ProviderChoice)

    keywords: list[Keyword] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    references: list[TextRef] = Field(default_factory=list)
    # Per-source search notes: what was searched, found, kept, skipped (with reasons) or failed.
    source_notes: list[dict[str, Any]] = Field(default_factory=list)
    script: str = ""
    scenes: list[Scene] = Field(default_factory=list)
    output_path: str | None = None

    # Human feedback given when rejecting a gate; fed into the re-run.
    keyword_feedback: list[str] = Field(default_factory=list)
    asset_feedback: list[str] = Field(default_factory=list)
    scene_feedback: list[str] = Field(default_factory=list)

    error: str | None = None
    failed_from: JobState | None = None     # running state to resume on retry
    events: list[Event] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def model_post_init(self, _ctx: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.subject)

    # ---- convenience -------------------------------------------------
    @property
    def approved_keywords(self) -> list[Keyword]:
        return [k for k in self.keywords if k.approved]

    @property
    def approved_assets(self) -> list[Asset]:
        return [a for a in self.assets if a.status == "approved"]

    @property
    def uses_sources(self) -> bool:
        return bool(self.providers.sources)

    def log(self, kind: str, message: str, **data: Any) -> None:
        self.events.append(Event(kind=kind, message=message, data=data))
        self.updated_at = _now()
