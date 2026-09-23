"""Gate 2's "Find more" panel (requested directly, after establishing that the automated sources are
structurally limited to free/openly-licensed archive APIs and can't see platform videos, press photo
archives, or public records -- which a specific case like this often actually needs). It never fetches
or adds anything itself: it opens a real search on other free sites, prefilled from the job subject,
an approved keyword, or a checklist item, so the reviewer isn't retyping case details into six different
search boxes by hand. Whatever's found still comes back in through "Add links" or a scene drop, same as
always. Same approach as test_review_page_sort.py / test_review_page_manual_links.py: extract the actual
JS out of review_page.PAGE and run it for real in Node, rather than a reimplementation that could drift."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from pipeline.api.review_page import PAGE


def _extract(pattern: str) -> str:
    m = re.search(pattern, PAGE, re.S)
    if not m:
        raise AssertionError(f"couldn't find {pattern!r} in review_page.PAGE -- did this code move or change shape?")
    return m.group(0)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FindMoreTests(unittest.TestCase):
    def setUp(self):
        sites_js = _extract(r"const FIND_SITES = \[.*?\n\];")
        suggestions_js = _extract(r"function findMoreSuggestions\(\)\{.*?\n\}")
        query_js = _extract(r"function findMoreQuery\(\)\{.*?\}")
        self.harness = "\n".join([sites_js, suggestions_js, query_js])

    def _run(self, script_body: str):
        script = self.harness + "\n" + script_body
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "find_more_test.js"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def _job(self, **kw):
        base = dict(subject="Richard Speck", keywords=[], visual_checklist=[])
        base.update(kw)
        return base

    # ---- findMoreSuggestions --------------------------------------------------------------------

    def test_includes_the_job_subject_first(self):
        out = self._run(f"let job={json.dumps(self._job())};\nconsole.log(JSON.stringify(findMoreSuggestions()));")
        self.assertEqual(json.loads(out)[0], "Richard Speck")

    def test_pulls_in_approved_keyword_terms_entities_and_aliases(self):
        job = self._job(keywords=[
            {"term": "Corazon Amurao interview", "approved": True, "entity": "Corazon Amurao", "aliases": ["Cora Amurao"]},
        ])
        out = self._run(f"let job={json.dumps(job)};\nconsole.log(JSON.stringify(findMoreSuggestions()));")
        got = json.loads(out)
        for expected in ["Corazon Amurao interview", "Corazon Amurao", "Cora Amurao"]:
            self.assertIn(expected, got)

    def test_unapproved_keywords_are_left_out(self):
        job = self._job(keywords=[{"term": "should not appear", "approved": False}])
        out = self._run(f"let job={json.dumps(job)};\nconsole.log(JSON.stringify(findMoreSuggestions()));")
        self.assertNotIn("should not appear", json.loads(out))

    def test_includes_visual_checklist_labels(self):
        job = self._job(visual_checklist=[{"label": "a period photo of the hospital exterior"}])
        out = self._run(f"let job={json.dumps(job)};\nconsole.log(JSON.stringify(findMoreSuggestions()));")
        self.assertIn("a period photo of the hospital exterior", json.loads(out))

    def test_dedupes_case_insensitively(self):
        job = self._job(subject="Richard Speck",
                         keywords=[{"term": "richard speck", "approved": True, "entity": "", "aliases": []}])
        out = self._run(f"let job={json.dumps(job)};\nconsole.log(JSON.stringify(findMoreSuggestions()));")
        got = json.loads(out)
        self.assertEqual(sum(1 for x in got if x.lower() == "richard speck"), 1)

    def test_blank_entity_and_alias_fields_are_skipped_not_added_as_empty_strings(self):
        job = self._job(keywords=[{"term": "x", "approved": True, "entity": "", "aliases": [""]}])
        out = self._run(f"let job={json.dumps(job)};\nconsole.log(JSON.stringify(findMoreSuggestions()));")
        self.assertNotIn("", json.loads(out))

    # ---- findMoreQuery ----------------------------------------------------------------------------

    def test_query_defaults_to_the_job_subject_when_untouched(self):
        out = self._run(f"let job={json.dumps(self._job())};\nlet findQuery=null;\nconsole.log(findMoreQuery());")
        self.assertEqual(out, "Richard Speck")

    def test_query_reflects_what_the_reviewer_typed_once_touched(self):
        out = self._run(f"let job={json.dumps(self._job())};\nlet findQuery=\"Corazon Amurao\";\nconsole.log(findMoreQuery());")
        self.assertEqual(out, "Corazon Amurao")

    # ---- FIND_SITES -------------------------------------------------------------------------------

    def test_every_site_url_is_correctly_encoded_and_points_at_the_expected_host(self):
        expected_hosts = {
            "Internet Archive": "archive.org",
            "Chronicling America": "chroniclingamerica.loc.gov",
            "Wikimedia Commons": "commons.wikimedia.org",
            "YouTube": "www.youtube.com",
            "Google Images": "www.google.com",
            "FindAGrave": "www.findagrave.com",
            "TikTok": "www.tiktok.com",
            "Facebook": "www.facebook.com",
        }
        out = self._run(
            'let q = "Richard Speck nurses & Cook County";\n'
            'console.log(JSON.stringify(FIND_SITES.map(s => ({label: s.label, url: s.url(q)}))));'
        )
        sites = {s["label"]: s["url"] for s in json.loads(out)}
        self.assertEqual(set(sites), set(expected_hosts))
        for label, url in sites.items():
            host = urlparse(url).netloc
            self.assertEqual(host, expected_hosts[label], f"{label}: expected host {expected_hosts[label]}, got {host} ({url})")
            # the raw query must not leak through unescaped -- a literal space or "&" in the URL would
            # either break the request or silently drop part of the search
            self.assertNotIn(" ", url)
            self.assertNotIn("nurses &", url)

    def test_instagram_is_deliberately_not_offered(self):
        # Unlike the others, Instagram has no plain query-string search URL to link to at all (its search
        # is a logged-in, JS-driven experience) -- a button would just be a dead link to its login page,
        # not a shortcut to anything. Guards against it being added back without that being reconsidered.
        out = self._run('console.log(JSON.stringify(FIND_SITES.map(s=>s.label)));')
        self.assertNotIn("Instagram", json.loads(out))


if __name__ == "__main__":
    unittest.main()
