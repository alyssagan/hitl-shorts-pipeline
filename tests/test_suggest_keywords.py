"""Orchestrator.suggest_keywords()/approve_suggested_keywords() -- Gate 2's "Suggest more search terms"
(requested directly: "will this be implemented in the pipeline" -> re-run the same LLM keyword-writing
stage Gate 1 uses, on demand, mid asset review, instead of a separate manual/external prompt). Unlike
"Search again"'s typed extra_queries (tests/test_orchestrator.py, pipeline/stages/sourcing.py: no group of
their own, sent to every configured source regardless of fit), a suggestion comes back with the model's
own research/case/historical/stock classification, same as any Gate-1 keyword, and is routed correctly by
sourcing.py once approved.

Full Orchestrator + mock-transport integration style, like tests/test_defer_relevance.py, since this
exercises the real run_pending()/review_keywords() dispatch to reach ASSETS_REVIEW, not just a single
function in isolation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core.models import JobState as S, ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.sources.pexels import PexelsSource
from tests.fakes import FakeSuggestKeywords, fake_registry

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

    def make(self, suggest_terms=None):
        reg, self.stages = fake_registry()
        self.suggest_stage = FakeSuggestKeywords(terms=suggest_terms) if suggest_terms is not None else FakeSuggestKeywords()
        reg.register_keywords("llm", lambda: self.suggest_stage)
        reg.source_transport = httpx.MockTransport(_pexels_handler)
        reg.register_source("pexels", lambda: PexelsSource(api_key="k", videos=True))
        self.store = JobStore(self.root / "projects")
        return Orchestrator(self.store, reg, {"review": {}})

    async def to_assets_review(self, orch, *, keywords="llm"):
        job = await orch.create_job("Cute cats!", ProviderChoice(keywords=keywords, scenes="fake", render="fake",
                                                                   sources=["pexels"]))
        await orch.start(job.id)
        job = await orch.run_pending(job.id)                        # -> SCRIPT_REVIEW
        job = await orch.approve_script(job.id, reviewer="Aly")     # -> KEYWORDS_RUNNING
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md), so this one run_pending
        # call both generates them (kw1/kw2/kw3, or the "llm" fake's gate-1 batch) and carries the
        # job straight through to sourcing.
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SOURCING_RUNNING)
        return await orch.run_pending(job.id)


class SuggestKeywordsTests(Base):
    async def test_suggestions_are_appended_unapproved_not_replacing_existing_keywords(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        before = [k.term for k in job.keywords]
        result_job, new_kws = await orch.suggest_keywords(job.id, reviewer="Aly")
        self.assertEqual([k.term for k in new_kws], ["suggested case term", "suggested stock term"])
        self.assertTrue(all(not k.approved for k in new_kws))
        after_terms = [k.term for k in result_job.keywords]
        for t in before:
            self.assertIn(t, after_terms)   # nothing already there was removed or replaced
        self.assertIn("suggested case term", after_terms)
        self.assertIn("suggested stock term", after_terms)
        # Not searched by themselves -- approved_keywords is unchanged until a human approves them.
        self.assertNotIn("suggested case term", [k.term for k in result_job.approved_keywords])

    async def test_suggested_keywords_keep_their_own_group(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        _, new_kws = await orch.suggest_keywords(job.id, reviewer="Aly")
        groups = {k.term: k.group for k in new_kws}
        self.assertEqual(groups["suggested case term"], "case")
        self.assertEqual(groups["suggested stock term"], "stock")

    async def test_already_covered_lists_every_existing_term_so_the_model_does_not_repeat_it(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        # Gate 1's own first batch already made one call() (via to_assets_review's run_pending) -- the
        # suggest_keywords() call below is the SECOND, and it's the one under test here.
        self.assertEqual(len(self.suggest_stage.calls), 1)
        existing_terms = [k.term for k in job.keywords]
        await orch.suggest_keywords(job.id, reviewer="Aly")
        self.assertEqual(len(self.suggest_stage.calls), 2)
        for t in existing_terms:
            self.assertIn(t, self.suggest_stage.calls[-1]["already_covered"])

    async def test_feedback_reaches_the_stage_as_extra_feedback(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        await orch.suggest_keywords(job.id, feedback="more of the actual building", reviewer="Aly")
        self.assertEqual(self.suggest_stage.calls[-1]["extra_feedback"], "more of the actual building")

    async def test_a_second_suggest_call_sees_the_first_calls_suggestions_as_already_covered(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        await orch.suggest_keywords(job.id, reviewer="Aly")
        await orch.suggest_keywords(job.id, reviewer="Aly")
        second_suggest_call_covered = self.suggest_stage.calls[-1]["already_covered"]
        self.assertIn("suggested case term", second_suggest_call_covered)
        self.assertIn("suggested stock term", second_suggest_call_covered)

    async def test_records_a_decision_log_entry(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        await orch.suggest_keywords(job.id, reviewer="Aly")
        log_path = self.store.job_dir(job.id) / "decisions.jsonl"
        entries = [l for l in log_path.read_text(encoding="utf-8").splitlines() if '"suggested_more_keywords"' in l]
        self.assertTrue(entries)
        self.assertIn("suggested case term", entries[-1])

    async def test_rejects_outside_asset_review(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        pending_id = next(a.id for a in job.assets if a.status == "pending")
        await orch.review_assets(job.id, {pending_id: {"decision": "approve", "note": ""}}, reviewer="Aly")
        job = await orch.approve_assets(job.id, reviewer="Aly")   # -> scenes
        with self.assertRaises(ValueError):
            await orch.suggest_keywords(job.id, reviewer="Aly")

    async def test_rejects_for_the_manual_keyword_provider(self):
        orch = self.make()
        job = await self.to_assets_review(orch, keywords="fake")   # "fake" stands in for "manual" here -- not "llm"
        with self.assertRaises(ValueError) as cm:
            await orch.suggest_keywords(job.id, reviewer="Aly")
        self.assertIn("llm", str(cm.exception))


class ApproveSuggestedKeywordsTests(Base):
    async def test_approving_flips_approved_without_changing_job_state(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        _, new_kws = await orch.suggest_keywords(job.id, reviewer="Aly")
        case_id = next(k.id for k in new_kws if k.term == "suggested case term")
        result = await orch.approve_suggested_keywords(job.id, [case_id], reviewer="Aly")
        self.assertEqual(result.state, S.ASSETS_REVIEW)   # never transitions -- same gate, just more approved terms
        approved_terms = [k.term for k in result.approved_keywords]
        self.assertIn("suggested case term", approved_terms)
        self.assertNotIn("suggested stock term", approved_terms)   # only the id actually passed was approved

    async def test_reviewer_name_is_required(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        _, new_kws = await orch.suggest_keywords(job.id, reviewer="Aly")
        with self.assertRaises(ValueError):
            await orch.approve_suggested_keywords(job.id, [new_kws[0].id], reviewer="")

    async def test_unknown_keyword_id_is_rejected(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        with self.assertRaises(ValueError):
            await orch.approve_suggested_keywords(job.id, ["not-a-real-id"], reviewer="Aly")

    async def test_updates_keywords_approved_json_on_disk(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        _, new_kws = await orch.suggest_keywords(job.id, reviewer="Aly")
        await orch.approve_suggested_keywords(job.id, [new_kws[0].id], reviewer="Aly")
        import json
        data = json.loads((self.store.job_dir(job.id) / "keywords_approved.json").read_text(encoding="utf-8"))
        self.assertIn("suggested case term", data["keywords"])


if __name__ == "__main__":
    unittest.main()
