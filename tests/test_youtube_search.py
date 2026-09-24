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

    async def test_ignore_errors_flag_is_passed_so_one_blocked_video_does_not_abort_the_whole_search(self):
        seen = {}

        async def runner(args):
            seen["args"] = args
            return 0, "", ""

        await search_youtube("x", 5, runner)
        self.assertIn("--ignore-errors", seen["args"])

    # ---- YouTube's anti-bot sign-in wall (real report: a search returned nothing because ONE result in
    # the batch needed "Sign in to confirm you're not a bot", which -- before --ignore-errors was added --
    # aborted yt-dlp's whole ytsearch run instead of just skipping that one entry) --------------------

    async def test_partial_results_are_kept_even_when_yt_dlp_exits_nonzero_overall(self):
        # One entry in the batch hit the sign-in wall and yt-dlp exited 1 for the run as a whole, but two
        # other entries were already successfully dumped to stdout before that -- those must still come
        # back rather than the whole search failing because of the one blocked video.
        async def runner(args):
            out = _line(id="ok1") + "\n" + _line(id="ok2", title="Second clip")
            err = "ERROR: [youtube] blocked1: Sign in to confirm you’re not a bot. Use --cookies-from-browser..."
            return 1, out, err
        got = await search_youtube("x", 3, runner)
        self.assertEqual({r["id"] for r in got}, {"ok1", "ok2"})

    async def test_sign_in_wall_on_every_result_raises_a_clear_actionable_error(self):
        # Nothing at all could be parsed (every candidate hit the wall) -- this really is an error, but the
        # message should say this is YouTube's own check, not something retrying the same search will fix.
        async def runner(args):
            err = "ERROR: [youtube] yAM3U7OrEaY: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication."
            return 1, "", err
        with self.assertRaises(RuntimeError) as ctx:
            await search_youtube("x", 1, runner)
        msg = str(ctx.exception)
        self.assertIn("sign-in/bot check", msg)
        self.assertIn("Add links", msg)


if __name__ == "__main__":
    unittest.main()
