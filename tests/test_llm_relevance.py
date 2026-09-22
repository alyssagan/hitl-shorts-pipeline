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
        self.assertEqual(a.vetting.relevance_method, "llm-semantic")
        self.assertNotIn("RELEVANCE_LOW", [f.rule for f in a.vetting.flags])

    def test_falls_back_to_keyword_match_when_llm_has_no_entry_for_asset(self):
        a = mk("a1", title="Whitechapel Road 1888")
        vet_all([a], ["whitechapel 1888"], 0.5, {"other-id": (0.9, "n/a")})
        self.assertEqual(a.vetting.relevance_method, "keyword-match (LLM unavailable/failed for this item)")
        self.assertGreater(a.vetting.relevance, 0)

    def test_no_llm_scores_arg_uses_plain_keyword_match_label(self):
        a = mk("a1", title="Whitechapel Road 1888")
        vet_all([a], ["whitechapel 1888"])
        self.assertEqual(a.vetting.relevance_method, "keyword-match")


if __name__ == "__main__":
    unittest.main()
