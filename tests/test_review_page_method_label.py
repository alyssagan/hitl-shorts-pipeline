"""The review page's "scored by:" label (pipeline/api/review_page.py's `card()`) reads `scoring_method` and
`method_version` as SEPARATE fields (docs/SCORING_CHANGELOG.md), plus an optional `scoring_fallback_note` --
never a single combined/prefix-matched string the way an older version of this page (and this test) used to.
Like tests/test_review_page_sort.py, this pulls the actual inline JS out of the PAGE string and runs it for
real in Node, so a change here that breaks the label fails this test rather than only being noticed by eye in
the browser."""
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
        raise AssertionError(f"couldn't find {pattern!r} in review_page.PAGE -- did the method-label code move or change shape?")
    return m.group(0)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ScoredByLabelTests(unittest.TestCase):
    def setUp(self):
        self.harness = _extract(r"const scoredBy = .*?;")

    def _scored_by(self, *, scoring_method="", method_version="", scoring_fallback_note="") -> str:
        v = json.dumps({"scoring_method": scoring_method, "method_version": method_version,
                         "scoring_fallback_note": scoring_fallback_note})
        script = f"const v = {v};\n" + self.harness + "\nconsole.log(JSON.stringify(scoredBy));\n"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "label_test.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_tfidf_shows_method_and_exact_version(self):
        self.assertEqual(self._scored_by(scoring_method="tfidf", method_version="tfidf-v1"),
                          "scored by: tfidf [tfidf-v1]")

    def test_llm_semantic_shows_method_and_exact_version(self):
        self.assertEqual(self._scored_by(scoring_method="llm-semantic", method_version="llm-semantic-v1"),
                          "scored by: llm-semantic [llm-semantic-v1]")

    def test_keyword_match_with_a_fallback_note_appends_it_in_parens(self):
        out = self._scored_by(scoring_method="keyword-match", method_version="keyword-match-v1",
                               scoring_fallback_note="LLM unavailable/failed for this item")
        self.assertEqual(out, "scored by: keyword-match [keyword-match-v1] (LLM unavailable/failed for this item)")

    def test_unknown_future_method_still_prints_as_is_not_silently_empty(self):
        self.assertEqual(self._scored_by(scoring_method="some-new-method", method_version="some-new-method-v9"),
                          "scored by: some-new-method [some-new-method-v9]")

    def test_not_scored_at_all(self):
        self.assertEqual(self._scored_by(), "scored by: (not scored)")


if __name__ == "__main__":
    unittest.main()
