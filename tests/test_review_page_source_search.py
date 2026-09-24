"""Gate 2's "Find more" panel, the inline-search half added after Aly asked why most of the panel was
"just links" and asked for a way to streamline it: Internet Archive, Chronicling America and Wikimedia
Commons get the same "search right here, metadata only, add what you want" treatment YouTube already had,
because (unlike Google Images/FindAGrave/TikTok/Facebook) they already have a free no-key API AND already
run as automated sources elsewhere in the pipeline (pipeline/sources/groups.py's ARCHIVE_SOURCES) -- see
pipeline/api/app.py's INLINE_SEARCH_SOURCES for the backend side of this same list.

Only the pure/state-shape pieces are covered here, same scope as test_review_page_find_more.py -- the
actual search/add HTTP flow is covered end-to-end, offline, in tests/test_source_search.py."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pipeline.api.app import INLINE_SEARCH_SOURCES
from pipeline.api.review_page import PAGE


def _extract(pattern: str) -> str:
    m = re.search(pattern, PAGE, re.S)
    if not m:
        raise AssertionError(f"couldn't find {pattern!r} in review_page.PAGE -- did this code move or change shape?")
    return m.group(0)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SearchableSourcesTests(unittest.TestCase):
    def setUp(self):
        sources_js = _extract(r"const SEARCHABLE_SOURCES = \[.*?\n\];")
        state_js = _extract(r"function srcState\(name\)\{.*?\n\}")
        self.harness = "\n".join(["let srcSearch = {};", sources_js, state_js])

    def _run(self, script_body: str) -> str:
        script = self.harness + "\n" + script_body
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "source_search_test.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_frontend_source_names_match_the_backend_exactly(self):
        # This is the contract that actually matters: a name here that the backend doesn't recognize
        # (or vice versa) would make every search 400 with "source must be one of: ...".
        out = self._run("console.log(JSON.stringify(SEARCHABLE_SOURCES.map(s=>s.name)));")
        self.assertEqual(set(json.loads(out)), set(INLINE_SEARCH_SOURCES))

    def test_labels_match_the_backends_display_names(self):
        out = self._run("console.log(JSON.stringify(Object.fromEntries(SEARCHABLE_SOURCES.map(s=>[s.name,s.label]))));")
        self.assertEqual(json.loads(out), INLINE_SEARCH_SOURCES)

    def test_youtube_and_the_pure_link_out_sites_are_not_in_this_list(self):
        # YouTube already has its own separate yt*/searchYoutube() path (it isn't a pipeline source at
        # all); Google Images/FindAGrave/TikTok/Facebook have no free API to call here -- both stay out
        # of SEARCHABLE_SOURCES on purpose.
        out = self._run("console.log(JSON.stringify(SEARCHABLE_SOURCES.map(s=>s.label)));")
        labels = json.loads(out)
        for excluded in ("YouTube", "Google Images", "FindAGrave", "TikTok", "Facebook"):
            self.assertNotIn(excluded, labels)

    def test_srcState_lazily_creates_one_state_object_per_source(self):
        out = self._run(
            'console.log(JSON.stringify(srcState("archive")));'
        )
        self.assertEqual(json.loads(out), {"results": None, "loading": False, "error": "", "lastQuery": "", "count": 5})

    def test_srcState_returns_the_same_object_on_a_second_call_so_mutations_persist(self):
        out = self._run(
            'srcState("commons").count = 10;\n'
            'console.log(srcState("commons").count);'
        )
        self.assertEqual(out, "10")

    def test_different_sources_get_independent_state(self):
        out = self._run(
            'srcState("archive").count = 3;\n'
            'srcState("chronicling_america").count = 10;\n'
            'console.log(JSON.stringify([srcState("archive").count, srcState("chronicling_america").count]));'
        )
        self.assertEqual(json.loads(out), [3, 10])


if __name__ == "__main__":
    unittest.main()
