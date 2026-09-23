"""Covers pipeline/evaluation/report.py's metric math against hand-built RELEVANCE_LABELS.jsonl-shaped rows --
the kind of thing that's easy to get backwards (which denominator goes with which rate), so every metric gets a
fixture where the right answer is obvious by inspection."""
from __future__ import annotations

import unittest

from pipeline.evaluation.report import DEFAULT_SPARSE_N, UNVERSIONED_KEY, RateStat, build_report, format_report


def row(asset_id, label, machine_decision, *, scoring_method="tfidf", method_version="tfidf-v1",
        threshold=0.5, dup_flag=False):
    return {"asset_id": asset_id, "label": label, "machine_decision": machine_decision,
            "scoring_method": scoring_method, "method_version": method_version,
            "relevance_threshold": threshold, "duplicate_flag_fired": dup_flag}


class RateStatTests(unittest.TestCase):
    def test_zero_denominator_is_n_a_not_a_crash(self):
        self.assertIsNone(RateStat(0, 0).rate)
        self.assertIn("n/a", RateStat(0, 0).render())

    def test_sparse_warning_shows_below_threshold_only(self):
        self.assertIn("sparse", RateStat(1, 2).render(sparse_n=5))
        self.assertNotIn("sparse", RateStat(5, 10).render(sparse_n=5))

    def test_percent_rendering(self):
        self.assertEqual(RateStat(1, 4).render(sparse_n=1), "25.0% (n=4)")


class GroupingTests(unittest.TestCase):
    def test_groups_by_method_and_version_separately(self):
        rows = [row("a1", "use", "relevant", scoring_method="tfidf", method_version="tfidf-v1"),
                row("a2", "use", "relevant", scoring_method="tfidf", method_version="tfidf-v2-experimental"),
                row("a3", "use", "relevant", scoring_method="llm-semantic", method_version="llm-semantic-v1")]
        report = build_report(rows)
        keys = {g.key for g in report.groups}
        self.assertEqual(keys, {"tfidf [tfidf-v1]", "tfidf [tfidf-v2-experimental]", "llm-semantic [llm-semantic-v1]"})

    def test_rows_with_no_scoring_method_land_in_the_unversioned_bucket(self):
        rows = [row("a1", "use", "relevant", scoring_method="", method_version=""),
                row("a2", "use", "relevant", scoring_method="tfidf", method_version="tfidf-v1")]
        report = build_report(rows)
        keys = {g.key for g in report.groups}
        self.assertIn(UNVERSIONED_KEY, keys)
        unversioned = next(g for g in report.groups if g.key == UNVERSIONED_KEY)
        self.assertEqual(unversioned.n, 1)

    def test_missing_scoring_method_key_entirely_also_lands_in_unversioned(self):
        # Older RELEVANCE_LABELS.jsonl rows, written before this feature existed, have no scoring_method/
        # method_version keys at all (not even empty strings) -- must not KeyError, must not be guessed at.
        rows = [{"asset_id": "old1", "label": "use", "machine_decision": ""}]
        report = build_report(rows)
        self.assertEqual(len(report.groups), 1)
        self.assertEqual(report.groups[0].key, UNVERSIONED_KEY)


class SelectionRateTests(unittest.TestCase):
    """Use yield / irrelevant selection rate / duplicate selection rate: denominator is every labeled row the
    MACHINE selected (machine_decision == "relevant"), regardless of what the human ultimately labeled it."""

    def test_yield_and_selection_rates_denominator_is_machine_selected_only(self):
        rows = [
            row("a1", "use", "relevant"), row("a2", "use", "relevant"), row("a3", "irrelevant", "relevant"),
            row("a4", "duplicate", "relevant"),
            row("a5", "use", "not_relevant"),          # machine rejected this one -- must not count in these denominators
        ]
        g = build_report(rows).groups[0]
        self.assertEqual((g.use_yield.n, g.use_yield.d), (2, 4))
        self.assertEqual((g.irrelevant_selection_rate.n, g.irrelevant_selection_rate.d), (1, 4))
        self.assertEqual((g.duplicate_selection_rate.n, g.duplicate_selection_rate.d), (1, 4))
        self.assertAlmostEqual(g.use_yield.rate, 0.5)


