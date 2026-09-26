#!/usr/bin/env python3
"""Deterministic, no-network dry run of the two-pass shot-list fallback feature (docs/ROADMAP.md
"two-pass hybrid"), driven through the REAL Orchestrator/state machine/vetting/clip-matching code --
only the three things that would otherwise need live credentials (the keyword-writing LLM, the script
writer LLM, and the asset sources' HTTP calls) are swapped for small, fully deterministic fakes local
to this script. Everything else -- SourcingStage.queries_for()'s Pass-1/Pass-2 scoping, vet_asset()'s
real risk/relevance rules, Orchestrator._scenes_needing_fallback()/approve_assets()'s Pass-2 trigger,
AssetClipSource's real ordered shot-list matching -- runs unmodified.

Scenario: 2 scenes, deliberately built with zero shared vocabulary between them (clip matching is exact
word overlap -- pipeline/stages/scenes/clips.py's _tokens() -- so any shared word would let scene 0's
asset falsely "cover" scene 1 too, silently defeating this demo).
  Scene 0 ("riverside warehouse murder scene photograph"): the fake source finds a match for the
    scene's OWN primary query on Pass 1 -- no fallback needed.
  Scene 1 ("grocer household portrait image"): the fake source finds NOTHING for the primary query on
    Pass 1 (deliberately) -- so Gate 2 should auto-trigger Pass 2, activating this scene's first broader
    alternative ("french quarter tenement exterior view"), which the fake source DOES match, and the job
    should come back to asset review with that new candidate pending -- never auto-approved.

Run: `python3 scripts/dry_run_demo.py` from the repo root (or anywhere; it uses its own temp project
directory and never touches config/pipeline.toml, .env or the real `projects/` folder).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.core.models import Job, Keyword, ProviderChoice, RUNNING_STATES  # noqa: E402
from pipeline.core.orchestrator import Orchestrator                            # noqa: E402
from pipeline.core.store import JobStore                                       # noqa: E402
from pipeline.stages.base import SceneResult, StageContext                     # noqa: E402
from pipeline.stages.registry import Registry                                  # noqa: E402
from pipeline.stages.scenes.mpt import MptSceneStage                           # noqa: E402
from pipeline.sources.base import SourceContext, SourceResult                  # noqa: E402

# ------------------------------------------------------------------ the scenario's fixed data
SCENE_NARRATION = [
    "On a quiet New Orleans night in 1919, an unseen attacker crept through the darkness.",
    "Detectives combed the scene for any trace of the man the papers had already started calling the Axeman.",
]

# term -> (alternatives...), one entry per scene, in scene order. Deliberately ZERO shared words between
# scene 0's and scene 1's vocabulary (across every term/alternative) -- AssetClipSource/
# scene_has_shot_list_coverage match on exact word overlap (pipeline/stages/scenes/clips.py's _tokens()),
# so any shared word (even an incidental one, like a repeated "1919") would make scene 1 look covered by
# scene 0's asset and silently defeat the whole point of this demo.
SCENE_SHOT_LISTS = [
    ("riverside warehouse murder scene photograph", []),
    ("grocer household portrait image", ["french quarter tenement exterior view", "storyville residential block picture"]),
]

# Every query the fake source can actually "find" something for (anything else comes back empty --
# a real, honest miss, not an error). Deliberately excludes scene 1's own primary term.
FINDABLE_QUERIES = {"riverside warehouse murder scene photograph", "french quarter tenement exterior view"}


class DemoKeywordStage:
    """Stands in for LLMKeywordStage under KEYWORDS_RUNNING. Returns one Keyword per scene, each
    carrying the fixed shot list above (term = most specific, alternatives = broader fallbacks) --
    exactly the shape run_for_scenes()/SCENE_PROMPT produces for real, just without an LLM call."""
    last_trace = {"note": "fake keyword stage for the dry run demo"}

    async def run_for_scenes(self, job: Job, ctx: StageContext, scenes) -> list[Keyword]:
        out = []
        for scene in scenes:
            term, alts = SCENE_SHOT_LISTS[scene.index]
            out.append(Keyword(term=term, group="case", source="fake-llm", scene_index=scene.index, alternatives=list(alts)))
        return out


class DemoSceneStage(MptSceneStage):
    """Real MptSceneStage, except write_script() is replaced with a fixed, deterministic script/scene
    split (no LLM, no MPT). run() -- the part actually under test -- is inherited UNCHANGED: when
    job.uses_sources is True (true here) it matches clips via the real AssetClipSource against the
    real approved-asset pool, never touching self.client at all."""

    async def write_script(self, job: Job, ctx: StageContext) -> SceneResult:
        from pipeline.core.models import Scene
        scenes = [Scene(index=i, narration=t) for i, t in enumerate(SCENE_NARRATION)]
        return SceneResult(script="\n\n".join(SCENE_NARRATION), scenes=scenes)


class DemoSourceAdapter:
    """Stands in for every real HTTP source (Wikipedia/Pexels/Pixabay/...), registered under the name
    "commons" (pipeline/sources/groups.py's GROUP_POOLS routes a "case"-group term -- what our fake
    keyword stage uses -- to every ARCHIVE_SOURCES name except wikipedia; "commons" is simply the one
    we picked, its real CommonsSource is never touched). For each query SourcingStage actually asks for
    (queries_for()'s Pass-1/Pass-2 scoping decides that, unmodified), returns exactly one clean, high-
    resolution, public-domain image Asset when the query is in FINDABLE_QUERIES, and nothing at all
    otherwise -- deterministic, no network."""
    name = "commons"
    label = "Demo (fake)"

    async def fetch(self, queries: list[str], ctx: SourceContext) -> SourceResult:
        from pipeline.core.models import Asset
        assets = []
        trace = []
        for q in queries:
            if q not in FINDABLE_QUERIES:
                trace.append({"source": self.name, "query": q, "found": 0, "kept": 0, "skipped": [], "kind": "media"})
                continue
            slug = q.replace(" ", "-")
            assets.append(Asset(
                source=self.name, kind="image", path=f"library/demo/{slug}.jpg",
                source_url=f"https://example.invalid/{slug}.jpg",
                page_url=f"https://example.invalid/pages/{slug}",
                title=q, description=f"A public-domain photo matching '{q}'.", query=q,
                author="Demo Archive", license="Public Domain", license_url="https://example.invalid/pd",
                width=1920, height=1080, mime="image/jpeg", sha256=hashlib.sha256(slug.encode()).hexdigest(),
            ))
            trace.append({"source": self.name, "query": q, "found": 1, "kept": 1, "skipped": [], "kind": "media"})
        return SourceResult(assets=assets, references=[], trace=trace)


def build_demo_registry() -> Registry:
    reg = Registry()
    reg.register_keywords("fake-llm", DemoKeywordStage)
    reg.register_scenes("demo", lambda: DemoSceneStage(client=None, clips=None, generate_audio=False))
    reg.register_source("commons", DemoSourceAdapter)
    return reg


def summarize(job: Job) -> dict:
    return {
        "state": job.state.value,
        "scenes": [{"index": s.index, "search_terms": s.search_terms, "clip_path": s.clip_path,
                    "clip_reason": s.clip_reason} for s in job.scenes],
        "assets": [{"id": a.id[:8], "status": a.status, "query": a.query, "title": a.title} for a in job.assets],
        "fallback_only_queries": job.providers.options.get("fallback_only_queries"),
        "fallback_queries_tried": job.providers.options.get("fallback_queries_tried"),
    }


def banner(title: str) -> None:
    print(f"\n{'=' * 10} {title} {'=' * 10}")


async def run_until_gate(orch: Orchestrator, job_id: str) -> Job:
    job = orch.get(job_id)
    while job.state in RUNNING_STATES:
        job = await orch.run_pending(job_id)
    return job


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dry_run_demo_"))
    try:
        orch = Orchestrator(JobStore(tmp), build_demo_registry(), settings={})

        providers = ProviderChoice(keywords="fake-llm", scenes="demo", sources=["commons"])
        job = await orch.create_job("The Axeman of New Orleans (dry run demo)", providers, reviewer="demo-harness")
        job = await orch.start(job.id, reviewer="demo-harness")
        banner("SCRIPT_RUNNING -> SCRIPT_REVIEW")
        job = await run_until_gate(orch, job.id)
        print(json.dumps(summarize(job), indent=2))
        assert job.state.value == "script_review"

        job = await orch.approve_script(job.id, reviewer="demo-harness")
        banner("KEYWORDS_RUNNING (auto-approves) -> SOURCING_RUNNING -> VETTING_RUNNING -> ASSETS_REVIEW (Pass 1)")
        job = await run_until_gate(orch, job.id)
        print(json.dumps(summarize(job), indent=2))
        assert job.state.value == "assets_review"
        pending = [a for a in job.assets if a.status == "pending"]
        assert len(pending) == 1, f"expected exactly 1 Pass-1 asset (scene 0's), got {len(pending)}"
        assert pending[0].query == "riverside warehouse murder scene photograph"
        print("-> Pass 1 found exactly ONE asset (scene 0's own primary query). Scene 1's primary query came up "
              "empty, as designed -- its shot-list alternatives were never searched this round.")

        job = await orch.review_assets(job.id, {pending[0].id: {"decision": "approve"}}, reviewer="demo-harness")
        banner("close-gate-2, attempt 1: should auto-redirect to Pass 2 (scene 1 has no coverage)")
        job = await orch.approve_assets(job.id, reviewer="demo-harness")
        print(json.dumps(summarize(job), indent=2))
        assert job.state.value == "sourcing_running", f"expected auto-redirect to sourcing_running, got {job.state.value}"
        assert job.providers.options.get("fallback_only_queries") == ["french quarter tenement exterior view"]
        print("-> Confirmed: approve_assets() did NOT close the gate. It activated scene 1's next shot-list "
              "query ('french quarter tenement exterior view') and sent the job back to sourcing -- the exact same "
              "reject_assets() transition a human's own 'Search again' click uses, just triggered by the "
              "orchestrator (decision log action: auto_fallback_sourcing).")

        banner("Pass 2: sourcing_running -> vetting_running -> ASSETS_REVIEW again")
        job = await run_until_gate(orch, job.id)
        print(json.dumps(summarize(job), indent=2))
        assert job.state.value == "assets_review"
        pending2 = [a for a in job.assets if a.status == "pending"]
        assert len(pending2) == 1, f"expected exactly 1 new Pass-2 asset, got {len(pending2)}"
        assert pending2[0].query == "french quarter tenement exterior view"
        print("-> Pass 2's round searched ONLY the one activated fallback query -- fallback_only_queries scoped "
              "it correctly -- and found exactly the one new asset for it. Nothing was auto-approved: it's "
              "sitting pending, waiting for a human decision, same as any other sourcing round.")

        job = await orch.review_assets(job.id, {pending2[0].id: {"decision": "approve"}}, reviewer="demo-harness")
        banner("close-gate-2, attempt 2: should succeed now (every scene covered)")
        job = await orch.approve_assets(job.id, reviewer="demo-harness")
        print(json.dumps(summarize(job), indent=2))
        assert job.state.value == "scenes_running", f"expected scenes_running, got {job.state.value}"

        banner("SCENES_RUNNING -> SCENES_REVIEW (real AssetClipSource clip-matching)")
        job = await run_until_gate(orch, job.id)
        print(json.dumps(summarize(job), indent=2))
        assert job.state.value == "scenes_review"
        by_index = {s.index: s for s in job.scenes}
        assert by_index[0].clip_path is not None, "scene 0 should have matched its Pass-1 asset"
        assert by_index[1].clip_path is not None, "scene 1 should have matched its Pass-2 fallback asset"
        assert by_index[0].clip_path != by_index[1].clip_path, "each scene should have matched a DIFFERENT asset"
        print(f"-> Scene 0 clip: {by_index[0].clip_path}  (reason: {by_index[0].clip_reason})")
        print(f"-> Scene 1 clip: {by_index[1].clip_path}  (reason: {by_index[1].clip_reason})")

        banner("decision log: the auto_fallback_sourcing entry")
        rows = [json.loads(line) for line in orch.store.decisions(job.id).jsonl.read_text().splitlines() if line.strip()]
        fallback_rows = [r for r in rows if r.get("action") == "auto_fallback_sourcing"]
        print(json.dumps(fallback_rows, indent=2))
        assert len(fallback_rows) == 1

        banner("ALL ASSERTIONS PASSED")
        print("The two-pass hybrid fallback correctly: (1) stayed lean on Pass 1 (only each scene's most "
              "specific query was searched), (2) detected scene 1's coverage gap right when Gate 2 was closed, "
              "(3) activated only that scene's next shot-list query rather than every scene's, (4) scoped Pass "
              "2's sourcing round to exactly that one query, (5) sent the new candidate through a normal human "
              "asset-review round rather than auto-approving it, and (6) matched each scene's real clip once "
              "coverage existed, all logged under a distinguishable 'auto_fallback_sourcing' decision.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
