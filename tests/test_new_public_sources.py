"""Openverse, DPLA, Chronicling America, Flickr, Europeana -- offline with httpx MockTransport, added
2026-09-23 (docs/RUNNING.md "More sources for real case photos"). Response shapes follow each service's
public docs, except Chronicling America (modeled on loc.py's proven www.loc.gov pattern) and Europeana
(modeled on published docs only) -- see docs/KNOWN_LIMITATIONS.md #13."""
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.sources.base import SourceUnavailable
from pipeline.sources.chronicling_america import ChroniclingAmericaSource, best_image
from pipeline.sources.dpla import DplaSource
from pipeline.sources.europeana import EuropeanaSource
from pipeline.sources.flickr import FlickrSource
from pipeline.sources.openverse import OpenverseSource
from pipeline.vetting.rules import vet_asset

from tests.test_more_sources import BYTES, make_ctx


def risk(a):
    return vet_asset(a, [a]).risk


def rules(a):
    return {f.rule for f in vet_asset(a, [a]).flags}


class OpenverseTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_needed_and_license_from_url(self):
        def h(req):
            if req.url.host == "api.openverse.org":
                return httpx.Response(200, json={"results": [{
                    "id": "ov1", "title": "Whitechapel street 1888", "creator": "Museum of London",
                    "url": "https://example.org/ov1.jpg", "foreign_landing_url": "https://openverse.org/image/ov1",
                    "license": "by", "license_version": "4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "provider": "flickr", "source": "flickr", "filetype": "jpg", "width": 1200, "height": 1600,
                    "tags": [{"name": "street"}, {"name": "1888"}]}]})
            return httpx.Response(200, content=BYTES)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "openverse", h)
            res = await OpenverseSource().fetch(["whitechapel 1888"], ctx)
            self.assertEqual(len(res.assets), 1)
            a = res.assets[0]
            self.assertEqual(a.license, "CC BY 4.0")
            self.assertIn("flickr", a.attribution)
            self.assertEqual(risk(a), "low")
            self.assertIn("LIC_BY", rules(a))

    async def test_cc0_without_url_still_gets_a_label(self):
        def h(req):
            return httpx.Response(200, json={"results": [{
                "id": "ov2", "title": "Old ledger", "creator": "Archive", "url": "https://example.org/ov2.png",
                "license": "cc0", "license_version": "1.0", "license_url": "", "filetype": "png"}]})
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "openverse", h)
            res = await OpenverseSource().fetch(["ledger"], ctx)
            self.assertEqual(res.assets[0].license, "CC0 1.0")
            self.assertEqual(res.assets[0].mime, "image/png")


class DplaTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_is_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "dpla", lambda r: httpx.Response(404))
            with self.assertRaises(SourceUnavailable):
                await DplaSource(api_key="").fetch(["x"], ctx)

    async def test_recognized_rights_become_a_license_others_stay_unknown(self):
        def h(req):
            if req.url.host == "api.dp.la":
                return httpx.Response(200, json={"docs": [
                    {"id": "d1", "isShownAt": "https://example.org/1", "object": "https://example.org/1.jpg",
                     "dataProvider": "Chicago History Museum", "provider": {"name": "DPLA"},
                     "sourceResource": {"title": ["Squad car, 1966"], "description": ["a photo"],
                                        "rights": "https://creativecommons.org/publicdomain/mark/1.0/",
                                        "date": {"displayDate": "1966"}}},
                    {"id": "d2", "isShownAt": "https://example.org/2", "object": "https://example.org/2.jpg",
                     "dataProvider": "Some Library", "sourceResource": {"title": ["Unclear item"], "rights": "Contact repository for use"}},
                ]})
            return httpx.Response(200, content=BYTES + str(req.url.path).encode())
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "dpla", h)
            res = await DplaSource(api_key="DKEY").fetch(["chicago 1966"], ctx)
            pd = next(a for a in res.assets if a.title == "Squad car, 1966")
            unk = next(a for a in res.assets if a.title == "Unclear item")
            self.assertEqual(pd.license, "Public Domain Mark 1.0")
            self.assertEqual(unk.license, "")
            self.assertIn("Contact repository", unk.description)
            self.assertEqual(risk(unk), "high")
            self.assertNotIn("DKEY", ctx.http.log_path.read_text())


