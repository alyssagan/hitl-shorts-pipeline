"""The review page's per-keyword asset grouping (docs/REVIEW_UI.md, Gate 2): requested directly --
"different keywords should have a different section, if not enough is pulled up, a next section should
definitely be easy to do" -- in the web UI specifically, not scripts/poc.py. keywordSections() groups the
already-filtered/sorted asset list by which approved keyword's search found each item (Asset.query), gives
every approved keyword a section even if nothing currently shows for it (so an empty section next to a full
one is the visible signal that keyword needs another round), and still surfaces any term that isn't/wasn't
an approved keyword (custom "Search again" terms) as a trailing section rather than dropping it.
sectionSummary() turns job.source_notes (pipeline/stages/sourcing.py's structured per-query trace) into a
one-line "source: kept of found" readout, or "not searched yet" when no note exists for that term at all --
the only way to tell a genuine zero-result search apart from one that was never run.

This logic is inline JavaScript inside pipeline/api/review_page.py's PAGE string (no Python business logic
to unit test directly), so this pulls the *actual* functions out of that string and runs them for real in
Node -- the same approach test_review_page_sort.py uses for the risk/score comparator.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pipeline.api.review_page import PAGE


def _extract(pattern: str) -> str:
    m = re.search(pattern, PAGE, re.S)
    if not m:
        raise AssertionError(f"couldn't find {pattern!r} in review_page.PAGE -- did the code move or change shape?")
    return m.group(0)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class KeywordSectionsTests(unittest.TestCase):
    def setUp(self):
        keyword_sections_js = _extract(r"function keywordSections\(xs\)\{.*?\n\}")
        section_summary_js = _extract(r"function sectionSummary\(notes\)\{.*?\n\}")
        self.harness = "\n".join([keyword_sections_js, section_summary_js])

    def _run(self, job: dict, xs: list) -> list:
        script = (
            self.harness
            + f"\nconst job = {json.dumps(job)};\nconst xs = {json.dumps(xs)};"
            + "\nconsole.log(JSON.stringify(keywordSections(xs)));\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "keyword_sections_test.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_every_approved_keyword_gets_its_own_section_in_order(self):
        job = {"keywords": [
            {"term": "Henry Lee Lucas", "approved": True, "group": "research"},
            {"term": "Williamson County Courthouse Texas", "approved": True, "group": "case"},
        ], "source_notes": []}
        xs = [{"id": "a1", "query": "Henry Lee Lucas"}, {"id": "a2", "query": "Williamson County Courthouse Texas"}]
        sections = self._run(job, xs)
        self.assertEqual([s["term"] for s in sections], ["Henry Lee Lucas", "Williamson County Courthouse Texas"])
        self.assertEqual([a["id"] for a in sections[0]["items"]], ["a1"])
        self.assertEqual(sections[0]["group"], "research")

    def test_an_approved_keyword_with_no_matching_assets_still_gets_an_empty_section(self):
        # This is the "next section should be easy to do" case -- an approved keyword that hasn't produced
        # anything shown yet must still be visible as its own (empty) section, not silently absent.
        job = {"keywords": [{"term": "1980s mainframe computer server", "approved": True, "group": "historical"}],
               "source_notes": []}
        sections = self._run(job, [])
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["items"], [])

    def test_a_term_that_is_not_an_approved_keyword_still_gets_a_trailing_section(self):
        # Custom "Search again" terms, or terms from a keyword that was later un-approved, must not just
        # vanish -- their items still need somewhere to show up.
        job = {"keywords": [{"term": "approved term", "approved": True, "group": "case"}], "source_notes": []}
        xs = [{"id": "a1", "query": "approved term"}, {"id": "a2", "query": "custom typed term"}]
        sections = self._run(job, xs)
        self.assertEqual([s["term"] for s in sections], ["approved term", "custom typed term"])
        self.assertIsNone(sections[1]["group"])

    def test_an_unapproved_keyword_is_not_given_a_section_of_its_own(self):
        job = {"keywords": [{"term": "rejected term", "approved": False, "group": "case"}], "source_notes": []}
        self.assertEqual(self._run(job, []), [])

    def test_items_with_no_recorded_query_are_grouped_together_not_dropped(self):
        job = {"keywords": [], "source_notes": []}
        xs = [{"id": "manual-1", "query": ""}, {"id": "manual-2"}]
        sections = self._run(job, xs)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["term"], "(no search term recorded)")
        self.assertEqual({a["id"] for a in sections[0]["items"]}, {"manual-1", "manual-2"})


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SectionSummaryTests(unittest.TestCase):
    def setUp(self):
        section_summary_js = _extract(r"function sectionSummary\(notes\)\{.*?\n\}")
        self.harness = section_summary_js

    def _run(self, notes: list) -> str:
        script = self.harness + f"\nconsole.log(sectionSummary({json.dumps(notes)}));\n"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "section_summary_test.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_no_notes_means_not_searched_yet(self):
        self.assertEqual(self._run([]), "not searched yet")

    def test_a_genuine_zero_match_search_is_distinguished_from_unsearched(self):
        notes = [{"source": "commons", "query": "x", "found": 0, "kept": 0, "outcome": "ok_zero_matches"}]
        self.assertEqual(self._run(notes), "commons: 0 kept of 0 found")

    def test_kept_and_found_counts_are_reported_per_source(self):
        notes = [{"source": "commons", "query": "x", "found": 24, "kept": 8, "outcome": "ok_kept"}]
        self.assertEqual(self._run(notes), "commons: 8 kept of 24 found")

    def test_multiple_sources_for_the_same_keyword_each_get_their_own_clause(self):
        notes = [
            {"source": "wikipedia", "query": "x", "found": 1, "kept": 1, "outcome": "ok_kept"},
            {"source": "commons", "query": "x", "found": 0, "kept": 0, "outcome": "ok_zero_matches"},
        ]
        self.assertEqual(self._run(notes), "wikipedia: 1 kept of 1 found · commons: 0 kept of 0 found")

    def test_a_query_that_errored_with_nothing_found_is_reported_as_a_failure_not_a_zero_result(self):
        notes = [{"source": "commons", "query": "x", "error": "TimeoutError: timed out", "outcome": "unknown_error"}]
        self.assertEqual(self._run(notes), "commons: search failed")


if __name__ == "__main__":
    unittest.main()
