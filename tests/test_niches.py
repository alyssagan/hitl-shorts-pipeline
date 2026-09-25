"""Content niches (requested directly, full "MASTER NICHE PROMPT STRATEGIES" + "Evaluation Engine Output
Schema" spec, docs/NICHES.md). Unit tests for the deterministic evaluation engine
(pipeline/vetting/niche.py) and the keyword-prompt guidance (pipeline/stages/keywords/llm.py) -- pure
functions, no I/O. Orchestrator/API wiring is covered in tests/test_niches_integration.py."""
from __future__ import annotations

import unittest

from pipeline.core.models import Asset, Job, NicheEvaluation, Vetting
from pipeline.niches import NICHE_KEYWORD_GUIDANCE, NICHE_LABELS, NICHES
from pipeline.sources.groups import ARCHIVE_SOURCES, STOCK_SOURCES
from pipeline.stages.keywords.llm import _niche_guidance
from pipeline.vetting import niche as niche_eval


def _asset(source: str, **vetting_kwargs) -> Asset:
    a = Asset(source=source, path="/x")
    a.vetting = Vetting(**vetting_kwargs)
    return a


class EvaluateAssetTests(unittest.TestCase):
    def test_no_niche_means_no_evaluation_at_all(self):
        a = _asset("pexels", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW")
        self.assertIsNone(niche_eval.evaluate_asset(a, None))

    def test_unvetted_asset_gets_no_evaluation_even_with_a_niche(self):
        a = Asset(source="pexels", path="/x")   # vetting is None -- hasn't been through vet_all() yet
        self.assertIsNone(niche_eval.evaluate_asset(a, "true_crime"))

    def test_archive_source_is_excellent_for_true_crime_and_conspiracy(self):
        for niche in ("true_crime", "conspiracy"):
            a = _asset("loc", risk="low", relevance=0.7, relevance_decision="relevant", summary="Risk LOW")
            ev = niche_eval.evaluate_asset(a, niche)
            self.assertEqual(ev.aesthetic_fit, "Excellent", niche)

    def test_stock_source_is_jarring_for_true_crime_and_conspiracy(self):
        for niche in ("true_crime", "conspiracy"):
            a = _asset("pexels", risk="low", relevance=0.7, relevance_decision="relevant", summary="Risk LOW")
            ev = niche_eval.evaluate_asset(a, niche)
            self.assertEqual(ev.aesthetic_fit, "Jarring", niche)

    def test_stock_source_is_excellent_for_pet_product_and_food_bakery(self):
        for niche in ("pet_product", "food_bakery"):
            a = _asset("unsplash", risk="low", relevance=0.7, relevance_decision="relevant", summary="Risk LOW")
            ev = niche_eval.evaluate_asset(a, niche)
            self.assertEqual(ev.aesthetic_fit, "Excellent", niche)

    def test_archive_source_is_jarring_for_pet_product_and_food_bakery(self):
        for niche in ("pet_product", "food_bakery"):
            a = _asset("smithsonian", risk="low", relevance=0.7, relevance_decision="relevant", summary="Risk LOW")
            ev = niche_eval.evaluate_asset(a, niche)
            self.assertEqual(ev.aesthetic_fit, "Jarring", niche)

    def test_nasa_is_rewarded_not_penalized_for_science(self):
        a = _asset("nasa", risk="low", relevance=0.7, relevance_decision="relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "science")
        self.assertEqual(ev.aesthetic_fit, "Excellent")

    def test_generic_stock_is_penalized_for_science(self):
        a = _asset("pixabay", risk="low", relevance=0.7, relevance_decision="relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "science")
        self.assertEqual(ev.aesthetic_fit, "Jarring")

    def test_a_source_in_neither_list_is_acceptable(self):
        # "folder"/"urls" (queryless, own-footage sources) aren't in ARCHIVE_SOURCES or STOCK_SOURCES for
        # any niche -- neither vouched for nor penalized.
        a = _asset("folder", risk="low", relevance=0.7, relevance_decision="relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.aesthetic_fit, "Acceptable")

    def test_relevance_score_is_the_existing_relevance_scaled_to_1_10(self):
        a = _asset("loc", risk="low", relevance=0.83, relevance_decision="relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.relevance_score, 8)   # round(8.3) == 8

    def test_a_true_zero_relevance_still_floors_at_1_not_0(self):
        a = _asset("loc", risk="low", relevance=0.0, relevance_decision="not_relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.relevance_score, 1)

    def test_unscored_relevance_is_none_not_a_fabricated_number(self):
        a = _asset("loc", risk="low", summary="Risk LOW")   # relevance defaults to None
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertIsNone(ev.relevance_score)

    def test_high_risk_is_always_rejected_regardless_of_aesthetic_or_relevance(self):
        a = _asset("loc", risk="high", relevance=0.95, relevance_decision="relevant", summary="Risk HIGH")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.action, "Rejected")

    def test_not_relevant_is_rejected_even_at_low_risk(self):
        a = _asset("loc", risk="low", relevance=0.1, relevance_decision="not_relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.action, "Rejected")

    def test_medium_risk_is_flagged_for_review_not_rejected(self):
        a = _asset("loc", risk="medium", relevance=0.9, relevance_decision="relevant", summary="Risk MEDIUM")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.action, "Flagged for Review")

    def test_jarring_aesthetic_is_flagged_for_review_even_at_low_risk_and_high_relevance(self):
        a = _asset("pexels", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.aesthetic_fit, "Jarring")
        self.assertEqual(ev.action, "Flagged for Review")

    def test_unscored_relevance_is_flagged_for_review_not_approved(self):
        a = _asset("loc", risk="low", summary="Risk LOW")   # relevance_decision defaults to ""
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.action, "Flagged for Review")

    def test_clean_low_risk_relevant_excellent_aesthetic_is_approved(self):
        a = _asset("loc", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.action, "Approved")

    def test_risk_assessment_is_the_existing_summary_verbatim_never_reworded(self):
        a = _asset("loc", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW: no rule fired.")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.risk_assessment, "Risk LOW: no rule fired.")

    def test_niche_evaluated_is_the_display_label_not_the_raw_value(self):
        a = _asset("loc", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW")
        ev = niche_eval.evaluate_asset(a, "true_crime")
        self.assertEqual(ev.niche_evaluated, "True Crime")

    def test_action_suggestion_is_never_applied_to_asset_status(self):
        # apply_to_job() only ever touches a.vetting.niche_evaluation -- never a.status, which is Gate 2's
        # own, human-only decision (docs/NICHES.md).
        a = _asset("loc", risk="high", relevance=0.95, relevance_decision="relevant", summary="Risk HIGH")
        a.status = "pending"
        job = Job(subject="t", niche="true_crime")
        job.assets = [a]
        niche_eval.apply_to_job(job)
        self.assertEqual(a.vetting.niche_evaluation.action, "Rejected")
        self.assertEqual(a.status, "pending")   # untouched


class ApplyToJobAndReportTests(unittest.TestCase):
    def test_apply_to_job_is_a_noop_without_a_niche(self):
        a = _asset("pexels", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW")
        job = Job(subject="t")   # no niche
        job.assets = [a]
        niche_eval.apply_to_job(job)
        self.assertIsNone(a.vetting.niche_evaluation)

    def test_evaluate_job_returns_one_record_per_vetted_asset_with_asset_id(self):
        a1 = _asset("loc", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW")
        a2 = _asset("pexels", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW")
        job = Job(subject="t", niche="true_crime")
        job.assets = [a1, a2]
        niche_eval.apply_to_job(job)
        records = niche_eval.evaluate_job(job)
        self.assertEqual(len(records), 2)
        self.assertEqual({r["asset_id"] for r in records}, {a1.id, a2.id})

    def test_evaluate_job_record_shape_matches_the_requested_schema_exactly(self):
        a = _asset("loc", risk="low", relevance=0.9, relevance_decision="relevant", summary="Risk LOW")
        job = Job(subject="t", niche="true_crime")
        job.assets = [a]
        niche_eval.apply_to_job(job)
        [record] = niche_eval.evaluate_job(job)
        requested_schema_keys = {"niche_evaluated", "relevance_score", "aesthetic_fit", "risk_assessment",
                                  "reasoning", "action"}
        self.assertTrue(requested_schema_keys.issubset(record.keys()))
        self.assertNotIn("method", record)   # bookkeeping-only, deliberately not part of the requested schema

    def test_evaluate_job_is_empty_for_an_asset_never_vetted(self):
        job = Job(subject="t", niche="true_crime")
        job.assets = [Asset(source="loc", path="/x")]   # vetting still None
        self.assertEqual(niche_eval.evaluate_job(job), [])

    def test_apply_to_job_recomputes_every_call_reflecting_the_latest_vetting(self):
        a = _asset("pexels", risk="low", relevance=0.2, relevance_decision="not_relevant", summary="Risk LOW")
        job = Job(subject="t", niche="true_crime")
        job.assets = [a]
        niche_eval.apply_to_job(job)
        self.assertEqual(a.vetting.niche_evaluation.action, "Rejected")
        a.vetting.relevance, a.vetting.relevance_decision = 0.95, "relevant"
        a.vetting.summary = "Risk LOW"
        niche_eval.apply_to_job(job)
        # Still Jarring (pexels/true_crime) -> still Flagged for Review, not Approved, even though
        # relevance turned around -- proves this recomputes from the CURRENT vetting, not a stale copy.
        self.assertEqual(a.vetting.niche_evaluation.action, "Flagged for Review")


class NicheConstantsTests(unittest.TestCase):
    def test_every_niche_has_a_label(self):
        self.assertEqual(set(NICHES), set(NICHE_LABELS))

    def test_every_niche_has_keyword_guidance(self):
        self.assertEqual(set(NICHES), set(NICHE_KEYWORD_GUIDANCE))

    def test_reward_and_penalize_sets_never_overlap_for_the_same_niche(self):
        from pipeline.niches import NICHE_PENALIZE_SOURCES, NICHE_REWARD_SOURCES
        for n in NICHES:
            overlap = NICHE_REWARD_SOURCES.get(n, set()) & NICHE_PENALIZE_SOURCES.get(n, set())
            self.assertEqual(overlap, set(), f"{n}: a source can't be both rewarded and penalized")

    def test_niche_source_sets_are_drawn_from_real_source_groups(self):
        from pipeline.niches import NICHE_PENALIZE_SOURCES, NICHE_REWARD_SOURCES
        known = ARCHIVE_SOURCES | STOCK_SOURCES
        for n in NICHES:
            self.assertTrue(NICHE_REWARD_SOURCES.get(n, set()) <= known, n)
            self.assertTrue(NICHE_PENALIZE_SOURCES.get(n, set()) <= known, n)


class KeywordPromptNicheGuidanceTests(unittest.TestCase):
    def test_no_niche_gives_no_guidance_line(self):
        self.assertEqual(_niche_guidance(None), "")

    def test_every_niche_gives_a_nonempty_guidance_line(self):
        for n in NICHES:
            self.assertTrue(_niche_guidance(n).strip(), n)

    def test_unknown_niche_string_degrades_to_empty_rather_than_crashing(self):
        # Defensive only -- create_job() already rejects an unknown niche before a job can ever have one,
        # but this function shouldn't blow up a keyword round if it's ever called with something odd.
        self.assertEqual(_niche_guidance("not_a_real_niche"), "")


if __name__ == "__main__":
    unittest.main()