class MissedUseTests(unittest.TestCase):
    """denominator = every labeled row the MACHINE rejected/hid, any label; numerator = how many of those were
    labeled 'use' by a human anyway."""

    def test_missed_use_denominator_is_machine_rejected_any_label(self):
        rows = [
            row("a1", "use", "not_relevant"),           # a real miss
            row("a2", "irrelevant", "not_relevant"),     # correctly hidden
            row("a3", "duplicate", "not_relevant"),       # rejected, and happened to be a duplicate -- still counts in denominator
            row("a4", "use", "relevant"),                 # machine selected this one -- excluded from this metric
        ]
        g = build_report(rows).groups[0]
        self.assertEqual((g.missed_use.n, g.missed_use.d), (1, 3))


class AgreementFpFnTests(unittest.TestCase):
    """Only rows where the label is use/irrelevant (duplicate excluded -- it's a separate axis) AND the machine
    decision is known (relevant/not_relevant, not "") count toward agreement/FP/FN."""

    def test_duplicate_labeled_rows_are_excluded_from_agreement_entirely(self):
        rows = [
            row("a1", "use", "relevant"),        # agree
            row("a2", "irrelevant", "not_relevant"),  # agree
            row("a3", "irrelevant", "relevant"),   # false positive
            row("a4", "use", "not_relevant"),      # false negative
            row("a5", "duplicate", "relevant"),     # excluded from agreement/FP/FN (not from selection-rate above)
        ]
        g = build_report(rows).groups[0]
        self.assertEqual(g.agreement.d, 4)                  # a5 excluded
        self.assertEqual(g.agreement.n, 2)
        self.assertEqual((g.false_positive.n, g.false_positive.d), (1, 2))   # of the 2 comparable machine-selected (a1,a3)
        self.assertEqual((g.false_negative.n, g.false_negative.d), (1, 2))   # of the 2 comparable machine-rejected (a2,a4)

    def test_unknown_machine_decision_rows_are_excluded_and_counted(self):
        rows = [row("a1", "use", ""), row("a2", "use", "relevant")]
        g = build_report(rows).groups[0]
        self.assertEqual(g.unknown_machine_decision, 1)
        self.assertEqual(g.agreement.d, 1)                  # only a2 is comparable


class DuplicateDetectionTests(unittest.TestCase):
    """Evaluated against its own machine signal (duplicate_flag_fired), entirely separate from relevance
    agreement above."""

    def test_precision_and_recall_use_independent_denominators(self):
        rows = [
            row("a1", "duplicate", "relevant", dup_flag=True),    # flagged AND labeled duplicate
            row("a2", "use", "relevant", dup_flag=True),           # flagged but NOT a duplicate (false positive for the flag)
            row("a3", "duplicate", "relevant", dup_flag=False),    # labeled duplicate but flag never fired (missed by the flag)
            row("a4", "use", "relevant", dup_flag=False),
        ]
        g = build_report(rows).groups[0]
        # precision: of the 2 flagged (a1, a2), 1 was actually labeled duplicate
        self.assertEqual((g.dup_flag_precision.n, g.dup_flag_precision.d), (1, 2))
        # recall: of the 2 labeled duplicate (a1, a3), 1 had the flag fired
        self.assertEqual((g.dup_flag_recall.n, g.dup_flag_recall.d), (1, 2))


class ThresholdProvenanceTests(unittest.TestCase):
    def test_uses_the_recorded_threshold_never_a_current_config_value(self):
        rows = [row("a1", "use", "relevant", threshold=0.5), row("a2", "use", "relevant", threshold=0.6)]
        g = build_report(rows).groups[0]
        self.assertEqual(g.thresholds_seen, {0.5, 0.6})


class FormatReportTests(unittest.TestCase):
    def test_empty_report_says_so_without_crashing(self):
        text = format_report(build_report([]))
        self.assertIn("No labeled data yet", text)

    def test_format_includes_group_and_sparse_marker(self):
        rows = [row("a1", "use", "relevant")]
        text = format_report(build_report(rows), sparse_n=DEFAULT_SPARSE_N)
        self.assertIn("tfidf [tfidf-v1]", text)
        self.assertIn("sparse", text)   # n=1 selected is well under the default sparse threshold


if __name__ == "__main__":
    unittest.main()
