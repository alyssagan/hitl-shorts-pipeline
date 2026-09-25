"""Which env var wins for each LLM call site's API key (pipeline/stages/registry.py). [keywords] and
[relevance] already had a per-stage override (KEYWORD_LLM_API_KEY / RELEVANCE_LLM_API_KEY) that falls back to
whatever env var [<section>].api_key_env names, itself defaulting to GEMINI_API_KEY -- so all three stages can
share one Gemini key/project by default, or each be pointed at its own key/project (and so its own free-tier
quota pool) without touching the other two. [script] is the newest to get this: it used to read only
script_cfg["api_key_env"] directly, with no SCRIPT_LLM_API_KEY override at all. This file pins that precedence
down for all three stages so a future refactor can't silently drop the override layer for one of them."""
import os
import tempfile
import unittest
from unittest import mock

from pipeline.stages.registry import build_default_registry

ENV_VARS = (
    "GEMINI_API_KEY", "KEYWORD_LLM_API_KEY", "RELEVANCE_LLM_API_KEY", "SCRIPT_LLM_API_KEY",
    "MY_KEYWORD_KEY", "MY_RELEVANCE_KEY", "MY_SCRIPT_KEY",
)


def clean_env():
    return mock.patch.dict(os.environ, {v: "" for v in ENV_VARS}, clear=False)


class ScriptWriterKeyResolutionTests(unittest.TestCase):
    """script_cfg["provider"] defaults to "llm", so build_default_registry({}) always tries to build a
    ScriptWriter; scene_stage("mpt").writer is None only when no key resolves at all."""

    def _writer(self, settings, env):
        with clean_env(), mock.patch.dict(os.environ, env, clear=False):
            with tempfile.TemporaryDirectory():
                reg = build_default_registry(settings)
                return reg.scene_stage("mpt").writer

    def test_no_key_anywhere_means_no_script_writer(self):
        self.assertIsNone(self._writer({}, {}))

    def test_falls_back_to_the_default_gemini_api_key(self):
        w = self._writer({}, {"GEMINI_API_KEY": "gem-key"})
        self.assertIsNotNone(w)
        self.assertEqual(w.api_key, "gem-key")

    def test_script_cfg_api_key_env_names_a_different_variable(self):
        settings = {"script": {"api_key_env": "MY_SCRIPT_KEY"}}
        w = self._writer(settings, {"MY_SCRIPT_KEY": "custom-key", "GEMINI_API_KEY": "gem-key"})
        self.assertEqual(w.api_key, "custom-key", "api_key_env should win over the GEMINI_API_KEY default")

    def test_script_llm_api_key_overrides_api_key_env(self):
        settings = {"script": {"api_key_env": "MY_SCRIPT_KEY"}}
        w = self._writer(settings, {"SCRIPT_LLM_API_KEY": "override-key", "MY_SCRIPT_KEY": "custom-key"})
        self.assertEqual(w.api_key, "override-key", "SCRIPT_LLM_API_KEY should win over api_key_env")

    def test_script_llm_api_key_does_not_leak_into_keywords_or_relevance(self):
        """The whole point of a per-stage key: setting SCRIPT_LLM_API_KEY alone must not accidentally
        give keywords/relevance a key (and so a provider) they weren't configured with."""
        with clean_env(), mock.patch.dict(os.environ, {"SCRIPT_LLM_API_KEY": "script-only-key"}, clear=False):
            reg = build_default_registry({})
            self.assertEqual(reg.scene_stage("mpt").writer.api_key, "script-only-key")
            self.assertIsNone(reg.relevance_scorer(), "no GEMINI_API_KEY/RELEVANCE_LLM_API_KEY set -> no scorer")

    def test_provider_mpt_never_builds_a_script_writer_even_with_a_key(self):
        settings = {"script": {"provider": "mpt"}}
        w = self._writer(settings, {"SCRIPT_LLM_API_KEY": "script-only-key"})
        self.assertIsNone(w)


class KeywordAndRelevanceKeyResolutionStillWorkTheSameWay(unittest.TestCase):
    """Baseline coverage for the two stages SCRIPT_LLM_API_KEY was modeled on, so the three stay consistent."""

    def test_keyword_llm_api_key_overrides_the_shared_default(self):
        with clean_env(), mock.patch.dict(os.environ, {"KEYWORD_LLM_API_KEY": "kw-key", "GEMINI_API_KEY": "gem-key"}, clear=False):
            reg = build_default_registry({})
            self.assertEqual(reg.keyword_stage("llm").api_key, "kw-key")

    def test_relevance_llm_api_key_overrides_the_shared_default(self):
        with clean_env(), mock.patch.dict(os.environ, {"RELEVANCE_LLM_API_KEY": "rel-key", "GEMINI_API_KEY": "gem-key"}, clear=False):
            reg = build_default_registry({})
            self.assertEqual(reg.relevance_scorer().api_key, "rel-key")

    def test_relevance_falls_back_to_gemini_api_key_by_default_like_keywords(self):
        # Without RELEVANCE_LLM_API_KEY, relevance reads whatever env var [relevance].api_key_env names,
        # falling back to [keywords].api_key_env, falling back to "GEMINI_API_KEY" -- NOT the literal
        # KEYWORD_LLM_API_KEY override (that's a separate override layer, only for the keywords stage).
        with clean_env(), mock.patch.dict(os.environ, {"GEMINI_API_KEY": "gem-key", "KEYWORD_LLM_API_KEY": "kw-only-key"}, clear=False):
            reg = build_default_registry({})
            self.assertEqual(reg.relevance_scorer().api_key, "gem-key")
            self.assertEqual(reg.keyword_stage("llm").api_key, "kw-only-key", "keywords' own override should still win for keywords")


if __name__ == "__main__":
    unittest.main()
