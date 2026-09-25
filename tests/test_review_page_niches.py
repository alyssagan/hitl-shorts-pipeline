"""Content niches (docs/NICHES.md): the review page's own NICHE_LABELS JS constant (cosmetic display text
only) can't silently drift from pipeline/niches.py's NICHE_LABELS -- same cross-check pattern
tests/test_review_page_stock_budget.py uses for STOCK_SOURCES and tests/test_review_page_source_search.py
uses for SEARCHABLE_SOURCES."""
from __future__ import annotations

import re
import unittest

from pipeline.api.review_page import PAGE
from pipeline.niches import NICHE_LABELS
from pipeline.stages.scenes.script_styles import STYLE_LABELS as SCRIPT_STYLE_LABELS


def _extract(pattern: str) -> str:
    m = re.search(pattern, PAGE, re.S)
    if not m:
        raise AssertionError(f"couldn't find {pattern!r} in review_page.PAGE -- did this code move or change shape?")
    return m.group(0)


class NicheLabelsCrossCheckTests(unittest.TestCase):
    def test_frontend_niche_labels_match_the_backends_exactly(self):
        js = _extract(r"const NICHE_LABELS = \{.*?\};")
        pairs = re.findall(r'(\w+):"([^"]*)"', js)
        self.assertEqual(dict(pairs), NICHE_LABELS)

    def test_frontend_script_style_labels_match_the_backends_exactly(self):
        js = _extract(r"const SCRIPT_STYLE_LABELS = \{.*?\};")
        pairs = re.findall(r'(\w+):"([^"]*)"', js)
        self.assertEqual(dict(pairs), SCRIPT_STYLE_LABELS)


class NicheEvaluationSurfacedInTheCardTests(unittest.TestCase):
    """Not a full render (see test_review_page_stock_budget.py's Node-harness pattern for that level of
    depth) -- just confirms the aesthetic badge and the niche chip actually reference the right fields,
    so a rename of Vetting.niche_evaluation or Job.niche silently breaks nothing here without a test
    failing somewhere."""

    def test_card_reads_niche_evaluation_off_the_asset_vetting(self):
        self.assertIn("v.niche_evaluation", PAGE)
        self.assertIn("aesthetic_fit", PAGE)

    def test_header_reads_niche_off_the_job(self):
        self.assertIn("job.niche", PAGE)

    def test_header_reads_script_style_off_the_job(self):
        self.assertIn("job.script_style", PAGE)


if __name__ == "__main__":
    unittest.main()
