"""The extra sources (Pixabay, NASA, Internet Archive, Library of Congress, Smithsonian, Unsplash,
URL list), offline with httpx MockTransport. Response shapes follow each service's public docs."""
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core.models import Asset, Scene
from pipeline.sources.base import LoggedHttp, SourceContext, SourceUnavailable, cc_name, redact_url
from pipeline.sources.internet_archive import InternetArchiveSource
from pipeline.sources.loc import LibraryOfCongressSource
from pipeline.sources.nasa import NasaSource, pick_file
from pipeline.sources.pixabay import PixabaySource
from pipeline.sources.smithsonian import SmithsonianSource
from pipeline.sources.unsplash import UnsplashSource
from pipeline.sources.urls import UrlListSource, parse_url_lines
from pipeline.stages.scenes.clips import AssetClipSource
from pipeline.vetting.rules import vet_asset

BYTES = b"\xff\xd8\xff-fake-media-"


def make_ctx(d: Path, name: str, handler, settings=None) -> SourceContext:
    src = d / "sources" / name
    (src / "files").mkdir(parents=True)
    return SourceContext(project_dir=d, dir=src, settings=settings or {},
                         http=LoggedHttp(src, name, transport=httpx.MockTransport(handler), backoff=0))


def log_lines(ctx: SourceContext) -> list[dict]:
    return [json.loads(x) for x in ctx.http.log_path.read_text().splitlines()]


def risk(a: Asset) -> str:
    return vet_asset(a, [a]).risk


def rules(a: Asset) -> set[str]:
    return {f.rule for f in vet_asset(a, [a]).flags}


class HelperTests(unittest.TestCase):
    def test_keys_are_hidden_in_urls(self):
        self.assertEqual(redact_url("https://x/api?key=SECRET123&q=cat"), "https://x/api?key=***&q=cat")
        self.assertNotIn("SECRET", redact_url("https://x/?a=1&api_key=SECRET&b=2"))
        # Europeana's key param is "wskey", not "key" -- a plain "key=" pattern wouldn't catch it since
        # the [?&] anchor requires the whole param name to match, not just a trailing substring of it.
        self.assertNotIn("SECRET", redact_url("https://api.europeana.eu/record/v2/search.json?wskey=SECRET&query=x"))

    def test_cc_names(self):
        self.assertEqual(cc_name("https://creativecommons.org/licenses/by-sa/4.0/"), "CC BY-SA 4.0")
        self.assertEqual(cc_name("https://creativecommons.org/publicdomain/zero/1.0/"), "CC0 1.0")
        self.assertEqual(cc_name("https://creativecommons.org/publicdomain/mark/1.0/"), "Public Domain Mark 1.0")
        self.assertEqual(cc_name("https://example.com"), "")


class PixabayTests(unittest.IsolatedAsyncioTestCase):
    async def test_photos_and_videos_and_key_never_logged(self):
        def h(req):
            if req.url.host == "pixabay.com" and req.url.path == "/api/":
                return httpx.Response(200, json={"hits": [{"id": 1, "tags": "octopus, sea", "user": "Ann", "pageURL": "https://pixabay.com/p/1",
                                                            "largeImageURL": "https://cdn.pixabay.com/1.jpg", "imageWidth": 1280, "imageHeight": 1920}]})
            if req.url.path == "/api/videos/":
                return httpx.Response(200, json={"hits": [{"id": 2, "tags": "octopus swim", "user": "Bo", "pageURL": "https://pixabay.com/v/2", "duration": 12,
                                                            "videos": {"large": {"url": "https://cdn.pixabay.com/l.mp4", "width": 1920, "height": 1080},
                                                                       "medium": {"url": "https://cdn.pixabay.com/m.mp4", "width": 1280, "height": 720},
                                                                       "tiny": {"url": "https://cdn.pixabay.com/t.mp4", "width": 480, "height": 270}}}]})
            return httpx.Response(200, content=BYTES + str(req.url.path).encode())
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "pixabay", h)
            res = await PixabaySource(api_key="TOPSECRET").fetch(["octopus"], ctx)
            self.assertEqual({a.kind for a in res.assets}, {"image", "video"})
            vid = next(a for a in res.assets if a.kind == "video")
            self.assertTrue(vid.source_url.endswith("m.mp4"))          # smallest that is at least 720 wide
            self.assertEqual(vid.duration, 12)
            self.assertEqual(vid.license, "Pixabay Content License")
            self.assertNotIn("TOPSECRET", (ctx.dir / "requests.jsonl").read_text())
            self.assertEqual(risk(vid), "low")
            self.assertIn("LIC_PIXABAY", rules(vid))

    async def test_no_key_means_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "pixabay", lambda r: httpx.Response(404))
            with self.assertRaises(SourceUnavailable):
                await PixabaySource(api_key="").fetch(["x"], ctx)


