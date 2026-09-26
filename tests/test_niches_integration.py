"""Content niches (docs/NICHES.md): orchestrator wiring (Job.niche persists, a sourcing/vetting round
populates niche_evaluation on every pending asset, an invalid niche is rejected at creation) and the
niche_evaluation_report() read method. Full Orchestrator + mock-transport integration style, reusing the
same Base pattern as tests/test_defer_relevance.py."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core.models import JobState as S, ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.sources.pexels import PexelsSource
from tests.fakes import fake_registry

IMG = b"\xff\xd8\xff-fake-jpeg-"


def _pexels_handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "api.pexels.com":
        if "/videos/" in str(request.url):
            return httpx.Response(200, json={"videos": []})
        return httpx.Response(200, json={"photos": [{"id": 9, "width": 1080, "height": 1920,
                                                      "url": "https://www.pexels.com/photo/tabby-cat-9/",
                                                      "photographer": "Bo", "alt": "A tabby cat",
                                                      "src": {"large2x": "https://images.pexels.com/9.jpg"}}]})
    if host == "images.pexels.com":
        return httpx.Response(200, content=IMG + str(request.url).encode())
    return httpx.Response(404)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make(self):
        reg, self.stages = fake_registry()
        reg.source_transport = httpx.MockTransport(_pexels_handler)
        reg.register_source("pexels", lambda: PexelsSource(api_key="k", videos=True))
        self.store = JobStore(self.root / "projects")
        return Orchestrator(self.store, reg, {"review": {}})

    async def to_assets_review(self, orch, *, niche=None):
        job = await orch.create_job("Cute cats!", ProviderChoice(keywords="fake", scenes="fake", render="fake",
                                                                   sources=["pexels"]), niche=niche)
        await orch.start(job.id)
        job = await orch.run_pending(job.id)                        # -> SCRIPT_REVIEW
        job = await orch.approve_script(job.id, reviewer="Aly")     # -> KEYWORDS_RUNNING
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md), so this one run_pending
        # call both generates them and carries the job straight through to sourcing.
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SOURCING_RUNNING)
        return await orch.run_pending(job.id)


class CreateJobNicheTests(Base):
    async def test_niche_is_optional_and_defaults_to_none(self):
        orch = self.make()
        job = await orch.create_job("Cute cats!")
        self.assertIsNone(job.niche)

    async def test_a_valid_niche_persists_on_the_job(self):
        orch = self.make()
        job = await orch.create_job("Jack the Ripper", niche="true_crime")
        self.assertEqual(job.niche, "true_crime")
        reloaded = orch.get(job.id)   # round-trips through JobStore's save/load (pydantic (de)serialization)
        self.assertEqual(reloaded.niche, "true_crime")

    async def test_an_unknown_niche_is_rejected(self):
        orch = self.make()
        with self.assertRaises(ValueError):
            await orch.create_job("Cute cats!", niche="not_a_real_niche")


class VettingRoundNicheEvaluationTests(Base):
    async def test_no_niche_means_no_niche_evaluation_on_any_asset(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        pending = [a for a in job.assets if a.status == "pending"]
        self.assertTrue(pending)
        self.assertTrue(all(a.vetting.niche_evaluation is None for a in pending))

    async def test_a_niche_produces_a_niche_evaluation_on_every_vetted_asset(self):
        orch = self.make()
        job = await self.to_assets_review(orch, niche="pet_product")
        pending = [a for a in job.assets if a.status == "pending"]
        self.assertTrue(pending)
        for a in pending:
            ev = a.vetting.niche_evaluation
            self.assertIsNotNone(ev)
            self.assertEqual(ev.niche_evaluated, "Pet Product")
            # pexels is rewarded for pet_product -> Excellent, not penalized
            self.assertEqual(ev.aesthetic_fit, "Excellent")

    async def test_score_relevance_now_also_updates_the_niche_evaluation(self):
        # defer_relevance leaves the round unscored; score_relevance() (the "Score relevance now" action,
        # tests/test_score_relevance_now.py) scores it for real and niche_evaluation must follow -- it's
        # recomputed from _apply_vetting(), which score_relevance() also calls with force_score=True.
        orch = self.make()
        job = await orch.create_job("Cute cats!", ProviderChoice(keywords="fake", scenes="fake", render="fake",
                                                                   sources=["pexels"],
                                                                   options={"defer_relevance": True}),
                                     niche="pet_product")
        await orch.start(job.id)
        job = await orch.run_pending(job.id)                        # -> SCRIPT_REVIEW
        job = await orch.approve_script(job.id, reviewer="Aly")     # -> KEYWORDS_RUNNING
        job = await orch.run_pending(job.id)                        # auto-approved keywords -> SOURCING_RUNNING
        job = await orch.run_pending(job.id)
        pending = [a for a in job.assets if a.status == "pending"]
        self.assertTrue(all(a.vetting.niche_evaluation.relevance_score is None for a in pending))
        job = await orch.score_relevance(job.id, reviewer="Aly")
        pending = [a for a in job.assets if a.status == "pending"]
        self.assertTrue(all(a.vetting.niche_evaluation.relevance_score is not None for a in pending))


class NicheEvaluationReportTests(Base):
    async def test_report_is_empty_with_no_niche(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        report = orch.niche_evaluation_report(job.id)
        self.assertIsNone(report["niche"])
        self.assertEqual(report["evaluations"], [])

    async def test_report_has_one_record_per_asset_with_a_niche_set(self):
        orch = self.make()
        job = await self.to_assets_review(orch, niche="pet_product")
        report = orch.niche_evaluation_report(job.id)
        self.assertEqual(report["niche"], "pet_product")
        self.assertEqual(len(report["evaluations"]), len(job.assets))
        self.assertEqual({e["asset_id"] for e in report["evaluations"]}, {a.id for a in job.assets})


if __name__ == "__main__":
    unittest.main()
