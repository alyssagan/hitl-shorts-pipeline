"""pipeline/sources/youtube_search.py: Gate 2's "Find more" YouTube auto-search, requested directly as the
one real gap in that panel (Internet Archive/Chronicling America/Commons are already automated sources;
Google Images/FindAGrave have no API or rights metadata worth automating). Metadata only via yt-dlp's own
`ytsearchN:` syntax -- no paid key, nothing downloaded here. Same fake-runner test style as
UrlListTests in test_more_sources.py, since this hits the same yt-dlp subprocess boundary."""
from __future__ import annotations

import json
import unittest

from pipeline.sources.base import SourceUnavailable
from pipeline.sources.youtube_search import search_youtube


def _line(**kw) -> str:
    base = {"id": "abc123", "title": "A period newsreel", "webpage_url": "https://www.youtube.com/watch?v=abc123",
            "uploader": "Archive Channel", "duration": 95, "thumbnail": "https://i.ytimg.com/vi/abc123/hq.jpg",
            "upload_date": "20240101", "description": "Some description " * 30}
    base.update(kw)
    return json.dumps(base)


class SearchYoutubeTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_one_candidate_per_json_line(self):
        async def runner(args):
            return 0, _line() + "\n" + _line(id="xyz789", title="A different clip", webpage_url="https://www.youtube.com/watch?v=xyz789"), ""
        got = await search_youtube("richard speck 1966", 5, runner)
        self.assertEqual(len(got), 2)
        first = got[0]
        self.assertEqual(first["id"], "abc123")
        self.assertEqual(first["title"], "A period newsreel")
        self.assertEqual(first["url"], "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(first["uploader"], "Archive Channel")
        self.assertEqual(first["duration"], 95)
        self.assertEqual(first["thumbnail"], "https://i.ytimg.com/vi/abc123/hq.jpg")
        self.assertLessEqual(len(first["description"]), 300)
        self.assertEqual(got[1]["id"], "xyz789")

    async def test_missing_fields_fall_back_sensibly_rather_than_crashing(self):
        async def runner(args):
            return 0, json.dumps({"id": "onlyid"}), ""
        got = await search_youtube("x", 1, runner)
        self.assertEqual(got, [{"id": "onlyid", "title": "(untitled)", "url": "https://www.youtube.com/watch?v=onlyid",
                                "uploader": "", "duration": None, "thumbnail": "", "upload_date": "", "description": ""}])

    async def test_channel_used_when_uploader_is_missing(self):
        async def runner(args):
            return 0, json.dumps({"id": "a", "channel": "Some Channel"}), ""
        got = await search_youtube("x", 1, runner)
        self.assertEqual(got[0]["uploader"], "Some Channel")

    async def test_blank_lines_and_unparseable_json_are_skipped_not_fatal(self):
        async def runner(args):
            return 0, "\n" + _line(id="ok") + "\n   \nnot json at all\n", ""
        got = await search_youtube("x", 3, runner)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["id"], "ok")

    async def test_no_results_is_an_empty_list_not_an_error(self):
        async def runner(args):
            return 0, "", ""
        got = await search_youtube("something with zero hits", 5, runner)
        self.assertEqual(got, [])

    async def test_missing_yt_dlp_raises_source_unavailable(self):
        async def runner(args):
            return 1, "", "ModuleNotFoundError: No module named yt_dlp"
        with self.assertRaises(SourceUnavailable):
            await search_youtube("x", 1, runner)

    async def test_other_failure_raises_runtime_error_with_the_actual_message(self):
        async def runner(args):
            return 1, "", "line one\nERROR: network is unreachable"
        with self.assertRaises(RuntimeError) as ctx:
            await search_youtube("x", 1, runner)
        self.assertIn("network is unreachable", str(ctx.exception))

    async def test_query_and_count_are_passed_through_to_yt_dlp(self):
        seen = {}

        async def runner(args):
            seen["args"] = args
            return 0, "", ""

        await search_youtube("cook county nurses 1966", 7, runner)
        args = seen["args"]
        self.assertIn("--skip-download", args)
        self.assertIn("--dump-json", args)
        self.assertIn("7", args[args.index("--playlist-end") + 1:args.index("--playlist-end") + 2])
        self.assertEqual(args[-1], "ytsearch7:cook county nurses 1966")


if __name__ == "__main__":
    unittest.main()