class NasaTests(unittest.IsolatedAsyncioTestCase):
    def test_pick_file(self):
        hrefs = ["http://x/a~orig.jpg", "http://x/a~large.jpg", "http://x/a~medium.jpg", "http://x/metadata.json"]
        self.assertTrue(pick_file(hrefs, "image").endswith("~large.jpg"))
        vids = ["http://x/v~orig.mp4", "http://x/v~medium.mp4", "http://x/v~small.mp4"]
        self.assertTrue(pick_file(vids, "video").endswith("~medium.mp4"))

    async def test_search_manifest_download(self):
        def h(req):
            if req.url.path == "/search":
                return httpx.Response(200, json={"collection": {"items": [{"data": [{
                    "nasa_id": "PIA1", "title": "Octopus nebula", "description": "<b>Nice</b> view", "center": "JPL", "media_type": "image",
                    "photographer": "NASA/JPL"}]}]}})
            if req.url.path.startswith("/asset/"):
                return httpx.Response(200, json={"collection": {"items": [{"href": "http://images-assets.nasa.gov/image/PIA1/PIA1~large.jpg"}, {"href": "http://images-assets.nasa.gov/image/PIA1/PIA1~small.jpg"}]}})
            return httpx.Response(200, content=BYTES)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "nasa", h)
            res = await NasaSource().fetch(["nebula"], ctx)
            a = res.assets[0]
            self.assertEqual(a.page_url, "https://images.nasa.gov/details/PIA1")
            self.assertIn("NASA", a.attribution)
            self.assertEqual(a.description, "Nice view")
            self.assertIn("NASA_NOTE", rules(a))
            self.assertIn("LIC_PD", rules(a))


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    def _handler(self):
        def h(req):
            if req.url.path == "/advancedsearch.php":
                return httpx.Response(200, json={"response": {"docs": [
                    {"identifier": "licensed", "title": "Old film", "mediatype": "movies", "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"},
                    {"identifier": "nolicense", "title": "Random upload", "mediatype": "movies"}]}})
            if req.url.path == "/metadata/licensed":
                return httpx.Response(200, json={"metadata": {"title": "Old film", "creator": "Prelinger", "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"},
                                                 "files": [{"name": "old.mp4", "size": "5000000", "format": "h.264"},
                                                           {"name": "old.ogv", "size": "9000000"},
                                                           {"name": "huge.mp4", "size": "900000000"}]})
            if req.url.path == "/metadata/nolicense":
                return httpx.Response(200, json={"metadata": {"title": "Random upload", "creator": "Someone"},
                                                 "files": [{"name": "random.mp4", "size": "4000000"}]})
            return httpx.Response(200, content=BYTES + str(req.url.path).encode())
        return h

    async def test_by_default_unlicensed_items_are_kept_and_flagged_not_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "archive", self._handler())
            res = await InternetArchiveSource().fetch(["film"], ctx)
            self.assertEqual(len(res.assets), 2)
            licensed = next(a for a in res.assets if a.source_url.endswith("/licensed/old.mp4"))
            self.assertEqual(licensed.license, "Public Domain Mark 1.0")
            self.assertEqual(risk(licensed), "low")
            unlicensed = next(a for a in res.assets if a.source_url.endswith("/nolicense/random.mp4"))
            self.assertEqual(unlicensed.license, "")
            self.assertIn("check the item page before use", unlicensed.description)
            self.assertIn("LIC_UNKNOWN", rules(unlicensed))
            self.assertEqual(risk(unlicensed), "high")

    async def test_require_license_true_still_drops_unlicensed_items(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "archive", self._handler())
            res = await InternetArchiveSource(require_license=True).fetch(["film"], ctx)
            self.assertEqual(len(res.assets), 1)
            a = res.assets[0]
            self.assertTrue(a.source_url.endswith("/licensed/old.mp4"))
            self.assertEqual(a.license, "Public Domain Mark 1.0")
            self.assertEqual(risk(a), "low")


class LocTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_domain_only_when_rights_say_so(self):
        def h(req):
            if req.url.host == "www.loc.gov":
                return httpx.Response(200, json={"results": [
                    {"title": "Harbor 1900", "url": "https://www.loc.gov/item/1/", "image_url": ["https://tile.loc.gov/a.gif", "https://tile.loc.gov/a_s.jpg#h=100", "https://tile.loc.gov/a_l.jpg#h=900"],
                     "rights_advisory": ["No known restrictions on publication."], "contributor": ["Detroit Publishing Co."]},
                    {"title": "Portrait", "url": "https://www.loc.gov/item/2/", "image_url": ["https://tile.loc.gov/b.jpg"], "rights_advisory": ["Rights status not evaluated."]}]})
            return httpx.Response(200, content=BYTES + str(req.url).encode())
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "loc", h)
            res = await LibraryOfCongressSource().fetch(["harbor"], ctx)
            pd = next(a for a in res.assets if a.title == "Harbor 1900")
            unk = next(a for a in res.assets if a.title == "Portrait")
            self.assertTrue(pd.source_url.endswith("a_l.jpg"))
            self.assertIn("Public domain", pd.license)
            self.assertEqual(unk.license, "")
            self.assertEqual(risk(unk), "high")
            self.assertIn("Rights status not evaluated", unk.description)


class SmithsonianTests(unittest.IsolatedAsyncioTestCase):
    async def test_cc0_gets_a_license_and_key_is_hidden(self):
        def h(req):
            if req.url.host == "api.si.edu":
                return httpx.Response(200, json={"response": {"rows": [{"id": "x1", "content": {"descriptiveNonRepeating": {
                    "title": {"content": "Octopus model"}, "data_source": "National Museum of Natural History", "record_link": "https://n2t.net/ark:/1",
                    "online_media": {"media": [{"type": "Images", "content": "https://ids.si.edu/ids/deliveryService?id=1", "usage": {"access": "CC0"}}]}}}}]}})
            return httpx.Response(200, content=BYTES)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "smithsonian", h)
            res = await SmithsonianSource(api_key="SIKEY").fetch(["octopus"], ctx)
            self.assertEqual(res.assets[0].license, "CC0 1.0")
            self.assertNotIn("SIKEY", ctx.http.log_path.read_text())


class UnsplashTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_auth_and_download_ping(self):
        seen = []

        def h(req):
            seen.append((req.url.host, req.url.path, req.headers.get("authorization")))
            if req.url.path == "/search/photos":
                return httpx.Response(200, json={"results": [{"id": "u1", "alt_description": "an octopus", "width": 4000, "height": 6000,
                                                               "urls": {"regular": "https://images.unsplash.com/u1"}, "user": {"name": "Cy"},
                                                               "links": {"html": "https://unsplash.com/photos/u1", "download_location": "https://api.unsplash.com/photos/u1/download"}}]})
            if req.url.path.endswith("/download"):
                return httpx.Response(200, json={"url": "https://images.unsplash.com/u1"})
            return httpx.Response(200, content=BYTES)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "unsplash", h)
            res = await UnsplashSource(api_key="UKEY").fetch(["octopus"], ctx)
            self.assertEqual(len(res.assets), 1)
            self.assertIn(("api.unsplash.com", "/photos/u1/download", "Client-ID UKEY"), seen)
            self.assertIn("LIC_UNSPLASH", rules(res.assets[0]))


class UrlListTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_lines(self):
        got = parse_url_lines("# comment\nhttps://a.com/v | great intro | 1\n\nhttps://b.com/x.mp4\n")
        self.assertEqual(got, [{"url": "https://a.com/v", "note": "great intro", "position": "1"},
                               {"url": "https://b.com/x.mp4", "note": "", "position": ""}])

    async def test_no_urls_is_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "urls", lambda r: httpx.Response(404))
            with self.assertRaises(SourceUnavailable):
                await UrlListSource().fetch(["x"], ctx)

    async def test_direct_file_and_platform_video(self):
        calls = []

        async def runner(args):
            calls.append(args)
            out = args[args.index("-o") + 1].replace("%(ext)s", "mp4")
            Path(out).write_bytes(b"video-bytes")
            Path(out.replace(".mp4", ".info.json")).write_text(json.dumps({
                "title": "Octopus escapes tank", "uploader": "SeaFan", "duration": 42, "webpage_url": "https://www.youtube.com/watch?v=abc",
                "width": 1080, "height": 1920, "upload_date": "20240101", "extractor_key": "Youtube",
                "license": "Creative Commons Attribution license (reuse allowed)"}))
            return 0, "", ""

        def h(req):
            return httpx.Response(200, content=BYTES + b"direct")
        with tempfile.TemporaryDirectory() as d:
            opts = {"job_options": {"urls": [
                {"url": "https://files.example.com/clip.mp4", "note": "b-roll", "position": "2"},
                {"url": "https://www.youtube.com/watch?v=abc", "note": "the escape", "position": "intro"},
                "ftp://nope/x.mp4"]}}
            ctx = make_ctx(Path(d), "urls", h, opts)
            res = await UrlListSource(runner=runner).fetch([], ctx)
            self.assertEqual(len(res.assets), 2)
            direct = next(a for a in res.assets if a.source_url.startswith("https://files"))
            yt = next(a for a in res.assets if "youtube" in a.source_url)
            self.assertEqual(direct.meta["position_hint"], "2")
            self.assertEqual(direct.meta["url_list_note"], "b-roll")
            self.assertEqual(direct.license, "")
            self.assertEqual(risk(direct), "high")                      # no license
            self.assertEqual((yt.author, yt.duration, yt.height), ("SeaFan", 42, 1920))
            self.assertIn("PLATFORM_SOURCE", rules(yt))
            self.assertEqual(risk(yt), "high")                          # platform video
            self.assertTrue((ctx.dir / "info").exists())
            self.assertEqual(len(calls), 1)                             # only the platform link needed yt-dlp
            self.assertTrue(any("ftp://" in str(s.get("url")) for s in res.trace[0]["skipped"]))
            self.assertTrue(any(e["method"] == "yt-dlp" for e in log_lines(ctx)))

    async def test_failed_download_is_reported_not_fatal(self):
        async def runner(args):
            return 1, "", "ERROR: Video unavailable"
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "urls", lambda r: httpx.Response(404), {"job_options": {"urls": ["https://www.tiktok.com/@a/video/1"]}})
            res = await UrlListSource(runner=runner).fetch([], ctx)
            self.assertEqual(res.assets, [])
            self.assertIn("Video unavailable", res.trace[0]["skipped"][0]["reason"])

    async def test_fetch_one_marks_manual_url_import_and_carries_a_position_hint(self):
        # fetch_one is the synchronous single-URL path Gate 2's "Add links" (#9) and Gate 3's "drop a link on
        # a scene" both use, outside the normal batch fetch() a sourcing round runs -- every asset it returns
        # was a human pasting a link, never a keyword search, so import_method should say so either way.
        async def runner(args):
            out = args[args.index("-o") + 1].replace("%(ext)s", "mp4")
            Path(out).write_bytes(b"video-bytes")
            return 0, "", ""
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "urls", lambda r: httpx.Response(200, content=BYTES))
            direct = await UrlListSource().fetch_one("https://files.example.com/clip.mp4", "b-roll", 1, ctx, position="3")
            self.assertEqual(direct.import_method, "manual_url")
            self.assertEqual(direct.meta["position_hint"], "3")
            yt = await UrlListSource(runner=runner).fetch_one("https://www.youtube.com/watch?v=abc", "the escape", 2, ctx)
            self.assertEqual(yt.import_method, "manual_url")
            self.assertEqual(yt.meta["position_hint"], "")     # optional -- Gate 3's caller never passes one

    async def test_ytdlp_error_gets_a_friendlier_hint_when_it_is_not_a_media_page(self):
        # Requirement #9: distinguish a direct-media link from one that's just a webpage. yt-dlp's own error
        # text already says this (it just says it cryptically) -- the fix surfaces that distinction rather
        # than inventing a new one, so it stays honest about what actually failed.
        async def runner(args):
            return 1, "", "ERROR: Unsupported URL: https://example.com/article"
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "urls", lambda r: httpx.Response(404))
            with self.assertRaises(RuntimeError) as cm:
                await UrlListSource(runner=runner).fetch_one("https://example.com/article", "", 1, ctx)
            self.assertIn("doesn't look like a direct video/photo link", str(cm.exception))
            self.assertIn("Unsupported URL", str(cm.exception))    # original yt-dlp text kept, not replaced

    async def test_ytdlp_error_keeps_the_original_message_for_other_failures(self):
        async def runner(args):
            return 1, "", "ERROR: Video unavailable"
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "urls", lambda r: httpx.Response(404))
            with self.assertRaises(RuntimeError) as cm:
                await UrlListSource(runner=runner).fetch_one("https://www.youtube.com/watch?v=dead", "", 1, ctx)
            msg = str(cm.exception)
            self.assertIn("yt-dlp could not download this link", msg)
            self.assertNotIn("doesn't look like", msg)


