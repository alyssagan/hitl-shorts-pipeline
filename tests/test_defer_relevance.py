"""`defer_relevance` (requested directly, alongside the stock photo budget: "let's pull stock photos first
and score relevance later"): a sourcing round with `providers.options["defer_relevance"]=True` still runs
every risk/license rule (PLATFORM_SOURCE, LIC_*, ...) on each pulled asset -- those don't depend on the
approved keywords at all -- but skips relevance scoring entirely, so nothing gets a score and nothing is
hidden by the review page's min-relevance threshold (pipeline/api/review_page.py's below() only hides a
*scored* item). `Orchestrator.score_relevance()` (the "Score relevance now" action) scores them for real,
on demand, whenever the reviewer is ready -- see tests/test_score_relevance_now.py.

Full Orchestrator + mock-transport integration style, like tests/test_sources_flow.py, since this exercises
the real run_pending()/_apply_vetting() dispatch, not just a single function."""
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

    async def to_assets_review(self, orch, *, options=None):
        job = await orch.create_job("Cute cats!", ProviderChoice(keywords="fake", scenes="fake", render="fake",
                                                                   sources=["pexels"], options=options or {}))
        await orch.start(job.id)
        job = await orch.run_pending(job.id)                        # -> SCRIPT_REVIEW
        job = await orch.approve_script(job.id, reviewer="Aly")     # -> KEYWORDS_RUNNING
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md), so this one run_pending
        # call both generates them and carries the job straight through to sourcing.
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SOURCING_RUNNING)
        return await orch.run_pending(job.id)


class DeferRelevanceTests(Base):
    async def test_without_the_option_relevance_is_scored_as_before(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        self.assertEqual(job.state, S.ASSETS_REVIEW)
        pending = [a for a in job.assets if a.status == "pending"]
        self.assertTrue(pending)
        self.assertTrue(all(a.vetting is not None and a.vetting.relevance is not None for a in pending))

    async def test_defer_relevance_leaves_relevance_unscored(self):
        orch = self.make()
        job = await self.to_assets_review(orch, options={"defer_relevance": True})
        self.assertEqual(job.state, S.ASSETS_REVIEW)
        pending = [a for a in job.assets if a.status == "pending"]
        self.assertTrue(pending)
        self.assertTrue(all(a.vetting.relevance is None for a in pending))
        self.assertTrue(all(a.vetting.relevance_decision == "" for a in pending))
        self.assertTrue(all(a.vetting.scoring_method == "" for a in pending))

    async def test_defer_relevance_still_runs_risk_and_license_rules(self):
        orch = self.make()
        job = await self.to_assets_review(orch, options={"defer_relevance": True})
        pending = [a for a in job.assets if a.status == "pending"]
        # Pexels License is low-risk/attribution-free, so risk is still computed and low -- not skipped,
        # not None -- and no RELEVANCE_LOW flag was added, since nothing was scored to flag as low.
        self.assertTrue(all(a.vetting.risk in ("low", "medium", "high") for a in pending))
        self.assertTrue(all(not any(f.rule == "RELEVANCE_LOW" for f in a.vetting.flags) for a in pending))

    async def test_defer_relevance_logs_a_deferred_decision_not_a_hidden_count(self):
        orch = self.make()
        job = await self.to_assets_review(orch, options={"defer_relevance": True})
        log_path = self.store.job_dir(job.id) / "decisions.jsonl"
        entries = [l for l in log_path.read_text(encoding="utf-8").splitlines() if '"relevance_scoring"' in l]
        self.assertTrue(entries)
        self.assertIn("deferred", entries[-1])


if __name__ == "__main__":
    unittest.main()