class ChroniclingAmericaTests(unittest.IsolatedAsyncioTestCase):
    def test_best_image_picks_last_jpg(self):
        urls = ["https://x/a.gif", "https://x/a_s.jpg#h=100", "https://x/a_l.jpg#h=900"]
        self.assertEqual(best_image(urls), "https://x/a_l.jpg")

    async def test_public_domain_when_rights_advisory_says_so(self):
        def h(req):
            if req.url.host == "www.loc.gov":
                return httpx.Response(200, json={"results": [
                    {"title": "The Daily Tribune", "date": "1900-01-05", "url": "https://www.loc.gov/item/sn1/",
                     "image_url": ["https://tile.loc.gov/a_s.jpg", "https://tile.loc.gov/a_l.jpg"],
                     "rights_advisory": ["No known restrictions on publication."]},
                    {"title": "The Evening Post", "date": "1901-02-02", "url": "https://www.loc.gov/item/sn2/",
                     "image_url": ["https://tile.loc.gov/b.jpg"]},
                ]})
            return httpx.Response(200, content=BYTES + str(req.url.path).encode())
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "chronicling_america", h)
            res = await ChroniclingAmericaSource().fetch(["chicago"], ctx)
            pd = next(a for a in res.assets if "Tribune" in a.title)
            silent = next(a for a in res.assets if "Post" in a.title)
            self.assertTrue(pd.source_url.endswith("a_l.jpg"))
            self.assertIn("Public domain", pd.license)
            self.assertEqual(silent.license, "")
            self.assertIn("no rights_advisory field", silent.description)
            self.assertEqual(risk(silent), "high")


class FlickrTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_is_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "flickr", lambda r: httpx.Response(404))
            with self.assertRaises(SourceUnavailable):
                await FlickrSource(api_key="").fetch(["x"], ctx)

    async def test_commons_license_and_key_hidden(self):
        def h(req):
            if req.url.host == "www.flickr.com":
                return httpx.Response(200, json={"photos": {"photo": [
                    {"id": "111", "owner": "22@N00", "ownername": "Chicago History Museum",
                     "title": "Squad car 1966", "license": "7", "url_l": "https://live.staticflickr.com/1_l.jpg",
                     "width_l": "1024", "height_l": "683", "description": {"_content": "A period squad car"}}]}})
            return httpx.Response(200, content=BYTES)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "flickr", h)
            res = await FlickrSource(api_key="FKEY").fetch(["chicago 1966"], ctx)
            a = res.assets[0]
            self.assertEqual(a.license, "No known copyright restrictions (Flickr Commons)")
            self.assertEqual((a.width, a.height), (1024, 683))
            self.assertEqual(risk(a), "low")
            self.assertNotIn("FKEY", ctx.http.log_path.read_text())

    async def test_government_work_license_reads_as_public_domain(self):
        def h(req):
            return httpx.Response(200, json={"photos": {"photo": [
                {"id": "9", "owner": "1", "ownername": "Agency", "title": "x", "license": "8",
                 "url_o": "https://live.staticflickr.com/9_o.jpg"}]}})
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "flickr", h)
            res = await FlickrSource(api_key="k").fetch(["x"], ctx)
            self.assertIn("public domain", res.assets[0].license.lower())
            self.assertEqual(risk(res.assets[0]), "low")


class EuropeanaTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_is_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "europeana", lambda r: httpx.Response(404))
            with self.assertRaises(SourceUnavailable):
                await EuropeanaSource(api_key="").fetch(["x"], ctx)

    async def test_items_parsed_and_rights_recorded(self):
        def h(req):
            if req.url.host == "api.europeana.eu":
                return httpx.Response(200, json={"items": [{
                    "id": "e1", "title": ["Old harbor"], "edmPreview": ["https://example.org/e1.jpg"],
                    "edmIsShownAt": ["https://europeana.eu/item/e1"], "dataProvider": ["Rijksmuseum"],
                    "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"], "year": ["1910"]}]})
            return httpx.Response(200, content=BYTES)
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(Path(d), "europeana", h)
            res = await EuropeanaSource(api_key="EKEY").fetch(["harbor"], ctx)
            a = res.assets[0]
            self.assertEqual(a.license, "Public Domain Mark 1.0")
            self.assertEqual(a.author, "Rijksmuseum")
            self.assertNotIn("EKEY", ctx.http.log_path.read_text())


if __name__ == "__main__":
    unittest.main()
