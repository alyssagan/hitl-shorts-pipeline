"""Stage contracts. Each pipeline stage is a small async class behind one of
these Protocols, so providers (RankReel-style ranking, an SEO API, an LLM,
MoneyPrinterTurbo, a local renderer...) can be swapped without touching the
orchestrator or the state machine."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..core.models import Job, Keyword, Scene, TextRef


@dataclass
class StageContext:
    assets_dir: Path                     # where this job's files should be written
    settings: dict[str, Any] = field(default_factory=dict)   # from config/pipeline.toml + job options
    project_dir: Path | None = None      # the project's root folder (sources/, decisions.jsonl, ...)


@dataclass
class SceneResult:
    script: str
    scenes: list[Scene]
    # NEW 2026-09-25: research text write_script() (SCRIPT_RUNNING) fetched this round, for
    # Orchestrator._apply_script to persist onto job.references -- empty for run() (SCENES_RUNNING),
    # which never fetches anything new.
    references: list[TextRef] = field(default_factory=list)


@dataclass
class RenderResult:
    output_path: str
    # {platform: {"title": ..., "caption": ..., "hashtags": [...]}}, e.g. {"tiktok": {...}}.
    # Empty when social-metadata generation is disabled or every platform's call failed.
    social_metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class KeywordStage(Protocol):
    async def run(self, job: Job, ctx: StageContext) -> list[Keyword]:
        """Return candidate keywords with whatever ranking/SEO data the
        provider has. `job.keyword_feedback` holds reviewer notes from earlier
        rejections; use them to refine the next round."""


@runtime_checkable
class SceneStage(Protocol):
    async def write_script(self, job: Job, ctx: StageContext) -> SceneResult:
        """SCRIPT_RUNNING (front of the pipeline): write the narration and split it into scenes.
        No clip_path/asset_id/audio yet -- run() below fills those in later, once keywords/assets
        exist. `job.script_feedback` holds a Gate-1 (script_review) rejection's reviewer notes."""

    async def run(self, job: Job, ctx: StageContext) -> SceneResult:
        """SCENES_RUNNING (back of the pipeline, after Gate 2): job.script/job.scenes already exist
        by now -- match a clip (+ optionally audio) to each one, using the per-scene search term(s)
        KEYWORDS_RUNNING generated. Never rewrites job.script or a scene's narration."""


@runtime_checkable
class RenderStage(Protocol):
    async def run(self, job: Job, ctx: StageContext) -> RenderResult:
        """Render the approved scenes (in `job.scenes` order) and return the
        path of the finished video, plus any generated social-post copy."""
