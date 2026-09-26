"""Deterministic in-memory stages for tests (no network, no MoneyPrinterTurbo)."""
from __future__ import annotations

import asyncio

from pipeline.core.models import Job, Keyword, Scene
from pipeline.stages.base import RenderResult, SceneResult, StageContext
from pipeline.stages.registry import Registry


class FakeKeywords:
    def __init__(self):
        self.calls: list[list[str]] = []

    async def run(self, job: Job, ctx: StageContext):
        self.calls.append(list(job.keyword_feedback))
        return [Keyword(term=f"kw{i}", source="fake", rank=i) for i in range(1, 4)]


class FakeSuggestKeywords:
    """Stands in for LLMKeywordStage under the "llm" provider name in orchestrator.suggest_keywords()
    tests -- accepts the same already_covered/extra_feedback kwargs the real stage does (recording them
    for assertions) without making a network call.

    A real job uses the SAME "llm" provider for both Gate 1's first batch (run_pending's KEYWORDS_RUNNING)
    and every later suggest_keywords() call, so a test harness that reaches ASSETS_REVIEW via the normal
    create_job/start/run_pending/review_keywords flow calls this stage once for Gate 1 before ever calling
    suggest_keywords() itself. The FIRST call returns distinct "gate1 kwN" terms (so approving
    job.keywords[0] at Gate 1 never accidentally pre-approves one of `terms` below); every call after that
    returns `terms` -- the fixed, obviously-distinct-group pair a suggest_keywords() test actually checks."""
    def __init__(self, terms: list[tuple[str, str]] | None = None):
        # [(term, group), ...] returned by every run() call after the first.
        self.terms = terms or [("suggested case term", "case"), ("suggested stock term", "stock")]
        self.calls: list[dict] = []
        self.last_trace: dict = {"note": "fake suggestion stage"}

    async def run(self, job: Job, ctx: StageContext, *, already_covered=None, extra_feedback: str = ""):
        self.calls.append({"already_covered": list(already_covered or []), "extra_feedback": extra_feedback})
        if len(self.calls) == 1:
            return [Keyword(term=f"gate1 kw{i}", group="historical", source="llm:fake", rank=i) for i in range(1, 4)]
        return [Keyword(term=t, group=g, source="llm:fake") for t, g in self.terms]


class FakeScenes:
    """Two-phase fake matching the current SceneStage contract (docs/PIPELINE_STAGES.md):
    write_script() (SCRIPT_RUNNING, now near the front -- no keywords exist yet) builds narration-only
    scenes deterministically from job.subject; run() (SCENES_RUNNING, shrunk, near the back) assumes
    those scenes already exist and only "fills in" a clip per scene, exactly mirroring
    pipeline/stages/scenes/mpt.py's MptSceneStage -- it never rebuilds job.scenes from job.keywords
    itself."""
    def __init__(self, n_scenes: int = 3):
        self.n_scenes = n_scenes
        self.script_calls: list[list[str]] = []
        self.calls: list[tuple[list[str], list[str]]] = []      # (all scene search_terms, scene_feedback)

    async def write_script(self, job: Job, ctx: StageContext):
        self.script_calls.append(list(job.script_feedback))
        scenes = [Scene(index=i, narration=f"scene {i}") for i in range(self.n_scenes)]
        return SceneResult(script="\n\n".join(s.narration for s in scenes), scenes=scenes)

    async def run(self, job: Job, ctx: StageContext):
        """Deliberately leaves clip_path/asset_id unset (same as before this reorder) -- several tests
        (e.g. tests/test_scene_crop.py's test_requires_the_scene_to_have_a_clip_first) rely on a scene
        reaching SCENES_REVIEW with no clip yet, then assigning one explicitly via add_scene_asset()/
        approve_pending_scene_asset(). A real MptSceneStage.run() only sets clip_path when its
        ClipSource actually finds something -- this fake models the "nothing found yet" case, the one
        every other test's setup wants."""
        terms = sorted({t for s in job.scenes for t in s.search_terms})
        self.calls.append((terms, list(job.scene_feedback)))
        return SceneResult(script=job.script, scenes=job.scenes)


class FakeRender:
    def __init__(self):
        self.orders: list[list[str]] = []

    async def run(self, job: Job, ctx: StageContext):
        self.orders.append([s.narration for s in job.scenes])
        out = ctx.assets_dir / "final.mp4"
        out.write_bytes(b"fake-mp4")
        return RenderResult(output_path=str(out))


class Boom:
    """Fails N times then succeeds; usable as any stage kind via subclass."""
    def __init__(self, inner, failures=1):
        self.inner, self.failures = inner, failures

    async def run(self, job, ctx):
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("provider exploded")
        return await self.inner.run(job, ctx)


class Slow:
    def __init__(self, inner, delay=0.2):
        self.inner, self.delay = inner, delay

    async def run(self, job, ctx):
        await asyncio.sleep(self.delay)
        return await self.inner.run(job, ctx)


def fake_registry(keywords=None, scenes=None, render=None) -> tuple[Registry, dict]:
    stages = {"keywords": keywords or FakeKeywords(), "scenes": scenes or FakeScenes(), "render": render or FakeRender()}
    reg = Registry()
    reg.register_keywords("fake", lambda: stages["keywords"])
    reg.register_scenes("fake", lambda: stages["scenes"])
    reg.register_render("fake", lambda: stages["render"])
    return reg, stages
