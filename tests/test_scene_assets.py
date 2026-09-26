"""Mid-job URL adds at Gate 2 (reject_assets extra_urls_text) and drag-and-drop footage at Gate 3
(add_scene_asset / approve_pending_scene_asset) -- see pipeline/core/orchestrator.py and docs/REVIEW_UI.md."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.core import state_machine as sm
from pipeline.core.models import Asset, JobState as S, ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.sources.base import SourceResult
from tests.fakes import fake_registry

FAKE_NO_SOURCES = ProviderChoice(keywords="fake", scenes="fake", render="fake")
FAKE_WITH_COMMONS = ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=["commons"])


class FakeCommonsSource:
    """A minimal stand-in for a real adapter: no HTTP, one asset per query, license set so vet_asset()
    scores it low-risk (CC0) unless a test overrides `license`."""
    name = "commons"
    label = "fake commons"
    license = "CC0"

    async def fetch(self, queries, ctx):
        (ctx.dir / "files").mkdir(parents=True, exist_ok=True)
        assets = []
        for i, q in enumerate(queries):
            p = ctx.dir / "files" / f"{i}-{q}.jpg"
            p.write_bytes(b"fake-jpg")
            assets.append(Asset(source=self.name, kind="image", path=str(p), rel_path=str(p), source_url=f"https://x/{q}",
                                page_url=f"https://x/p/{q}", license=self.license, author="Ann", width=1920, height=1080,
                                sha256=f"h{i}{q}", query=q))
        return SourceResult(assets=assets)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make(self):
        reg, self.stages = fake_registry()
        reg.register_source("commons", FakeCommonsSource)
        self.store = JobStore(self.root / "projects")
        return Orchestrator(self.store, reg, {"review": {}})

    async def to_assets_review(self, orch, sources=("commons",)):
        job = await orch.create_job("Cute cats!", ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=list(sources)))
        await orch.start(job.id)
        job = await orch.run_pending(job.id)                        # -> SCRIPT_REVIEW
        job = await orch.approve_script(job.id, reviewer="Aly")     # -> KEYWORDS_RUNNING
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md), so one run_pending call
        # here both generates them and carries the job straight through to sourcing.
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SOURCING_RUNNING)
        return await orch.run_pending(job.id)

    async def to_scenes_review(self, orch):
        """No sources configured -- Gate 1's fork skips straight to Scenes (state_machine.py), the same
        shortest path tests/test_orchestrator.py uses."""
        job = await orch.create_job("cats", FAKE_NO_SOURCES)
        await orch.start(job.id)
        job = await orch.run_pending(job.id)                        # -> SCRIPT_REVIEW
        job = await orch.approve_script(job.id, reviewer="Aly")     # -> KEYWORDS_RUNNING
        job = await orch.run_pending(job.id)                        # auto-approved keywords -> SCENES_RUNNING
        self.assertEqual(job.state, S.SCENES_RUNNING)
        return await orch.run_pending(job.id)


class RejectAssetsAddUrlsTests(Base):
    async def test_extra_urls_text_merges_in_and_enables_urls_source(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        self.assertNotIn("urls", job.providers.sources)
        job = await orch.reject_assets(job.id, extra_urls_text="https://example.com/a.mp4 | b-roll | 2\n"
                                                                "https://example.com/b.mp4\n", reviewer="Aly")
        self.assertEqual(job.state, S.SOURCING_RUNNING)
        self.assertIn("urls", job.providers.sources)
        urls = job.providers.options["urls"]
        self.assertEqual([u["url"] for u in urls], ["https://example.com/a.mp4", "https://example.com/b.mp4"])
        self.assertEqual(urls[0]["note"], "b-roll")
        self.assertEqual(urls[0]["position"], "2")

    async def test_dedupes_against_already_configured_urls_and_keeps_existing_note(self):
        orch = self.make()
        job = await self.to_assets_review(orch)

        job = orch.store.load(job.id)
        job.providers.options["urls"] = [{"url": "https://example.com/a.mp4", "note": "original note", "position": ""}]
        job.providers.sources = [*job.providers.sources, "urls"]
        orch.store.save(job)

        job = await orch.reject_assets(job.id, extra_urls_text="https://example.com/a.mp4 | new note\n"
                                                                "https://example.com/c.mp4\n", reviewer="Aly")
        urls = job.providers.options["urls"]
        self.assertEqual([u["url"] for u in urls], ["https://example.com/a.mp4", "https://example.com/c.mp4"])
        self.assertEqual(urls[0]["note"], "original note")            # not overwritten by the new paste
        self.assertEqual(job.providers.sources.count("urls"), 1)      # not appended twice

    async def test_blank_or_no_urls_text_does_not_touch_sources(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        job = await orch.reject_assets(job.id, feedback="wrong vibe", reviewer="Aly")
        self.assertEqual(job.state, S.SOURCING_RUNNING)
        self.assertNotIn("urls", job.providers.sources)
        self.assertNotIn("urls", job.providers.options)


class AddSceneAssetTests(Base):
    def asset(self, **kw):
        base = dict(source="upload", kind="image", path="/tmp/x.jpg", rel_path="x.jpg", source_url="upload://x.jpg",
                    title="x.jpg", license="CC0", width=1920, height=1080, sha256="deadbeef")
        base.update(kw)
        return Asset(**base)

    async def test_low_risk_asset_is_auto_approved_and_assigned(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        scene_id = job.scenes[0].id
        job = await orch.add_scene_asset(job.id, scene_id, self.asset(sha256="a1"), reviewer="Aly")
        new = next(a for a in job.assets if a.sha256 == "a1")
        self.assertEqual(new.status, "approved")
        self.assertEqual(next(s for s in job.scenes if s.id == scene_id).clip_path, new.path)
        self.assertEqual(next(s for s in job.scenes if s.id == scene_id).asset_id, new.id)

    async def test_high_risk_without_note_stays_pending_and_unassigned(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        scene_id = job.scenes[0].id
        before_clip = next(s for s in job.scenes if s.id == scene_id).clip_path
        job = await orch.add_scene_asset(job.id, scene_id, self.asset(sha256="a2", license=""), reviewer="Aly")
        new = next(a for a in job.assets if a.sha256 == "a2")
        self.assertEqual(new.vetting.risk, "high")
        self.assertEqual(new.status, "pending")            # kept, not lost
        self.assertEqual(next(s for s in job.scenes if s.id == scene_id).clip_path, before_clip)   # not assigned

    async def test_high_risk_with_note_is_approved_and_assigned_immediately(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        scene_id = job.scenes[0].id
        job = await orch.add_scene_asset(job.id, scene_id, self.asset(sha256="a3", license=""),
                                          reviewer="Aly", note="it's my own footage")
        new = next(a for a in job.assets if a.sha256 == "a3")
        self.assertEqual(new.status, "approved")
        self.assertEqual(new.decision_note, "it's my own footage")
        self.assertEqual(next(s for s in job.scenes if s.id == scene_id).clip_path, new.path)

    async def test_unknown_scene_id_raises_and_adds_nothing(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        with self.assertRaises(ValueError):
            await orch.add_scene_asset(job.id, "not-a-scene", self.asset(sha256="a4"), reviewer="Aly")
        self.assertFalse(any(a.sha256 == "a4" for a in orch.get(job.id).assets))

    async def test_wrong_state_raises(self):
        orch = self.make()
        job = await orch.create_job("cats", FAKE_NO_SOURCES)
        await orch.start(job.id)
        job = await orch.run_pending(job.id)                # KEYWORDS_REVIEW, not SCENES_REVIEW
        with self.assertRaises(sm.TransitionError):
            await orch.add_scene_asset(job.id, "whatever", self.asset(), reviewer="Aly")


class AddReviewableAssetTests(Base):
    """add_reviewable_asset -- the backend for Gate 2's "Add links" (#9): unlike add_scene_asset (Gate 3), an
    asset added here is NEVER auto-approved or assigned to anything -- Gate 2 itself is the review step, so it
    always lands `pending` and flows through the ordinary Use/Duplicate/Irrelevant review, whatever its risk."""
    def asset(self, **kw):
        base = dict(source="urls", kind="video", path="/tmp/z.mp4", rel_path="z.mp4", source_url="https://files.example.com/z.mp4",
                    title="z.mp4", license="", width=1920, height=1080, sha256="urlasset1", import_method="manual_url")
        base.update(kw)
        return Asset(**base)

    async def test_low_risk_asset_still_lands_pending_not_auto_approved(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        job = await orch.add_reviewable_asset(job.id, self.asset(sha256="u1", license="CC0"), reviewer="Aly")
        new = next(a for a in job.assets if a.sha256 == "u1")
        self.assertEqual(new.vetting.risk, "low")
        self.assertEqual(new.status, "pending")             # Gate 2 IS the review -- never auto-approved here
        self.assertEqual(new.import_method, "manual_url")

    async def test_high_risk_asset_lands_pending_with_no_note_required_up_front(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        job = await orch.add_reviewable_asset(job.id, self.asset(sha256="u2", license=""), reviewer="Aly")
        new = next(a for a in job.assets if a.sha256 == "u2")
        self.assertEqual(new.vetting.risk, "high")
        self.assertEqual(new.status, "pending")             # note is only required when you decide, not on add

    async def test_added_asset_flows_through_normal_gate2_review(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        job = await orch.add_reviewable_asset(job.id, self.asset(sha256="u3", license="CC0"), reviewer="Aly")
        new = next(a for a in job.assets if a.sha256 == "u3")
        job = await orch.review_assets(job.id, {new.id: {"decision": "approve"}}, reviewer="Aly")
        self.assertEqual(next(a for a in job.assets if a.sha256 == "u3").status, "approved")

    async def test_wrong_state_raises_and_adds_nothing(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        with self.assertRaises(sm.TransitionError):
            await orch.add_reviewable_asset(job.id, self.asset(sha256="u4"), reviewer="Aly")
        self.assertFalse(any(a.sha256 == "u4" for a in orch.get(job.id).assets))


class ApprovePendingSceneAssetTests(Base):
    def asset(self, **kw):
        base = dict(source="upload", kind="image", path="/tmp/y.jpg", rel_path="y.jpg", source_url="upload://y.jpg",
                    title="y.jpg", license="", width=1920, height=1080, sha256="pending1")
        base.update(kw)
        return Asset(**base)

    async def add_pending(self, orch, job):
        scene_id = job.scenes[0].id
        job = await orch.add_scene_asset(job.id, scene_id, self.asset(), reviewer="Aly")     # high risk, no note -> pending
        asset = next(a for a in job.assets if a.sha256 == "pending1")
        self.assertEqual(asset.status, "pending")
        return job, scene_id, asset.id

    async def test_approving_with_a_note_assigns_it(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job, scene_id, asset_id = await self.add_pending(orch, job)
        job = await orch.approve_pending_scene_asset(job.id, asset_id, scene_id, reviewer="Aly", note="mine to use")
        asset = next(a for a in job.assets if a.id == asset_id)
        self.assertEqual(asset.status, "approved")
        self.assertEqual(next(s for s in job.scenes if s.id == scene_id).asset_id, asset_id)

    async def test_approving_without_a_note_is_still_refused(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job, scene_id, asset_id = await self.add_pending(orch, job)
        with self.assertRaises(ValueError):
            await orch.approve_pending_scene_asset(job.id, asset_id, scene_id, reviewer="Aly")
        self.assertEqual(orch.get(job.id).assets[-1].status, "pending")


if __name__ == "__main__":
    unittest.main()
