"""The backup LLM provider (config/pipeline.toml [llm_fallback], docs/LOGGING.md "LLM fallback provider"):
post_chat() retries a request against the backup provider once its primary provider is still failing after its
own retries, tags the response with who actually answered, and records tokens/cost under the right model."""
from __future__ import annotations

import json
import unittest

import httpx

from pipeline.core import usage
from pipeline.stages.llm_http import post_chat
from pipeline.stages.registry import build_llm_fallback


class BuildLlmFallbackTests(unittest.TestCase):
    """No env mutation here (build_llm_fallback reads os.environ directly) -- these only exercise the config-only
    paths: disabled, and enabled-but-no-key-anywhere (since a real key would come from the environment, which
    these tests must not depend on or clobber)."""

    def test_disabled_in_config_returns_none(self):
        self.assertIsNone(build_llm_fallback({"llm_fallback": {"enabled": False}}))

    def test_no_settings_at_all_is_the_same_as_disabled_or_unkeyed(self):
        # whatever the real environment has GROQ_API_KEY set to (if anything) is out of this test's control,
        # so only assert the shape when a key IS found, never assume it's absent.
        out = build_llm_fallback({})
        if out is not None:
            self.assertEqual(set(out), {"provider", "base_url", "model", "api_key"})


class PostChatFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.primary_calls = {"n": 0}
        self.backup_calls = {"n": 0}

    def _handler(self, primary_status: int, backup_status: int = 200):
        def h(req: httpx.Request):
            if req.url.host == "primary.example":
                self.primary_calls["n"] += 1
                if primary_status == 200:
                    return httpx.Response(200, json={"choices": [{"message": {"content": "ok-primary"}}],
                                                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
                return httpx.Response(primary_status, json={"error": "primary down"})
            if req.url.host == "backup.example":
                self.backup_calls["n"] += 1
                if backup_status == 200:
                    body = json.loads(req.content)
                    self.assertEqual(body["model"], "backup-model")     # the fallback payload swaps in its own model
                    return httpx.Response(200, json={"choices": [{"message": {"content": "ok-backup"}}],
                                                     "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}})
                return httpx.Response(backup_status, json={"error": "backup down too"})
            raise AssertionError(f"unexpected host {req.url.host}")
        return h

    FALLBACK = {"provider": "groq", "base_url": "http://backup.example", "model": "backup-model", "api_key": "bk"}

    async def test_primary_success_never_touches_the_backup(self):
        h = self._handler(200)
        token = usage.bind()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
                resp = await post_chat(client, "http://primary.example/chat/completions", {}, {"model": "primary-model", "messages": []},
                                       what="x", waits=(), fallback=self.FALLBACK)
            calls = usage.collect()
        finally:
            usage.unbind(token)
        self.assertEqual((self.primary_calls["n"], self.backup_calls["n"]), (1, 0))
        self.assertEqual(resp.json()["choices"][0]["message"]["content"], "ok-primary")
        self.assertFalse(resp.pipeline_fell_back)
        self.assertEqual(resp.pipeline_model, "primary-model")
        self.assertEqual(resp.pipeline_provider, "primary")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "primary-model")

    async def test_primary_exhausted_falls_back_and_succeeds(self):
        h = self._handler(503, backup_status=200)
        token = usage.bind()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
                resp = await post_chat(client, "http://primary.example/chat/completions", {}, {"model": "primary-model", "messages": []},
                                       what="x", waits=(), fallback=self.FALLBACK)
            calls = usage.collect()
        finally:
            usage.unbind(token)
        self.assertEqual((self.primary_calls["n"], self.backup_calls["n"]), (1, 1))    # waits=() -> one primary try, then one backup try
        self.assertEqual(resp.json()["choices"][0]["message"]["content"], "ok-backup")
        self.assertTrue(resp.pipeline_fell_back)
        self.assertEqual((resp.pipeline_model, resp.pipeline_provider), ("backup-model", "groq"))
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["what"], calls[0]["model"], calls[0]["total_tokens"]), ("x (fallback: groq)", "backup-model", 28))

    async def test_both_providers_fail_returns_the_backups_response(self):
        h = self._handler(503, backup_status=500)
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
            resp = await post_chat(client, "http://primary.example/chat/completions", {}, {"model": "primary-model", "messages": []},
                                   what="x", waits=(), fallback=self.FALLBACK)
        self.assertEqual((self.primary_calls["n"], self.backup_calls["n"]), (1, 1))
        self.assertEqual(resp.status_code, 500)
        self.assertTrue(resp.pipeline_fell_back)

    async def test_no_fallback_configured_behaves_exactly_as_before(self):
        h = self._handler(503)
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
            resp = await post_chat(client, "http://primary.example/chat/completions", {}, {"model": "primary-model", "messages": []},
                                   what="x", waits=(), fallback=None)
        self.assertEqual((self.primary_calls["n"], self.backup_calls["n"]), (1, 0))
        self.assertEqual(resp.status_code, 503)

    async def test_network_error_on_primary_with_no_retries_left_still_falls_back(self):
        def h(req: httpx.Request):
            if req.url.host == "primary.example":
                self.primary_calls["n"] += 1
                raise httpx.ConnectError("no route", request=req)
            self.backup_calls["n"] += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok-backup"}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
            resp = await post_chat(client, "http://primary.example/chat/completions", {}, {"model": "primary-model", "messages": []},
                                   what="x", waits=(), fallback=self.FALLBACK)
        self.assertEqual((self.primary_calls["n"], self.backup_calls["n"]), (1, 1))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.pipeline_fell_back)

    async def test_network_error_on_primary_with_no_fallback_still_raises(self):
        def h(req: httpx.Request):
            raise httpx.ConnectError("no route", request=req)
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
            with self.assertRaises(httpx.ConnectError):
                await post_chat(client, "http://primary.example/chat/completions", {}, {"model": "primary-model", "messages": []},
                                what="x", waits=(), fallback=None)


if __name__ == "__main__":
    unittest.main()
