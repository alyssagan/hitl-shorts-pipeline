"""Stage contracts. Each pipeline stage is a small async class behind one of
these Protocols, so providers (RankReel-style ranking, an SEO API, an LLM,
MoneyPrinterTurbo, a local renderer...) can be swapped without touching the
orchestrator or the state machine."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..core.models import Job, Keyword, Scene


@dataclass
class StageContext:
    assets_dir: Path                     # where this job's files should be written
    settings: dict[str, Any] = field(default_factory=dict)   # from config/pipeline.toml + job options


@dataclass
class SceneResult:
    script: str
    scenes: list[Scene]


@runtime_checkable
class KeywordStage(Protocol):
    async def run(self, job: Job, ctx: StageContext) -> list[Keyword]:
        """Return candidate keywords with whatever ranking/SEO data the
        provider has. `job.keyword_feedback` holds reviewer notes from earlier
        rejections; use them to refine the next round."""


@runtime_checkable
class SceneStage(Protocol):
    async def run(self, job: Job, ctx: StageContext) -> SceneResult:
        """Build script, narration audio and scene assets from
        `job.approved_keywords`. `job.scene_feedback` holds reviewer notes."""


@runtime_checkable
class RenderStage(Protocol):
    async def run(self, job: Job, ctx: StageContext) -> str:
        """Render the approved scenes (in `job.scenes` order) and return the
        path of the finished video."""
