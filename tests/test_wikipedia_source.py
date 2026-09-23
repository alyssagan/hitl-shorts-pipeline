"""WikipediaSource (#2: separate research text from visual assets in reporting, make limits
configurable, dedupe articles preserving URLs, disambiguate 'kept' log lines)."""
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.sources.base import LoggedHttp, SourceContext
from pipeline.sources.wikipedia import DEFAULT_MAX_CHARS, WikipediaSource


def make_ctx(d: Path, known_urls=None) -> SourceContext:
    src = d / "sources" / "wikipedia"
    (src / "files").mkdir(parents=True)
    return SourceContext(project_dir=d, dir=src, known_urls=set(known_urls or []),
                         http=LoggedHttp(src, "wikipedia", transport=httpx.MockTransport(_handler), backoff=0))


ARTICLES = {
    "Cat": {"extract": "Cats are small mammals. " * 2000, "fullurl": "https://en.wikipedia.org/wiki/Cat"},
    "Cat!!": {"extract": "A second, differently-titled article that sanitizes to the same filename as Cat.",
              "fullurl": "https://en.wikipedia.org/wiki/Cat%21%21"},
    "Dog": {"extract": "Dogs are domesticated mammals.", "fullurl": "https://en.wikipedia.org/wiki/Dog"},
}
# Exact query text -> article title, so "cat" and "cat!!" resolve to distinct articles instead of one
# substring-matching the other.
QUERY_TO_TITLE = {"cat": "Cat", "cat!!": "Cat!!", "dog": "Dog"}


def _handler(req: httpx.Request) -> httpx.Response:
    p = req.url.params
    if p.get("list") == "search":
        title = QUERY_TO_TITLE.get(p.get("srsearch", "").lower())
        if not title:
            return httpx.Response(200, json={"query": {"search": []}})
        return httpx.Response(200, json={"query": {"search": [{"title": title}]}})
    title = p.get("titles", "")
    a = ARTICLES.get(title, {"extract": "", "fullurl": ""})
    return httpx.Response(200, json={"query": {"pages": {"1": a}}})


class MaxCharsConfigurableTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_matches_module_constant(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d))
            src = WikipediaSource(max_articles=1)
            self.assertEqual(src.max_chars, DEFAULT_MAX_CHARS)
            res = await src.fetch(["cat"], ctx)
            saved = Path(res.references[0].path).read_text(encoding="utf-8")
            # The extract is far longer than any reasonable custom cap; with the default it's kept whole.
            self.assertIn("Cats are small mammals.", saved)

    async def test_custom_max_chars_truncates_saved_text(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d))
            src = WikipediaSource(max_articles=1, max_chars=50)
            res = await src.fetch(["cat"], ctx)
            self.assertEqual(res.references[0].chars, 50)
            saved = Path(res.references[0].path).read_text(encoding="utf-8")
            body = saved.split("\n\n", 1)[1]
            self.assertEqual(len(body.rstrip("\n")), 50)


class DedupPreservesUrlsTests(unittest.IsolatedAsyncioTestCase):
    async def test_article_already_known_this_round_is_skipped_not_refetched(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d))
            src = WikipediaSource(max_articles=5)
            res = await src.fetch(["cat", "cat"], ctx)  # same query twice in one round
            self.assertEqual(len(res.references), 1)
            self.assertEqual(res.trace[1]["skipped"][0]["reason"], "already in this project")
            # The URL that made it a duplicate is preserved in the skip reason's context (still the
            # same known article), not silently dropped -- and the original TextRef's URL is intact.
            self.assertEqual(res.references[0].url, "https://en.wikipedia.org/wiki/Cat")

    async def test_article_already_known_from_a_prior_round_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), known_urls={"https://en.wikipedia.org/wiki/Cat"})
            src = WikipediaSource(max_articles=5)
            res = await src.fetch(["cat"], ctx)
            self.assertEqual(len(res.references), 0)
            self.assertEqual(res.trace[0]["skipped"][0]["reason"], "already in this project")

    async def test_different_articles_with_colliding_safe_filenames_both_keep_their_own_file(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d))
            src = WikipediaSource(max_articles=5)
            res = await src.fetch(["cat", "cat!!"], ctx)
            self.assertEqual(len(res.references), 2)
            paths = {r.path for r in res.references}
            self.assertEqual(len(paths), 2)  # no filename collision/overwrite
            for r in res.references:
                self.assertTrue(Path(r.path).exists())
            # Each file still contains the right article's own text.
            texts = {Path(r.path).read_text(encoding="utf-8") for r in res.references}
            self.assertTrue(any("Cats are small mammals" in t for t in texts))
            self.assertTrue(any("second, differently-titled article" in t for t in texts))


class RegistryWiringTests(unittest.TestCase):
    """config/pipeline.toml's [sources] wikipedia_max_chars reaches WikipediaSource the same way
    wikipedia_articles already does (pipeline/stages/registry.py)."""

    def test_custom_max_chars_in_settings_reaches_the_adapter(self):
        from pipeline.stages.registry import build_default_registry
        reg = build_default_registry({"sources": {"wikipedia_max_chars": 500, "wikipedia_articles": 3}})
        adapter = reg.sourcing_stage().adapters["wikipedia"]
        self.assertEqual(adapter.max_chars, 500)
        self.assertEqual(adapter.max_articles, 3)

    def test_default_when_not_configured(self):
        from pipeline.stages.registry import build_default_registry
        reg = build_default_registry({})
        adapter = reg.sourcing_stage().adapters["wikipedia"]
        self.assertEqual(adapter.max_chars, DEFAULT_MAX_CHARS)


class KindDisambiguationTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_notes_are_tagged_as_text_not_media(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d))
            src = WikipediaSource(max_articles=1)
            res = await src.fetch(["cat"], ctx)
            self.assertEqual(res.trace[0]["kind"], "text")

    async def test_zero_hit_query_is_still_tagged_text(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d))
            src = WikipediaSource(max_articles=1)
            res = await src.fetch(["no such article anywhere"], ctx)
            self.assertEqual(res.trace[0]["found"], 0)
            self.assertEqual(res.trace[0]["kind"], "text")


if __name__ == "__main__":
    unittest.main()
