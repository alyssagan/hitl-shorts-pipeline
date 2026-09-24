"""Gate 2's "Added by link" panel (requested directly, after a real link got lost in the main grid --
it was hidden by the relevance-score filter and sorted last as high risk, at the same time). A
manually pasted-in link (import_method="manual_url", pipeline/sources/urls.py) is always pending the
moment it's added (add_reviewable_asset() never auto-approves), so manualUrlAssets()/visible() in
pipeline/api/review_page.py keep it out of the main filtered/sorted grid and show it in its own
always-visible panel instead, for exactly as long as it's pending. Same approach test_review_page_sort.py
uses: pull the *actual* JS out of review_page.PAGE and run it for real in Node, rather than a
reimplementation that could silently drift from what ships."""
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
        raise AssertionError(f"couldn't find {pattern!r} in review_page.PAGE -- did this code move or change shape?")
    return m.group(0)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ManualLinksVisibilityTests(unittest.TestCase):
    def setUp(self):
        score_js = _extract(r"const score = a =>.*?;")
        below_js = _extract(r"const below = a =>.*?;")
        risk_js = _extract(r"const risk = a =>.*?;")
        manual_js = _extract(r"function manualUrlAssets\(\)\{.*?\n\}")
        visible_js = _extract(r"function visible\(\)\{.*?\n\}")
        self.harness = "\n".join([score_js, below_js, risk_js, manual_js, visible_js])

    def _run(self, assets: list[dict], *, showHidden=False, srcFilter="", kindFilter="", minScore=0.5, sortBy="risk") -> list[str]:
        script = (
            f"let job = {{assets: {json.dumps(assets)}}};\n"
            f"let showHidden = {json.dumps(showHidden)};\n"
            f"let srcFilter = {json.dumps(srcFilter)};\n"
            f"let kindFilter = {json.dumps(kindFilter)};\n"
            f"let minScore = {json.dumps(minScore)};\n"
            f"let sortBy = {json.dumps(sortBy)};\n"
            + self.harness +
            "\nconsole.log(JSON.stringify({visible: visible().map(a=>a.id), manual: manualUrlAssets().map(a=>a.id)}));\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manual_links_test.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def _asset(self, id, **kw):
        base = dict(id=id, source="urls", kind="video", import_method="manual_url", status="pending",
                    vetting={"risk": "high", "relevance": 0.0})
        base.update(kw)
        return base

    def test_a_pending_manual_url_asset_is_kept_out_of_the_main_grid(self):
        out = self._run([self._asset("m1")])
        self.assertEqual(out["visible"], [])
        self.assertEqual(out["manual"], ["m1"])

    def test_a_pending_manual_url_asset_shows_even_though_its_score_is_below_threshold(self):
        # This is the actual bug that prompted the panel: a platform link's caption rarely matches the
        # approved keywords, so it commonly scores under minScore -- below() alone would have hidden it.
        out = self._run([self._asset("m1", vetting={"risk": "high", "relevance": 0.0})], showHidden=False, minScore=0.5)
        self.assertNotIn("m1", out["visible"])   # not in the *main* grid...
        self.assertIn("m1", out["manual"])       # ...but always in the manual-links panel regardless

    def test_a_normal_low_score_asset_is_still_hidden_as_before(self):
        # Regression guard: only manual_url+pending gets the exemption -- an ordinary searched asset
        # below threshold is still hidden the normal way, this isn't a blanket "show everything" change.
        out = self._run([self._asset("s1", source="commons", import_method="search",
                                      vetting={"risk": "low", "relevance": 0.1})], minScore=0.5)
        self.assertEqual(out["visible"], [])
        self.assertEqual(out["manual"], [])

    def test_once_decided_it_leaves_the_manual_panel_and_rejoins_the_main_grid(self):
        out = self._run([self._asset("m1", status="approved", vetting={"risk": "high", "relevance": 0.9})])
        self.assertEqual(out["manual"], [])
        self.assertIn("m1", out["visible"])

    def test_a_manual_url_photo_still_respects_the_kind_filter_once_decided(self):
        out = self._run([self._asset("m1", status="approved", kind="image", vetting={"risk": "low", "relevance": 0.9})],
                        kindFilter="video")
        self.assertEqual(out["manual"], [])       # already decided -- not in the pending panel
        self.assertEqual(out["visible"], [])      # and filtered out of the main grid by kindFilter, same as any asset

    def test_source_searched_assets_are_unaffected(self):
        out = self._run([self._asset("s1", source="commons", import_method="search", status="approved",
                                      vetting={"risk": "low", "relevance": 0.9})])
        self.assertEqual(out["manual"], [])
        self.assertEqual(out["visible"], ["s1"])

    def test_mixed_batch_only_the_pending_manual_ones_are_pulled_out(self):
        assets = [
            self._asset("pending-manual", status="pending"),
            self._asset("decided-manual", status="approved", vetting={"risk": "high", "relevance": 0.9}),
            self._asset("searched", source="commons", import_method="search", status="approved",
                        vetting={"risk": "low", "relevance": 0.9}),
        ]
        out = self._run(assets)
        self.assertEqual(out["manual"], ["pending-manual"])
        self.assertEqual(set(out["visible"]), {"decided-manual", "searched"})

    # ---- "search_pick" (Gate 2's "Find more" -> add one result, pipeline/api/app.py's assets_add_candidate) --
    # same guarantee as manual_url above, added after a real report ("no it just jerks" -- an add that silently
    # succeeded, then the item vanished below the score threshold with no sign it had worked at all).

    def test_a_pending_search_pick_asset_is_kept_out_of_the_main_grid_and_shown_in_the_manual_panel(self):
        out = self._run([self._asset("p1", source="commons", import_method="search_pick", kind="image")])
        self.assertEqual(out["visible"], [])
        self.assertEqual(out["manual"], ["p1"])

    def test_a_pending_search_pick_asset_shows_even_though_its_score_is_below_threshold(self):
        # The exact bug this stamp fixes: a hand-picked archive/commons result whose title doesn't match
        # the approved keywords well commonly scores under minScore -- below() alone would hide it, exactly
        # like the manual_url case above.
        out = self._run([self._asset("p1", source="archive", import_method="search_pick", kind="video",
                                      vetting={"risk": "low", "relevance": 0.05})], showHidden=False, minScore=0.5)
        self.assertNotIn("p1", out["visible"])
        self.assertIn("p1", out["manual"])

    def test_once_decided_a_search_pick_leaves_the_manual_panel_and_rejoins_the_main_grid(self):
        out = self._run([self._asset("p1", source="commons", import_method="search_pick", status="approved",
                                      vetting={"risk": "low", "relevance": 0.9})])
        self.assertEqual(out["manual"], [])
        self.assertIn("p1", out["visible"])

    def test_search_pick_and_manual_url_can_both_be_pending_at_once(self):
        assets = [
            self._asset("pasted", import_method="manual_url"),
            self._asset("picked", source="commons", import_method="search_pick", kind="image"),
            self._asset("searched", source="commons", import_method="search", status="approved",
                        vetting={"risk": "low", "relevance": 0.9}),
        ]
        out = self._run(assets)
        self.assertEqual(set(out["manual"]), {"pasted", "picked"})
        self.assertEqual(out["visible"], ["searched"])


if __name__ == "__main__":
    unittest.main()
