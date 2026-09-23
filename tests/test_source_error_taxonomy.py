"""Source-error taxonomy (#4: never silently convert a technical error into "no results").

Covers:
  - _parse_retry_after(): the Retry-After header parser (integer-seconds and HTTP-date forms).
  - LoggedHttp._send()/get_json(): raises the specific SourceUnavailable subclass for 429/401/403/
    other-non-200/transport-error/invalid-JSON, instead of one undifferentiated failure.
  - HttpSource.fetch(): each per-query trace note gets an `outcome` that distinguishes a genuine
    zero-match search from "found candidates but couldn't keep any" from a parse/unknown error.
  - SourcingStage.run(): each of the new exception types gets its own `outcome` on the source-level
    trace note (credentials_missing/rate_limited/network_error/parse_error/skipped_other/unknown_error).
"""
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core.models import Asset, Job, ProviderChoice
from pipeline.sources.base import (
    Candidate, CredentialsMissing, HttpSource, LoggedHttp, ProviderError, RateLimited,
    ResponseParseError, SourceContext, SourceUnavailable, _parse_retry_after,
)
from pipeline.stages.base import StageContext
from pipeline.stages.sourcing import SourcingStage

BYTES = b"\xff\xd8\xff-fake-media-"


def make_ctx(d: Path, name: str, handler, retries: int = 0) -> SourceContext:
    src = d / "sources" / name
    (src / "files").mkdir(parents=True)
    return SourceContext(project_dir=d, dir=src,
                         http=LoggedHttp(src, name, transport=httpx.MockTransport(handler), backoff=0, retries=retries))


class ParseRetryAfterTests(unittest.TestCase):
    def test_integer_seconds(self):
        self.assertEqual(_parse_retry_after("120"), 120.0)
        self.assertEqual(_parse_retry_after("0"), 0.0)

    def test_missing_or_blank_is_none(self):
        self.assertIsNone(_parse_retry_after(None))
        self.assertIsNone(_parse_retry_after(""))
        self.assertIsNone(_parse_retry_after("   "))

    def test_garbage_is_none_not_an_error(self):
        self.assertIsNone(_parse_retry_after("not-a-number-or-date"))

    def test_negative_seconds_is_none(self):
        self.assertIsNone(_parse_retry_after("-5"))

    def test_http_date_form(self):
        import datetime
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=90)
        header = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
        seconds = _parse_retry_after(header)
        self.assertIsNotNone(seconds)
        self.assertTrue(60 <= seconds <= 120)  # allow a little test-run slack


class LoggedHttpErrorTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_429_raises_rate_limited_with_retry_after(self):
        def h(req):
            return httpx.Response(429, headers={"retry-after": "30"})
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "x", h)
            with self.assertRaises(RateLimited) as cm:
                await ctx.http.get_json("https://x/y")
            self.assertEqual(cm.exception.retry_after, 30.0)

    async def test_401_raises_credentials_missing(self):
        def h(req):
            return httpx.Response(401)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "x", h)
            with self.assertRaises(CredentialsMissing):
                await ctx.http.get_json("https://x/y")

    async def test_403_raises_credentials_missing(self):
        def h(req):
            return httpx.Response(403)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "x", h)
            with self.assertRaises(CredentialsMissing):
                await ctx.http.get_json("https://x/y")

    async def test_500_raises_provider_error(self):
        def h(req):
            return httpx.Response(500)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "x", h)
            with self.assertRaises(ProviderError):
                await ctx.http.get_json("https://x/y")

    async def test_invalid_json_raises_response_parse_error(self):
        def h(req):
            return httpx.Response(200, content=b"not json{{{")
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "x", h)
            with self.assertRaises(ResponseParseError):
                await ctx.http.get_json("https://x/y")

    async def test_transport_error_raises_provider_error(self):
        def h(req):
            raise httpx.ConnectError("boom", request=req)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "x", h)
            with self.assertRaises(ProviderError):
                await ctx.http.get_json("https://x/y")

    async def test_all_new_types_are_still_source_unavailable(self):
        # Backward compatibility: existing `except SourceUnavailable` handling must keep working.
        self.assertTrue(issubclass(CredentialsMissing, SourceUnavailable))
        self.assertTrue(issubclass(RateLimited, SourceUnavailable))
        self.assertTrue(issubclass(ProviderError, SourceUnavailable))
        self.assertTrue(issubclass(ResponseParseError, SourceUnavailable))


class _Source(HttpSource):
    name = "fake"
    label = "Fake"

    def __init__(self, cands=None, raise_exc=None, **kw):
        super().__init__(**kw)
        self._cands = cands or []
        self._raise = raise_exc

    async def search(self, query, ctx):
        if self._raise:
            raise self._raise
        return self._cands


class FetchOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ok_kept_when_a_candidate_is_downloaded(self):
        def h(req):
            return httpx.Response(200, content=BYTES + b"1")
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "fake", h)
            src = _Source(cands=[Candidate(url="https://x/1.jpg", mime="image/jpeg", title="a")])
            res = await src.fetch(["q"], ctx)
            self.assertEqual(res.trace[0]["outcome"], "ok_kept")
            self.assertEqual(len(res.assets), 1)

    async def test_ok_zero_matches_when_search_finds_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "fake", lambda r: httpx.Response(200))
            src = _Source(cands=[])
            res = await src.fetch(["q"], ctx)
            self.assertEqual(res.trace[0]["outcome"], "ok_zero_matches")

    async def test_ok_no_downloadable_when_candidates_found_but_none_kept(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "fake", lambda r: httpx.Response(200))
            # Unsupported format -> skipped, never downloaded, but the search DID find something.
            src = _Source(cands=[Candidate(url="https://x/1.bmp", mime="image/bmp", title="a")])
            res = await src.fetch(["q"], ctx)
            self.assertEqual(res.trace[0]["found"], 1)
            self.assertEqual(res.trace[0]["kept"], 0)
            self.assertEqual(res.trace[0]["outcome"], "ok_no_downloadable")

    async def test_parse_error_outcome_for_keyerror_typeerror_etc(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "fake", lambda r: httpx.Response(200))
            src = _Source(raise_exc=KeyError("missing_field"))
            res = await src.fetch(["q"], ctx)
            self.assertEqual(res.trace[0]["outcome"], "parse_error")
            self.assertIn("KeyError", res.trace[0]["error"])

    async def test_unknown_error_outcome_for_other_exceptions(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "fake", lambda r: httpx.Response(200))
            src = _Source(raise_exc=RuntimeError("something else"))
            res = await src.fetch(["q"], ctx)
            self.assertEqual(res.trace[0]["outcome"], "unknown_error")

    async def test_source_unavailable_still_propagates_uncaught(self):
        # A SourceUnavailable (or subclass) from search() must still abort this source entirely --
        # it's not a per-query outcome, it's "this whole source can't run right now".
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "fake", lambda r: httpx.Response(200))
            src = _Source(raise_exc=CredentialsMissing("no key"))
            with self.assertRaises(CredentialsMissing):
                await src.fetch(["q"], ctx)


def _job(sources):
    j = Job(subject="test case", providers=ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=sources))
    from pipeline.core.models import Keyword
    j.keywords = [Keyword(term="kw1", approved=True)]
    return j


class SourcingStageOutcomeTests(unittest.IsolatedAsyncioTestCase):
    # Paired with a second, always-successful source so the failing source's own outcome can be
    # inspected without tripping SourcingStage's "nothing could be sourced at all" guard, which is
    # a separate concern (every source failed) from what's under test here (which outcome one
    # particular failure gets).
    async def _run_with_adapter(self, adapter):
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            job = _job(["fake", "ok"])
            stage = SourcingStage({"fake": adapter, "ok": _RaisingAdapter(None)})
            ctx = StageContext(assets_dir=project_dir, project_dir=project_dir)
            res = await stage.run(job, ctx)
            return next(t for t in res.trace if t.get("source") == "fake")

    async def test_credentials_missing_outcome(self):
        note = await self._run_with_adapter(_RaisingAdapter(CredentialsMissing("no key")))
        self.assertEqual(note["outcome"], "credentials_missing")

    async def test_rate_limited_outcome_carries_retry_after(self):
        note = await self._run_with_adapter(_RaisingAdapter(RateLimited("slow down", retry_after=42.0)))
        self.assertEqual(note["outcome"], "rate_limited")
        self.assertEqual(note["retry_after"], 42.0)

    async def test_provider_error_outcome(self):
        note = await self._run_with_adapter(_RaisingAdapter(ProviderError("network broke")))
        self.assertEqual(note["outcome"], "network_error")

    async def test_response_parse_error_outcome(self):
        note = await self._run_with_adapter(_RaisingAdapter(ResponseParseError("bad shape")))
        self.assertEqual(note["outcome"], "parse_error")

    async def test_bare_source_unavailable_outcome(self):
        note = await self._run_with_adapter(_RaisingAdapter(SourceUnavailable("dunno")))
        self.assertEqual(note["outcome"], "skipped_other")

    async def test_unexpected_exception_outcome(self):
        note = await self._run_with_adapter(_RaisingAdapter(ValueError("oops")))
        self.assertEqual(note["outcome"], "unknown_error")


class _RaisingAdapter:
    name = "fake"
    label = "Fake"

    def __init__(self, exc):
        self._exc = exc

    async def fetch(self, queries, ctx):
        if self._exc is None:
            from pipeline.sources.base import SourceResult
            return SourceResult(assets=[Asset(source="ok", path="/x", title="t", source_url="https://x/ok")])
        raise self._exc


if __name__ == "__main__":
    unittest.main()
