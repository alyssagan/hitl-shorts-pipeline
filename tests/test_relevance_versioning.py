"""Covers the user's actual ask: "are we logging how we scored certain images? which sort of method, and each
time we change method we should be keeping track, so we can assess how different ways to score relevance... and
how they were scored. nothing should be deleted."

Three gaps existed before this file (see docs/SCORING_CHANGELOG.md for the full writeup):
1. The per-asset scoring TIER (tfidf/llm-semantic/keyword-match) never made it into the permanent decision log's
   `vetted_asset` entry -- only an unrelated static string (`rules-v1`, the risk-rules engine's version) did.
2. No scoring ALGORITHM had a version identifier at all, so two real rewrites of the TF-IDF formula earlier this
   session left no trace in any log of which formula produced a given score.
3. The `relevance_scoring` entry's logged "formula" was a single static string that didn't track which method
   (or which version of it) actually ran, and had already gone stale relative to the real TF-IDF math.

This file guards: the version constants stay in sync between where they're defined and where rules.py defaults
to them (so a future version bump can't silently go untracked the same way); the permanent decision log actually
carries the version per asset and per run; and old, already-written entries are never touched (append-only).
"""
from __future__ import annotations

import inspect
import tempfile
import unittest

from pipeline.core.models import Job, Keyword, ProviderChoice
from pipeline.core.orchestrator import Orchestrator, RELEVANCE_FORMULA_BY_METHOD
from pipeline.core.store import JobStore
from pipeline.vetting import llm_relevance, tfidf_relevance
from pipeline.vetting.rules import KEYWORD_MATCH_FORMULA, KEYWORD_MATCH_VERSION, vet_all, vet_asset
from tests.fakes import fake_registry


def mk(id_, title=""):
    from pipeline.core.models import Asset
    return Asset(id=id_, source="x", path="/x", title=title, license="CC0", source_url="https://x/y", author="a")


class VersionConstantsStaySyncedTests(unittest.TestCase):
    """rules.py's vet_asset()/vet_all() can't import tfidf_relevance.py or llm_relevance.py (they import FROM
    rules.py -- importing back would be circular), so their `tfidf_version`/`llm_version` defaults are a
    necessary, separate copy of the real version strings, for direct/unit-test callers that don't pass one
    explicitly. This test is the tripwire: if a future version bump updates the real VERSION constant but not
    rules.py's default, this fails immediately instead of the two silently drifting apart the way the old,
    unversioned `method` field did across two real TF-IDF rewrites this session."""

    def test_tfidf_default_matches_the_real_tfidf_version(self):
        self.assertEqual(inspect.signature(vet_asset).parameters["tfidf_version"].default, tfidf_relevance.VERSION)
        self.assertEqual(inspect.signature(vet_all).parameters["tfidf_version"].default, tfidf_relevance.VERSION)

    def test_llm_default_matches_the_real_llm_semantic_version(self):
        self.assertEqual(inspect.signature(vet_asset).parameters["llm_version"].default, llm_relevance.VERSION)
        self.assertEqual(inspect.signature(vet_all).parameters["llm_version"].default, llm_relevance.VERSION)

    def test_orchestrators_formula_lookup_covers_all_three_current_versions(self):
        # If a version is bumped without adding it (and its formula) to orchestrator.RELEVANCE_FORMULA_BY_METHOD,
        # the relevance_scoring decision-log entry would silently fall back to "(unknown method version...)" for
        # every asset scored by it -- this confirms all three live versions are covered.
        self.assertEqual(RELEVANCE_FORMULA_BY_METHOD[KEYWORD_MATCH_VERSION], KEYWORD_MATCH_FORMULA)
        self.assertEqual(RELEVANCE_FORMULA_BY_METHOD[tfidf_relevance.VERSION], tfidf_relevance.FORMULA)
        self.assertEqual(RELEVANCE_FORMULA_BY_METHOD[llm_relevance.VERSION], llm_relevance.FORMULA)


def _job_with_assets(assets) -> Job:
    job = Job(subject="jack the ripper", providers=ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=[]))
    job.keywords = [Keyword(term="whitechapel 1888", source="human", approved=True)]
    job.assets = assets
    return job


