"""The review page's default asset ordering (docs/REVIEW_UI.md): safest first (low risk, then medium, then
high), and within each risk level the best-scoring (most relevant) items first. The sort itself is a small bit
of inline JavaScript inside pipeline/api/review_page.py's PAGE string (no Python business logic to unit test
directly), so this pulls the *actual* `score`/`risk`/`by` snippets out of that string and runs them for real in
Node -- a change to the comparator that regresses the ordering fails this test even though the logic lives in a
big HTML/JS blob, not a reimplementation that could silently drift from what ships.
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
        raise AssertionError(f"couldn't find {pattern!r} in review_page.PAGE -- did the sort code move or change shape?")
    return m.group(0)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ReviewPageSortTests(unittest.TestCase):
    def setUp(self):
        score_js = _extract(r"const score = a =>.*?;")
        risk_js = _extract(r"const risk = a =>.*?;")
        by_js = _extract(r"const by = \{.*?\};")
        self.harness = "\n".join([score_js, risk_js, by_js])

    def _sort(self, assets: list[dict], key: str) -> list[str]:
        script = self.harness + f"\nconst assets = {json.dumps(assets)};\nconsole.log(JSON.stringify([...assets].sort(by.{key}).map(a=>a.id)));\n"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sort_test.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_low_risk_first_then_medium_then_high(self):
        assets = [
            {"id": "high-a", "vetting": {"risk": "high", "relevance": 0.9}},
            {"id": "low-a", "vetting": {"risk": "low", "relevance": 0.1}},
            {"id": "medium-a", "vetting": {"risk": "medium", "relevance": 0.5}},
            {"id": "high-b", "vetting": {"risk": "high", "relevance": 0.2}},
        ]
        order = self._sort(assets, "risk")
        # every low-risk id appears before every medium one, which appears before every high one
        risk_of = {"low-a": 0, "medium-a": 1, "high-a": 2, "high-b": 2}
        self.assertEqual([risk_of[i] for i in order], sorted(risk_of[i] for i in order))

    def test_within_a_risk_level_best_score_comes_first(self):
        assets = [
            {"id": "low-weak", "vetting": {"risk": "low", "relevance": 0.1}},
            {"id": "low-strong", "vetting": {"risk": "low", "relevance": 0.9}},
            {"id": "high-weak", "vetting": {"risk": "high", "relevance": 0.2}},
            {"id": "high-strong", "vetting": {"risk": "high", "relevance": 0.8}},
        ]
        order = self._sort(assets, "risk")
        self.assertEqual(order, ["low-strong", "low-weak", "high-strong", "high-weak"])

    def test_full_ordering_low_to_high_and_best_first_within_each(self):
        assets = [
            {"id": "a", "vetting": {"risk": "high", "relevance": 0.9}},
            {"id": "b", "vetting": {"risk": "low", "relevance": 0.1}},
            {"id": "c", "vetting": {"risk": "low", "relevance": 0.9}},
            {"id": "d", "vetting": {"risk": "medium", "relevance": 0.5}},
            {"id": "e", "vetting": {"relevance": 0.99}},           # no risk on the asset -> treated as low
            {"id": "f", "vetting": {"risk": "high", "relevance": 0.2}},
        ]
        self.assertEqual(self._sort(assets, "risk"), ["e", "c", "b", "d", "a", "f"])

    def test_items_with_no_score_sort_last_within_their_risk_level(self):
        assets = [
            {"id": "low-scored", "vetting": {"risk": "low", "relevance": 0.5}},
            {"id": "low-unscored", "vetting": {"risk": "low", "relevance": None}},
        ]
        self.assertEqual(self._sort(assets, "risk"), ["low-scored", "low-unscored"])


if __name__ == "__main__":
    unittest.main()
