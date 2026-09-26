import unittest

import httpx

from pipeline.core.models import Asset, Job
from pipeline.vetting.llm_relevance import LlmRelevanceScorer, _item_text, _parse_batch
from pipeline.vetting.rules import vet_all


def mk(id_, title="", desc=""):
    return Asset(id=id_, source="x", path="/x", title=title, description=desc, license="CC0",
                 source_url="https://x/y", author="a")


class ParsingTests(unittest.TestCase):
    def test_parse_batch_clamps_and_ignores_unknown_ids(self):
        batch = [mk("a1"), mk("a2")]
        text = '```json\n[{"id":"a1","score":150,"why":"x"},{"id":"unknown","score":10,"why":"y"}]\n```'
        out = _parse_batch(text, batch)
        self.assertEqual(out, {"a1": (1.0, "x")})

    def test_item_text_flags_empty_caption(self):
        self.assertIn("no title, description or tags", _item_text(mk("a1")))
        self.assertIn("title=", _item_text(mk("a1", title="Mitre Square")))


class ScorerTests(unittest.IsolatedAsyncioTestCase):
    async def test_batches_and_scores(self):
        calls = []
        def handler(req: httpx.Request):
            body = req.content.decode()
            calls.append(body)
            ids = [f"a{i}" for i in range(1, 4)] if len(calls) == 1 else [f"a{i}" for i in range(4, 5)]
            reply = [{"id": i, "score": 90, "why": "matches"} for i in ids]
            import json
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
        assets = [mk(f"a{i}", title=f"item {i}") for i in range(1, 5)]
        scorer = LlmRelevanceScorer("http://x", "key", "m", batch_size=3, transport=httpx.MockTransport(handler), retry_waits=())
        out = await scorer.score(Job(subject="jack the ripper"), assets, ["whitechapel 1888"])
        self.assertEqual(len(calls), 2)                 # 4 assets, batch_size 3 -> 2 batches
        self.assertEqual(set(out), {"a1", "a2", "a3", "a4"})
        self.assertEqual(out["a1"], (0.9, "matches"))

    async def test_bad_batch_is_skipped_not_fatal(self):
        def handler(req: httpx.Request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
        assets = [mk("a1")]
        scorer = LlmRelevanceScorer("http://x", "key", "m", transport=httpx.MockTransport(handler), retry_waits=())
        out = await scorer.score(Job(subject="x"), assets, ["x"])
        self.assertEqual(out, {})

    async def test_no_keywords_or_assets_short_circuits(self):
        scorer = LlmRelevanceScorer("http://x", "key", "m")
        self.assertEqual(await scorer.score(Job(subject="x"), [], ["x"]), {})
        self.assertEqual(await scorer.score(Job(subject="x"), [mk("a1")], []), {})


class VettingIntegrationTests(unittest.TestCase):
    def test_llm_score_wins_over_keyword_match_and_is_labelled(self):
        a = mk("a1")                                    # empty caption: keyword-match would find nothing
        vet_all([a], ["whitechapel 1888"], 0.5, {"a1": (0.9, "the model says this is Mitre Square")})
        self.assertEqual(a.vetting.relevance, 0.9)
        # scoring_method/method_version are separate, versioned fields (docs/SCORING_CHANGELOG.md).
        self.assertEqual((a.vetting.scoring_method, a.vetting.method_version), ("llm-semantic", "llm-semantic-v1"))
        self.assertNotIn("RELEVANCE_LOW", [f.rule for f in a.vetting.flags])

    def test_falls_back_to_keyword_match_when_llm_has_no_entry_for_asset(self):
        a = mk("a1", title="Whitechapel Road 1888")
        vet_all([a], ["whitechapel 1888"], 0.5, {"other-id": (0.9, "n/a")})
        self.assertEqual((a.vetting.scoring_method, a.vetting.method_version), ("keyword-match", "keyword-match-v1"))
        self.assertEqual(a.vetting.scoring_fallback_note, "LLM unavailable/failed for this item")
        self.assertGreater(a.vetting.relevance, 0)

    def test_no_llm_scores_arg_uses_plain_keyword_match_label(self):
        a = mk("a1", title="Whitechapel Road 1888")
        vet_all([a], ["whitechapel 1888"])
        self.assertEqual((a.vetting.scoring_method, a.vetting.method_version), ("keyword-match", "keyword-match-v1"))
        self.assertEqual(a.vetting.scoring_fallback_note, "")

    def test_a_custom_version_can_be_passed_in(self):
        # The orchestrator always passes the live, current version identifiers (Orchestrator._apply_vetting) --
        # this confirms vet_all()/vet_asset() actually use whatever is passed, not a hardcoded string.
        a = mk("a1")
        vet_all([a], ["whitechapel 1888"], 0.5, {"a1": (0.9, "why")}, llm_version="llm-semantic-v2-experimental")
        self.assertEqual((a.vetting.scoring_method, a.vetting.method_version), ("llm-semantic", "llm-semantic-v2-experimental"))


if __name__ == "__main__":
    unittest.main()


class ReuseAcrossRoundsTests(unittest.TestCase):
    def test_prior_llm_score_is_kept_when_not_resent(self):
        a = mk("a1", title="")                          # word-match alone would find nothing for this
        vet_all([a], ["whitechapel 1888"], 0.5, {"a1": (0.9, "clearly the right place")})
        self.assertEqual((a.vetting.relevance, a.vetting.scoring_method, a.vetting.method_version), (0.9, "llm-semantic", "llm-semantic-v1"))
        # Second round: a1 isn't in this round's llm_scores (it wasn't resent) -- must NOT fall back to word-match.
        vet_all([a], ["whitechapel 1888"], 0.5, {"a2": (0.1, "unrelated")})
        self.assertEqual((a.vetting.relevance, a.vetting.scoring_method, a.vetting.method_version), (0.9, "llm-semantic", "llm-semantic-v1"))
        self.assertEqual(a.vetting.relevance_why, "clearly the right place")

    def test_prior_word_match_score_is_not_protected_and_can_be_upgraded(self):
        a = mk("a1", title="Whitechapel Road 1888")
        vet_all([a], ["whitechapel 1888"])               # first round: no LLM at all -> word-match
        self.assertEqual((a.vetting.scoring_method, a.vetting.method_version), ("keyword-match", "keyword-match-v1"))
        vet_all([a], ["whitechapel 1888"], 0.5, {"a1": (0.95, "confirmed")})   # now the LLM scores it
        self.assertEqual((a.vetting.relevance, a.vetting.scoring_method, a.vetting.method_version), (0.95, "llm-semantic", "llm-semantic-v1"))


class ScoreRelevanceSelectionTests(unittest.IsolatedAsyncioTestCase):
    """Unit-level: Orchestrator._score_relevance() computes a TF-IDF baseline for every pending asset, then sends
    the LLM only the ones whose TF-IDF score is "borderline" (close to the threshold) -- never assets that
    already have a good LLM score from an earlier round, never confidently-scored ones, never non-pending ones.
    These tests patch tfidf_scores() so the borderline/confident split is exact and doesn't depend on real
    TF-IDF math (that's covered separately in tests/test_tfidf_relevance.py)."""

    def _llm_handler(self, seen_ids):
        def handler(req: httpx.Request):
            import json
            body = json.loads(req.content)
            ids = [ln.split("id=")[1].split()[0] for ln in body["messages"][0]["content"].splitlines()
                   if ln.strip() and ln[0].isdigit()]
            seen_ids.extend(ids)
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
                [{"id": i, "score": 70, "why": "ok"} for i in ids])}}]})
        return handler

    async def test_sends_only_borderline_assets_not_reused_or_confident(self):
        import tempfile
        from unittest.mock import patch
        from pipeline.core.models import Job, Keyword, ProviderChoice, Vetting
        from pipeline.core.orchestrator import Orchestrator
        from pipeline.core.store import JobStore
        from pipeline.vetting.llm_relevance import LlmRelevanceScorer
        from tests.fakes import fake_registry

        already_good = mk("good", title="already scored")
        already_good.vetting = Vetting(relevance=0.8, scoring_method="llm-semantic", method_version="llm-semantic-v1", relevance_why="fine")
        confident_low = mk("low", title="clearly off topic")
        confident_high = mk("high", title="clearly on topic")
        borderline = mk("border", title="hard to call")
        decided = mk("decided", title="already approved, not pending")
        decided.status = "approved"

        # min_relevance defaults to 0.5, borderline_band defaults to 0.15 -> borderline window is (0.35, 0.65).
        fake_tfidf = {"good": (0.9, "n/a"), "low": (0.05, "no match"), "high": (0.95, "strong match"),
                      "border": (0.55, "partial match")}

        seen_ids: list[str] = []
        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry()
            reg._relevance_scorer_factory = lambda: LlmRelevanceScorer(
                "http://llm", "key", "m", transport=httpx.MockTransport(self._llm_handler(seen_ids)), retry_waits=())
            orch = Orchestrator(JobStore(d), reg, settings={})
            job = Job(subject="jack the ripper", providers=ProviderChoice(keywords="fake"))
            job.keywords = [Keyword(term="whitechapel 1888", source="human", approved=True)]
            job.assets = [already_good, confident_low, confident_high, borderline, decided]
            with patch("pipeline.core.orchestrator.tfidf_scores", return_value=fake_tfidf):
                tfidf, llm_scores = await orch._score_relevance(job)

        self.assertEqual(seen_ids, ["border"])            # only the borderline one went to the LLM
        self.assertEqual(set(llm_scores), {"border"})
        self.assertEqual(tfidf, fake_tfidf)                # the baseline is still returned for everyone

    async def test_per_round_cap_keeps_only_the_closest_to_threshold(self):
        import tempfile
        from unittest.mock import patch
        from pipeline.core.models import Job, Keyword, ProviderChoice
        from pipeline.core.orchestrator import Orchestrator
        from pipeline.core.store import JobStore
        from pipeline.vetting.llm_relevance import LlmRelevanceScorer
        from tests.fakes import fake_registry

        assets = [mk("near", title="a"), mk("mid", title="b"), mk("far", title="c")]  # all "borderline"
        # distances from threshold 0.5: near=0.02, mid=0.08, far=0.14 (all within default band 0.15)
        fake_tfidf = {"near": (0.52, ""), "mid": (0.58, ""), "far": (0.64, "")}

        seen_ids: list[str] = []
        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry()
            reg._relevance_scorer_factory = lambda: LlmRelevanceScorer(
                "http://llm", "key", "m", transport=httpx.MockTransport(self._llm_handler(seen_ids)), retry_waits=())
            orch = Orchestrator(JobStore(d), reg, settings={"relevance": {"max_llm_per_round": 1}})
            job = Job(subject="x", providers=ProviderChoice(keywords="fake"))
            job.keywords = [Keyword(term="x", source="human", approved=True)]
            job.assets = assets
            with patch("pipeline.core.orchestrator.tfidf_scores", return_value=fake_tfidf):
                await orch._score_relevance(job)

        self.assertEqual(seen_ids, ["near"])               # cap=1: only the closest-to-threshold asset is sent

    async def test_llm_scores_is_none_when_no_scorer_configured(self):
        import tempfile
        from pipeline.core.models import Job, Keyword, ProviderChoice
        from pipeline.core.orchestrator import Orchestrator
        from pipeline.core.store import JobStore
        from tests.fakes import fake_registry
        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry()          # no _relevance_scorer_factory set -> disabled
            orch = Orchestrator(JobStore(d), reg, settings={})
            job = Job(subject="jack the ripper", providers=ProviderChoice(keywords="fake"))
            job.keywords = [Keyword(term="whitechapel", source="human", approved=True)]
            job.assets = [mk("a1", title="Whitechapel 1888")]
            tfidf, llm_scores = await orch._score_relevance(job)
        self.assertIsNone(llm_scores)
        self.assertIn("a1", tfidf)                         # the local baseline still runs with no LLM configured

    async def test_no_terms_or_no_pending_assets_short_circuits(self):
        import tempfile
        from pipeline.core.models import Job, ProviderChoice
        from pipeline.core.orchestrator import Orchestrator
        from pipeline.core.store import JobStore
        from tests.fakes import fake_registry
        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry()
            orch = Orchestrator(JobStore(d), reg, settings={})
            job = Job(subject="x", providers=ProviderChoice(keywords="fake"))    # no approved keywords
            job.assets = [mk("a1")]
            self.assertEqual(await orch._score_relevance(job), ({}, None))


class OrchestratorSkipsAlreadyScoredTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_vetting_round_only_sends_new_assets_to_the_llm(self):
        import tempfile
        from pipeline.core.models import ProviderChoice
        from pipeline.core.orchestrator import Orchestrator
        from pipeline.core.store import JobStore
        from pipeline.sources.commons import CommonsSource
        from pipeline.vetting.llm_relevance import LlmRelevanceScorer
        from tests.fakes import fake_registry
        from tests.test_sources_flow import handler as commons_handler

        seen_ids: list[list[str]] = []

        def llm_handler(req: httpx.Request):
            import json
            body = json.loads(req.content)
            text = body["messages"][0]["content"]
            ids = [ln.split("id=")[1].split()[0] for ln in text.splitlines() if ln.strip() and ln[0].isdigit()]
            seen_ids.append(ids)
            reply = [{"id": i, "score": 80, "why": "on topic"} for i in ids]
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry()
            reg.source_transport = httpx.MockTransport(commons_handler)
            reg.register_source("commons", lambda: CommonsSource(per_query=5))
            reg._relevance_scorer_factory = lambda: LlmRelevanceScorer(
                "http://llm", "key", "m", transport=httpx.MockTransport(llm_handler), retry_waits=())
            # A wide borderline band (covers the whole 0..1 range) makes every not-yet-llm-scored pending asset
            # "borderline" regardless of its real TF-IDF score, so this test can focus purely on the
            # reuse-across-rounds behavior (the borderline/cap selection itself is covered by
            # ScoreRelevanceSelectionTests above, with the TF-IDF math stubbed out for exactness).
            orch = Orchestrator(JobStore(d), reg, settings={"relevance": {"borderline_band": 1.0, "max_llm_per_round": 999}})
            job = await orch.create_job("cats", ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=["commons"],
                                                                 options={"auto_approve_keywords": False}),
                                        reviewer="Aly")
            await orch.start(job.id, reviewer="Aly")
            for _ in range(20):
                j = orch.get(job.id)
                if j.state.value in ("script_review", "failed"):
                    break
                await orch.run_pending(job.id)
            await orch.approve_script(job.id, reviewer="Aly")
            for _ in range(20):
                j = orch.get(job.id)
                if j.state.value in ("keywords_review", "failed"):
                    break
                await orch.run_pending(job.id)
            await orch.review_keywords(job.id, [j.keywords[0].id], reviewer="Aly")
            for _ in range(20):
                j = orch.get(job.id)
                if j.state.value in ("assets_review", "failed"):
                    break
                await orch.run_pending(job.id)
            self.assertEqual(j.state.value, "assets_review")
            first_ids = set(seen_ids[0])
            self.assertEqual(len(first_ids), len(j.assets))         # every asset scored the first time

            # "search again": still-pending assets carry over, plus whatever the next round sources.
            await orch.reject_assets(job.id, "not specific enough", ["another term"], reviewer="Aly")
            for _ in range(20):
                j = orch.get(job.id)
                if j.state.value in ("assets_review", "failed"):
                    break
                await orch.run_pending(job.id)
            self.assertEqual(j.state.value, "assets_review")
            second_ids = set(seen_ids[-1]) if len(seen_ids) > 1 else set()
            # None of the assets already scored in round 1 should be sent to the LLM again in round 2.
            self.assertFalse(first_ids & second_ids, f"re-scored assets that were already llm-semantic: {first_ids & second_ids}")
            self.assertTrue(all(a["vetting"]["scoring_method"] == "llm-semantic" and a["vetting"]["method_version"] == "llm-semantic-v1"
                                 for a in j.model_dump(mode="json")["assets"]))