class PhotosDoNotCrowdOutVideosTests(unittest.IsolatedAsyncioTestCase):
    async def test_videos_are_kept_even_when_enough_photos_exist(self):
        def h(req):
            if req.url.host == "pixabay.com" and req.url.path == "/api/":
                return httpx.Response(200, json={"hits": [
                    {"id": i, "tags": f"octopus {i}", "user": "A", "pageURL": f"https://pixabay.com/p/{i}",
                     "largeImageURL": f"https://cdn.pixabay.com/{i}.jpg", "imageWidth": 1280, "imageHeight": 1920} for i in range(1, 8)]})
            if req.url.path == "/api/videos/":
                return httpx.Response(200, json={"hits": [
                    {"id": 100 + i, "tags": f"octopus video {i}", "user": "B", "pageURL": f"https://pixabay.com/v/{i}", "duration": 9,
                     "videos": {"medium": {"url": f"https://cdn.pixabay.com/v{i}.mp4", "width": 1280, "height": 720}}} for i in range(1, 5)]})
            return httpx.Response(200, content=BYTES + str(req.url).encode())
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "pixabay", h)
            res = await PixabaySource(per_query=4, videos_per_query=2, api_key="k").fetch(["octopus"], ctx)
            self.assertEqual(sum(a.kind == "image" for a in res.assets), 4)
            self.assertEqual(sum(a.kind == "video" for a in res.assets), 2)
            self.assertEqual((res.trace[0]["kept_photos"], res.trace[0]["kept_videos"]), (4, 2))

    async def test_zero_videos_setting_keeps_photos_only(self):
        def h(req):
            if req.url.path == "/api/videos/":
                return httpx.Response(200, json={"hits": [{"id": 1, "tags": "v", "user": "B", "pageURL": "https://pixabay.com/v/1", "duration": 9,
                                                            "videos": {"medium": {"url": "https://cdn.pixabay.com/v.mp4", "width": 1280, "height": 720}}}]})
            if req.url.path == "/api/":
                return httpx.Response(200, json={"hits": [{"id": 1, "tags": "p", "user": "A", "pageURL": "https://pixabay.com/p/1", "largeImageURL": "https://cdn.pixabay.com/1.jpg",
                                                            "imageWidth": 1280, "imageHeight": 1920}]})
            return httpx.Response(200, content=BYTES)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "pixabay", h)
            res = await PixabaySource(per_query=4, videos_per_query=0, api_key="k").fetch(["octopus"], ctx)
            self.assertEqual({a.kind for a in res.assets}, {"image"})


class SearchAgainGivesNewResultsTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_round_asks_for_the_next_page_and_skips_repeats(self):
        pages_asked = []

        def h(req):
            if req.url.path == "/api/":
                page = int(req.url.params.get("page", "1"))
                pages_asked.append(page)
                ids = [1, 2] if page == 1 else [2, 3]            # id 2 comes back again on page 2
                return httpx.Response(200, json={"hits": [
                    {"id": i, "tags": f"octopus {i}", "user": "A", "pageURL": f"https://pixabay.com/p/{i}",
                     "largeImageURL": f"https://cdn.pixabay.com/{i}.jpg", "imageWidth": 1280, "imageHeight": 1920} for i in ids]})
            return httpx.Response(200, content=BYTES + str(req.url).encode())
        with tempfile.TemporaryDirectory() as d:
            src = PixabaySource(per_query=4, videos_per_query=0, api_key="k")
            ctx = make_ctx(Path(d), "pixabay", h)
            first = await src.fetch(["octopus"], ctx)
            self.assertEqual(len(first.assets), 2)
            self.assertEqual(first.trace[0]["page"], 1)
            # Round two: the pipeline reports one earlier search and the URLs already held.
            ctx2 = make_ctx_existing(ctx, {"prior_searches": {"pixabay|octopus": 1}},
                                     known_urls={a.source_url for a in first.assets},
                                     known_hashes={a.sha256 for a in first.assets})
            second = await src.fetch(["octopus"], ctx2)
            self.assertEqual(pages_asked, [1, 2])
            self.assertEqual(second.trace[0]["page"], 2)
            self.assertEqual([a.title for a in second.assets], ["octopus 3"])          # only the new one
            self.assertTrue(any("already in this project" in s["reason"] for s in second.trace[0]["skipped"]))