class DecisionLogCarriesMethodAndVersionTests(unittest.TestCase):
    """Orchestrator-level: confirms the actual gap the user flagged is closed -- the permanent, tamper-evident
    decisions.jsonl (never edited, only appended to) now says which scoring method+version produced each
    asset's score, not just an unrelated risk-rules version string."""

    def _apply(self, assets, scores=None):
        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry()
            orch = Orchestrator(JobStore(d), reg, settings={})
            job = _job_with_assets(assets)
            orch.store.save(job)
            orch._apply_vetting(job, scores)
            return job, list(orch.store.decisions(job.id).entries())

    def test_vetted_asset_entry_carries_the_real_relevance_method_and_version(self):
        assets = [mk("a1", "Whitechapel Road 1888"), mk("a2", "unrelated caption")]
        job, entries = self._apply(assets)                        # no scores arg -> tfidf_scores_batch={}, llm=None -> keyword-match
        vetted = [e for e in entries if e["action"] == "vetted_asset"]
        self.assertEqual(len(vetted), 2)
        by_id = {a.id: a for a in job.assets}
        for e in vetted:
            asset_id = e["subject"]["asset_id"]
            live = by_id[asset_id].vetting
            self.assertEqual(e["logic"]["method_version"], live.method_version or None,
                              "the decision log's method_version must match what's actually on the asset")
            self.assertEqual(e["logic"]["scoring_method"], live.scoring_method or None,
                              "the decision log's scoring_method must match what's actually on the asset")
            # It's the unrelated risk-rules version -- confirms we did NOT remove or repurpose the old field.
            self.assertEqual(e["logic"]["method"], "rules-v1")
        self.assertTrue(all(e["logic"]["method_version"] == KEYWORD_MATCH_VERSION for e in vetted),
                         [e["logic"]["method_version"] for e in vetted])
        # No TF-IDF batch and no LLM scorer -> the keyword-match fallback fired for every asset, and it should
        # say so (docs/EVALUATION.md: never silently look like a first-class score).
        self.assertTrue(all(e["logic"]["scoring_fallback_note"] for e in vetted),
                         [e["logic"]["scoring_fallback_note"] for e in vetted])

    def test_tfidf_scores_are_recorded_with_the_real_tfidf_version(self):
        a = mk("a1", "Whitechapel Road 1888")
        job, entries = self._apply([a], ({"a1": (0.8, "matched")}, None))   # tfidf batch given, no LLM scorer
        vetted = next(e for e in entries if e["action"] == "vetted_asset")
        self.assertEqual(vetted["logic"]["scoring_method"], "tfidf")
        self.assertEqual(vetted["logic"]["method_version"], tfidf_relevance.VERSION)

    def test_llm_scores_are_recorded_with_the_real_llm_semantic_version(self):
        a = mk("a1")
        job, entries = self._apply([a], ({}, {"a1": (0.9, "on topic")}))   # LLM scored it this round
        vetted = next(e for e in entries if e["action"] == "vetted_asset")
        self.assertEqual(vetted["logic"]["scoring_method"], "llm-semantic")
        self.assertEqual(vetted["logic"]["method_version"], llm_relevance.VERSION)

    def test_relevance_scoring_entry_lists_only_methods_actually_used_with_real_formulas(self):
        assets = [mk("a1", "Whitechapel Road 1888")]
        _, entries = self._apply(assets)                          # tfidf batch empty, no LLM -> falls to keyword-match
        run_entry = next(e for e in entries if e["action"] == "relevance_scoring")
        logic = run_entry["logic"]
        self.assertEqual(logic["methods_used_this_round"], [KEYWORD_MATCH_VERSION])
        self.assertEqual(logic["formulas_by_method"], {KEYWORD_MATCH_VERSION: KEYWORD_MATCH_FORMULA})
        self.assertEqual(logic["changelog"], "docs/SCORING_CHANGELOG.md")
        # The old, single static "formula" key is gone -- it was the one that went stale (docs/SCORING_CHANGELOG.md).
        self.assertNotIn("formula", logic)

    def test_relevance_scoring_entry_lists_multiple_methods_when_a_round_mixes_tiers(self):
        assets = [mk("a1", "Whitechapel Road 1888"), mk("a2")]
        # a1 gets a tfidf score, a2 gets an LLM score (as _score_relevance would hand off borderline assets) --
        # a real "hybrid" round, same as docs/SCORING.md describes.
        _, entries = self._apply(assets, ({"a1": (0.6, "partial")}, {"a2": (0.9, "on topic")}))
        run_entry = next(e for e in entries if e["action"] == "relevance_scoring")
        logic = run_entry["logic"]
        self.assertEqual(set(logic["methods_used_this_round"]), {tfidf_relevance.VERSION, llm_relevance.VERSION})
        self.assertEqual(logic["formulas_by_method"], {tfidf_relevance.VERSION: tfidf_relevance.FORMULA,
                                                         llm_relevance.VERSION: llm_relevance.FORMULA})


class NothingIsEverDeletedTests(unittest.TestCase):
    """The user's explicit constraint: adding this tracking must never touch or remove anything already written.
    Runs vetting twice ("search again"-style, reusing a prior LLM score) and confirms every entry from round 1 is
    still present, byte-for-byte, after round 2 -- decisions.jsonl is append-only by construction (core/decisions.py's
    hash chain), this just confirms the new fields don't change that."""

    def test_two_vetting_rounds_only_append_never_rewrite_round_ones_entries(self):
        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry()
            orch = Orchestrator(JobStore(d), reg, settings={})
            job = _job_with_assets([mk("a1", "Whitechapel Road 1888"), mk("a2", "unrelated")])
            orch.store.save(job)
            orch._apply_vetting(job, ({"a1": (0.8, "matched")}, {"a2": (0.9, "on topic")}))
            round1 = list(orch.store.decisions(job.id).entries())
            self.assertGreater(len(round1), 0)

            # Round 2: a2 already has a kept llm-semantic score and isn't resent; a1's tfidf score is refreshed.
            orch._apply_vetting(job, ({"a1": (0.85, "matched better")}, {}))
            round2 = list(orch.store.decisions(job.id).entries())

            self.assertGreater(len(round2), len(round1))
            self.assertEqual(round2[:len(round1)], round1, "round 1's entries were mutated, not just appended to")
            ok, bad_seq = orch.store.decisions(job.id).verify()
            self.assertTrue(ok, f"hash chain broken at seq {bad_seq}")

            a2 = next(a for a in job.assets if a.id == "a2")
            self.assertEqual((a2.vetting.scoring_method, a2.vetting.method_version),
                              ("llm-semantic", llm_relevance.VERSION))   # kept, not downgraded


if __name__ == "__main__":
    unittest.main()
