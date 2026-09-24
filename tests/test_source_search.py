"""POST /jobs/{id}/source-search and /jobs/{id}/assets/add-candidate: Gate 2's "Find more" panel, for the
three sources that (unlike YouTube) already have a free no-key API and already run as automated sources
elsewhere in the pipeline -- Internet Archive, Chronicling America, Wikimedia Commons (requested directly,
2026-09-23, after Aly asked why "Find more" was mostly just link-outs and asked for a way to streamline it).

Offline, same httpx.MockTransport pattern ApiSourcesTests/test_sources_flow.py already use -- these
endpoints call each source's real search()/keep_one() (pipeline/sources/base.py HttpSource), just through
the registry's `source_transport` instead of the real network, so a bug in the endpoint wiring itself
(wrong ctx, wrong dedup set, wrong Candidate reconstruction) would actually be caught here, unlike mocking
the adapter call away entirely."""
import tempfile
import time
import unittest
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from pipeline.api.app import create_app
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.sources.chronicling_america import ChroniclingAmericaSource
from pipeline.sources.commons import CommonsSource
from pipeline.sources.internet_archive import InternetArchiveSource
from tests.fakes import fake_registry

IMG = b"\xff\xd8\xff-fake-jpeg-"


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "commons.wikimedia.org":
        # The job's own normal sourcing round (keyword "kw1", tests/fakes.py's FakeKeywords) and these
        # tests' own on-demand search must not collide on the same candidate -- that would make a genuine
        # dedup ("already in this project") look like a broken add-candidate endpoint. Query text decides
        # which fixed candidate comes back, same idea as the real API's own query-dependent results.
        query = request.url.params.get("gsrsearch", "")
        if "courthouse" not in query:
            return httpx.Response(200, json={"query": {"pages": {"z": {
                "index": 1, "title": "File:Empty hospital hallway.jpg",
                "imageinfo": [{"thumburl": "https://upload.wikimedia.org/hallway.jpg", "thumbwidth": 1280,
                              "thumbheight": 900, "url": "https://upload.wikimedia.org/hallway-orig.jpg",
                              "width": 3000, "height": 2000, "mime": "image/jpeg",
                              "descriptionurl": "https://commons.wikimedia.org/wiki/File:Empty_hospital_hallway.jpg",
                              "user": "SomeoneElse",
                              "extmetadata": {"LicenseShortName": {"value": "CC0 1.0"}, "Artist": {"value": "Unknown"},
                                              "ImageDescription": {"value": ""}}}]}}}})
        return httpx.Response(200, json={"query": {"pages": {"a": {
            "index": 1, "title": "File:Cook County Courthouse.jpg",
            "imageinfo": [{"thumburl": "https://upload.wikimedia.org/courthouse.jpg", "thumbwidth": 1280,
                          "thumbheight": 900, "url": "https://upload.wikimedia.org/courthouse-orig.jpg",
                          "width": 3000, "height": 2000, "mime": "image/jpeg",
                          "descriptionurl": "https://commons.wikimedia.org/wiki/File:Cook_County_Courthouse.jpg",
                          "user": "ChicagoArchivist",
                          "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"},
                                          "Artist": {"value": "Chicago Park District"},
                                          "ImageDescription": {"value": "Cook County Courthouse, c. 1965"}}}]}}}})
    if path == "/advancedsearch.php":
        return httpx.Response(200, json={"response": {"docs": [
            {"identifier": "speck1966", "title": "Evening news report", "mediatype": "movies",
             "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"}]}})
    if path == "/metadata/speck1966":
        return httpx.Response(200, json={
            "metadata": {"title": "Evening news report", "creator": "WBBM-TV",
                        "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"},
            "files": [{"name": "report.mp4", "size": "4000000"}]})
    if host == "www.loc.gov":
        return httpx.Response(200, json={"results": [
            {"title": "The Chicago Tribune", "date": "1966-07-15", "url": "https://www.loc.gov/item/sn1/",
             "image_url": ["https://tile.loc.gov/a_s.jpg", "https://tile.loc.gov/a_l.jpg"],
             "rights_advisory": ["No known restrictions on publication."]}]})
    return httpx.Response(200, content=IMG + str(request.url).encode())


class SourceSearchEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, _ = fake_registry()
        reg.source_transport = httpx.MockTransport(handler)
        reg.register_source("commons", lambda: CommonsSource(per_query=5))
        reg.register_source("archive", lambda: InternetArchiveSource())
        reg.register_source("chronicling_america", lambda: ChroniclingAmericaSource())
        self.store_dir = self.tmp.name
        self.client = TestClient(create_app(Orchestrator(JobStore(self.store_dir), reg), settings={}))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def wait(self, jid, state):
        end = time.time() + 5
        while time.time() < end:
            j = self.client.get(f"/jobs/{jid}").json()
            if j["state"] == state:
                return j
            time.sleep(0.02)
        self.fail(f"stuck at {j['state']}; wanted {state}")

    def to_assets_review(self, sources=("commons",)):
        # "commons" stays configured so the job actually reaches assets_review with something pending
        # (zero-result rounds skip straight past it) -- the mock handler above gives the job's own round
        # (query "kw1") a DIFFERENT candidate than these tests' own on-demand "courthouse" search finds,
        # so a genuine duplicate-add guard is never confused with a broken add-candidate endpoint.
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake",
                                                          "sources": list(sources)}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        j = self.wait(jid, "assets_review")
        return jid, j

    # -- source-search --

    def test_unknown_source_is_a_400(self):
        jid, _ = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/source-search", json={"source": "instagram", "query": "x", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("source must be one of", r.json()["error"])

    def test_query_is_required(self):
        jid, _ = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/source-search", json={"source": "commons", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 400)

    def test_wrong_job_state_is_a_409(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake"}})
        jid = r.json()["id"]     # still keywords_running/keywords_review
        r = self.client.post(f"/jobs/{jid}/source-search", json={"source": "commons", "query": "x", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 409)

    def test_searches_commons_and_adds_nothing(self):
        jid, before = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/source-search", json={"source": "commons", "query": "cook county courthouse", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Cook County Courthouse")
        self.assertEqual(out[0]["license"], "CC BY-SA 4.0")
        self.assertTrue(out[0]["id"])
        after = self.client.get(f"/jobs/{jid}").json()
        self.assertEqual(len(after["assets"]), len(before["assets"]))   # search alone adds nothing

    def test_searches_internet_archive(self):
        jid, _ = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/source-search", json={"source": "archive", "query": "richard speck 1966", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()[0]["title"], "Evening news report")

    def test_searches_chronicling_america(self):
        jid, _ = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/source-search", json={"source": "chronicling_america", "query": "speck", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Chicago Tribune", r.json()[0]["title"])

    def test_count_is_clamped_between_one_and_ten(self):
        jid, _ = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/source-search", json={"source": "commons", "query": "x", "count": 999, "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200)   # only 1 candidate in the mock, so this just proves it didn't 4xx

    def test_logged_to_its_own_source_folder(self):
        jid, _ = self.to_assets_review()
        self.client.post(f"/jobs/{jid}/source-search", json={"source": "commons", "query": "courthouse", "reviewer": "Aly"})
        log = JobStore(self.store_dir).job_dir(jid) / "sources" / "commons" / "requests.jsonl"
        self.assertTrue(log.exists())

    # -- assets/add-candidate --

    def test_unknown_source_is_a_400_on_add(self):
        jid, _ = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/assets/add-candidate",
                             json={"source": "instagram", "candidate": {"url": "https://x/1.jpg"}, "reviewer": "Aly"})
        self.assertEqual(r.status_code, 400)

    def test_missing_candidate_url_is_a_400(self):
        jid, _ = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/assets/add-candidate", json={"source": "commons", "candidate": {}, "reviewer": "Aly"})
        self.assertEqual(r.status_code, 400)

    def test_add_downloads_and_keeps_license_and_lands_pending(self):
        jid, before = self.to_assets_review()
        found = self.client.post(f"/jobs/{jid}/source-search",
                                 json={"source": "commons", "query": "cook county courthouse", "reviewer": "Aly"}).json()
        r = self.client.post(f"/jobs/{jid}/assets/add-candidate",
                             json={"source": "commons", "query": "cook county courthouse", "candidate": found[0], "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        after = self.client.get(f"/jobs/{jid}").json()
        self.assertEqual(len(after["assets"]), len(before["assets"]) + 1)
        added = next(a for a in after["assets"] if a["source_url"] == found[0]["url"])
        self.assertEqual(added["status"], "pending")
        self.assertEqual(added["license"], "CC BY-SA 4.0")
        self.assertEqual(added["author"], "Chicago Park District")
        self.assertEqual(added["source"], "commons")

    def test_add_same_url_twice_is_a_422(self):
        jid, _ = self.to_assets_review()
        found = self.client.post(f"/jobs/{jid}/source-search", json={"source": "commons", "query": "courthouse", "reviewer": "Aly"}).json()
        r1 = self.client.post(f"/jobs/{jid}/assets/add-candidate", json={"source": "commons", "candidate": found[0], "reviewer": "Aly"})
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.client.post(f"/jobs/{jid}/assets/add-candidate", json={"source": "commons", "candidate": found[0], "reviewer": "Aly"})
        self.assertEqual(r2.status_code, 422)
        self.assertIn("already in this project", r2.json()["error"])

    def test_add_records_a_default_note_naming_the_source_and_query(self):
        jid, _ = self.to_assets_review()
        found = self.client.post(f"/jobs/{jid}/source-search", json={"source": "commons", "query": "courthouse", "reviewer": "Aly"}).json()
        self.client.post(f"/jobs/{jid}/assets/add-candidate",
                         json={"source": "commons", "query": "courthouse", "candidate": found[0], "reviewer": "Aly"})
        log = self.client.get(f"/jobs/{jid}/decisions").json()["entries"]
        entry = next(e for e in log if e.get("action") == "added_gate2_asset")
        self.assertIn("Wikimedia Commons", entry["reason"])
        self.assertIn("courthouse", entry["reason"])