def make_ctx_existing(ctx: SourceContext, settings: dict, known_urls: set, known_hashes: set) -> SourceContext:
    return SourceContext(project_dir=ctx.project_dir, dir=ctx.dir, http=ctx.http, settings=settings,
                         known_urls=known_urls, known_hashes=known_hashes)


class PositionHintTests(unittest.IsolatedAsyncioTestCase):
    async def test_hints_place_clips(self):
        def asset(name, hint):
            return Asset(source="urls", path=f"/p/{name}", title=name, status="approved", meta={"position_hint": hint}, kind="video")
        assets = [asset("a", "2"), asset("b", "intro"), asset("c", "end"), asset("d", "")]
        src = AssetClipSource(assets)
        src.total_scenes = 4
        picks = {}
        used: set[str] = set()
        for i in range(4):
            p = await src.fetch(Scene(index=i, narration="zzz"), Path("."), used)
            used.add(p)
            picks[i] = p
        self.assertEqual(picks[0], "/p/b")     # intro
        self.assertEqual(picks[1], "/p/a")     # scene number 2
        self.assertEqual(picks[3], "/p/c")     # end = last scene
        self.assertEqual(picks[2], "/p/d")


class ClipMatchingTests(unittest.IsolatedAsyncioTestCase):
    """pipeline/stages/scenes/clips.py: matching a scene's narration to an approved asset, and what
    happens when there are more scenes than approved assets to draw from -- both prompted by "the
    photos don't fit the script" / "the photos just keep running in a circle" feedback."""

    async def test_stopwords_dont_count_as_a_match(self):
        a = Asset(source="x", path="/p/a", title="The Ocean and the Sky", description="", status="approved")
        b = Asset(source="x", path="/p/b", title="Whitechapel Street, 1888", description="", status="approved")
        src = AssetClipSource([a, b])
        scene = Scene(index=0, narration="A look at the street and the case.", search_terms=["whitechapel"])
        pick = await src.fetch(scene, Path("."), set())
        # Every word in "a"'s title is a stopword ("the", "and") except "ocean"/"sky", which share nothing
        # with the scene; "b" shares the real subject words "street"/"whitechapel". Without stopword
        # filtering, "the"/"and" alone would have made "a" look like a match too.
        self.assertEqual(pick, "/p/b")
        self.assertIn("whitechapel", src.last_pick.reason.lower())

    async def test_pool_is_reused_not_dropped_when_scenes_outnumber_approved_assets(self):
        a = Asset(source="x", path="/p/a", title="Whitechapel street", description="", status="approved")
        src = AssetClipSource([a])
        used: set[str] = set()
        first = await src.fetch(Scene(index=0, narration="whitechapel", search_terms=["whitechapel"]), Path("."), used)
        used.add(first)
        self.assertEqual(first, "/p/a")
        self.assertNotIn("REUSED", src.last_pick.reason)
        # A second scene, same (only) approved asset: used to return None here (dropping the scene from
        # what's sent to render), which is what let MoneyPrinterTurbo loop old clips to fill the gap.
        second = await src.fetch(Scene(index=1, narration="whitechapel again", search_terms=["whitechapel"]), Path("."), used)
        self.assertEqual(second, "/p/a")
        self.assertIn("REUSED", src.last_pick.reason)

    async def test_no_approved_assets_at_all_still_returns_none(self):
        src = AssetClipSource([])
        pick = await src.fetch(Scene(index=0, narration="whitechapel"), Path("."), set())
        self.assertIsNone(pick)
        self.assertIsNone(src.last_pick)


if __name__ == "__main__":
    unittest.main()
