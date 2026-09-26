"""Tokens and $ cost provenance (docs/LOGGING.md "Tokens / cost"): core/usage.py's own math, post_chat() actually
recording a call's usage block, and the end-to-end path -- an LLM call made during a real (fake-provider) job
turns into its own `llm_call` decision-log entry and rolls up correctly in Orchestrator.usage_summary()."""
from __future__ import annotations

import tempfile
import time
import unittest

import httpx
from starlette.testclient import TestClient

from pipeline.core import usage
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.sources.commons import CommonsSource
from pipeline.stages.llm_http import post_chat
from tests.fakes import fake_registry
from tests.test_sources_flow import handler


class UsageMathTests(unittest.TestCase):
    """core/usage.py's own bookkeeping, independent of any orchestrator or network call."""

    def setUp(self):
        usage.configure_prices({})               # reset to no prices / no quota between tests

    def test_record_needs_a_bind_and_a_usage_block(self):
        usage.record("x", "m", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})  # nothing bound
        self.assertEqual(usage.collect(), [])
        token = usage.bind()
        try:
            usage.record("x", "m", None)          # no usage block in the reply
            self.assertEqual(usage.collect(), [])
            usage.record("x", "m", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        finally:
            usage.unbind(token)
        # the list only lives inside the bound context, but collect() before unbind sees it
        token = usage.bind()
        try:
            usage.record("keywords", "gemini-3.6-flash", {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140})
            calls = usage.collect()
            self.assertEqual(len(calls), 1)
            self.assertEqual((calls[0]["what"], calls[0]["model"], calls[0]["total_tokens"]),
                             ("keywords", "gemini-3.6-flash", 140))
            self.assertEqual(calls[0]["cost_usd"], 0.0)   # no price configured -> free
        finally:
            usage.unbind(token)

    def test_configured_price_computes_real_cost(self):
        usage.configure_prices({"usage": {"prices": {"gpt-4o-mini": {"prompt_per_mtok": 0.15, "completion_per_mtok": 0.60}}}})
        token = usage.bind()
        try:
            usage.record("script writer", "gpt-4o-mini", {"prompt_tokens": 1_000_000, "completion_tokens": 500_000, "total_tokens": 1_500_000})
            calls = usage.collect()
        finally:
            usage.unbind(token)
        # 1M prompt tokens @ $0.15/M + 0.5M completion tokens @ $0.60/M = 0.15 + 0.30 = $0.45
        self.assertEqual(calls[0]["cost_usd"], 0.45)

    def test_quota_lookup(self):
        usage.configure_prices({"usage": {"free_quota": {"gemini-3.6-flash": {"requests_per_minute": 15, "requests_per_day": 1500}}}})
        self.assertEqual(usage.quota_for("gemini-3.6-flash"), {"requests_per_minute": 15, "requests_per_day": 1500})
        self.assertEqual(usage.quota_for("some-other-model"), {})

    def test_rollup_sums_and_breaks_down_by_model(self):
        calls = [
            {"what": "a", "model": "m1", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost_usd": 0.01},
            {"what": "b", "model": "m1", "prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25, "cost_usd": 0.02},
            {"what": "c", "model": "m2", "prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100, "cost_usd": 0.0},
        ]
        out = usage.rollup(calls)
        self.assertEqual(out["calls"], 3)
        self.assertEqual(out["total_tokens"], 140)
        self.assertEqual(round(out["cost_usd"], 2), 0.03)
        self.assertEqual(out["by_model"]["m1"], {"calls": 2, "prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40, "cost_usd": 0.03})
        self.assertEqual(out["by_model"]["m2"]["calls"], 1)

    def test_rollup_of_no_calls_is_all_zero(self):
        out = usage.rollup([])
        self.assertEqual(out, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "by_model": {}})


class PostChatRecordsUsageTests(unittest.IsolatedAsyncioTestCase):
    """post_chat() is the one choke point every LLM call in the pipeline goes through -- confirms it actually
    hands a successful reply's usage block to core/usage.py, and that a reply with none, or a non-200, records
    nothing (never breaks the call itself)."""

    async def test_200_with_usage_block_is_recorded(self):
        def h(req: httpx.Request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                             "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
        token = usage.bind()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
                resp = await post_chat(client, "http://x/chat/completions", {}, {"model": "test-model", "messages": []}, what="unit test call")
            self.assertEqual(resp.status_code, 200)
            calls = usage.collect()
        finally:
            usage.unbind(token)
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["what"], calls[0]["model"], calls[0]["prompt_tokens"], calls[0]["total_tokens"]),
                         ("unit test call", "test-model", 7, 10))

    async def test_no_usage_block_records_nothing(self):
        def h(req: httpx.Request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})     # no "usage" key at all
        token = usage.bind()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
                await post_chat(client, "http://x/chat/completions", {}, {"model": "m"}, what="x")
            calls = usage.collect()
        finally:
            usage.unbind(token)
        self.assertEqual(calls, [])

    async def test_error_status_records_nothing(self):
        def h(req: httpx.Request):
            return httpx.Response(404, json={"error": "nope"})
        token = usage.bind()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
                await post_chat(client, "http://x/chat/completions", {}, {"model": "m"}, what="x", waits=())
            calls = usage.collect()
        finally:
            usage.unbind(token)
        self.assertEqual(calls, [])


class EndToEndUsageTests(unittest.TestCase):
    """A real (fake-provider sourcing + mocked LLM relevance) job, driven over HTTP exactly like a browser would:
    confirms an LLM call made mid-job becomes an `llm_call` decision-log entry with real tokens/cost, and that
    GET /jobs/{id}/usage rolls every such entry up correctly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _client(self, llm_handler):
        from pipeline.vetting.llm_relevance import LlmRelevanceScorer
        reg, _ = fake_registry()
        reg.source_transport = httpx.MockTransport(handler)
        reg.register_source("commons", lambda: CommonsSource(per_query=5))
        reg._relevance_scorer_factory = lambda: LlmRelevanceScorer(
            "http://llm", "key", "gemini-3.6-flash", transport=httpx.MockTransport(llm_handler), retry_waits=())
        settings = {"relevance": {"borderline_band": 1.0},   # force every asset to the LLM regardless of TF-IDF
                    "usage": {"prices": {"gemini-3.6-flash": {"prompt_per_mtok": 0.0, "completion_per_mtok": 0.0}},
                              "free_quota": {"gemini-3.6-flash": {"requests_per_minute": 15, "requests_per_day": 1500}}}}
        from pipeline.api.app import create_app
        orch = Orchestrator(JobStore(self.tmp.name), reg, settings=settings)
        client = TestClient(create_app(orch, settings=settings))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client, orch

    def _wait(self, client, jid, state):
        end = time.time() + 5
        j = None
        while time.time() < end:
            j = client.get(f"/jobs/{jid}").json()
            if j["state"] == state:
                return j
            time.sleep(0.02)
        self.fail(f"stuck at {j['state'] if j else '?'}; wanted {state}")

    def test_llm_relevance_call_is_recorded_with_real_tokens_and_shows_up_in_usage_summary(self):
        calls = {"n": 0}

        def llm_handler(req: httpx.Request):
            import json
            calls["n"] += 1
            body = json.loads(req.content)
            text = body["messages"][0]["content"]
            ids = [ln.split("id=")[1].split()[0] for ln in text.splitlines() if ln.strip() and ln[0].isdigit()]
            reply = [{"id": i, "score": 90, "why": "matches"} for i in ids]
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}],
                                             "usage": {"prompt_tokens": 250, "completion_tokens": 60, "total_tokens": 310}})

        client, orch = self._client(llm_handler)
        r = client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                       "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        self._wait(client, jid, "script_review")
        client.post(f"/jobs/{jid}/script/approve", json={"reviewer": "Aly"})
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md); the job runs straight
        # through keywords_review to sourcing/vetting on its own.
        self._wait(client, jid, "assets_review")

        self.assertGreater(calls["n"], 0)          # the LLM really was called at least once

        entries = client.get(f"/jobs/{jid}/decisions").json()["entries"]
        llm_calls = [e for e in entries if e["action"] == "llm_call"]
        self.assertEqual(len(llm_calls), calls["n"])
        for e in llm_calls:
            self.assertEqual(e["outputs"]["prompt_tokens"], 250)
            self.assertEqual(e["outputs"]["completion_tokens"], 60)
            self.assertEqual(e["outputs"]["total_tokens"], 310)
            self.assertEqual(e["outputs"]["cost_usd"], 0.0)      # priced at $0/Mtok in this test's settings
            self.assertEqual(e["subject"]["model"], "gemini-3.6-flash")
            self.assertEqual(e["actor"]["type"], "ai")

        out = client.get(f"/jobs/{jid}/usage").json()
        self.assertEqual(out["calls"], calls["n"])
        self.assertEqual(out["prompt_tokens"], 250 * calls["n"])
        self.assertEqual(out["total_tokens"], 310 * calls["n"])
        self.assertEqual(out["by_model"]["gemini-3.6-flash"]["calls"], calls["n"])
        self.assertEqual(out["free_quota"]["gemini-3.6-flash"], {"requests_per_minute": 15, "requests_per_day": 1500})

        # matches a fresh computation straight from the decision log
        self.assertEqual(out, orch.usage_summary(jid))

    def test_no_usage_block_in_reply_still_completes_the_job_with_zero_usage(self):
        def llm_handler(req: httpx.Request):
            import json
            body = json.loads(req.content)
            text = body["messages"][0]["content"]
            ids = [ln.split("id=")[1].split()[0] for ln in text.splitlines() if ln.strip() and ln[0].isdigit()]
            reply = [{"id": i, "score": 90, "why": "matches"} for i in ids]
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})   # no usage

        client, _ = self._client(llm_handler)
        r = client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                       "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        self._wait(client, jid, "script_review")
        client.post(f"/jobs/{jid}/script/approve", json={"reviewer": "Aly"})
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md); the job runs straight
        # through keywords_review to sourcing/vetting on its own.
        self._wait(client, jid, "assets_review")

        out = client.get(f"/jobs/{jid}/usage").json()
        self.assertEqual(out, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                               "cost_usd": 0.0, "by_model": {}, "free_quota": {}})
        entries = client.get(f"/jobs/{jid}/decisions").json()["entries"]
        self.assertNotIn("llm_call", [e["action"] for e in entries])

    def test_usage_endpoint_404s_for_unknown_job(self):
        client, _ = self._client(lambda req: httpx.Response(200, json={"choices": []}))
        self.assertEqual(client.get("/jobs/does-not-exist/usage").status_code, 404)


if __name__ == "__main__":
    unittest.main()
