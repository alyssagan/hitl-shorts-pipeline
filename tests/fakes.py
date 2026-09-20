"""Deterministic in-memory stages for tests (no network, no MoneyPrinterTurbo)."""
from __future__ import annotations

import asyncio

from pipeline.core.models import Job, Keyword, Scene
from pipeline.stages.base import SceneResult, StageContext
from pipeline.stages.registry import Registry


class FakeKeywords:
    def __init__(self):
        self.calls: list[list[str]] = []

    async def run(self, job: Job, ctx: StageContext):
        self.calls.append(list(job.keyword_feedback))
        return [Keyword(term=f"kw{i}", source="fake", rank=i) for i in range(1, 4)]


class FakeScenes:
    def __init__(self):
        self.calls: list[tuple[list[str], list[str]]] = []

    async def run(self, job: Job, ctx: StageContext):
        self.calls.append(([k.term for k in job.approved_keywords], list(job.scene_feedback)))
        scenes = [Scene(index=i, narration=f"scene {i}", search_terms=[job.approved_keywords[0].term]) for i in range(3)]
        return SceneResult(script="\n\n".join(s.narration for s in scenes), scenes=scenes)


class FakeRender:
    def __init__(self):
        self.orders: list[list[str]] = []

    async def run(self, job: Job, ctx: StageContext):
        self.orders.append([s.narration for s in job.scenes])
        out = ctx.assets_dir / "final.mp4"
        out.write_bytes(b"fake-mp4")
        return str(out)


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
