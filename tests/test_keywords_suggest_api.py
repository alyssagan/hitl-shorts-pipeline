"""HTTP surface for Gate 2's "Suggest more search terms" (pipeline/core/orchestrator.py's
suggest_keywords()/approve_suggested_keywords(), pipeline/api/app.py's kw_suggest/kw_approve_suggested) --
POST /jobs/{id}/keywords/suggest and POST /jobs/{id}/keywords/approve-suggested. Same TestClient-over-ASGI
style as tests/test_api.py; the orchestrator-level behavior itself (already_covered, group tagging, no
state transition) is tests/test_suggest_keywords.py's job -- this only checks the two routes exist, parse
their body correctly, and map errors to the right HTTP status."""
import tempfile
import time
import unittest

import httpx
from starlette.testclient import TestClient

from pipeline.api.app import create_app
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.sources.pexels import PexelsSource
from tests.fakes import FakeSuggestKeywords, fake_registry

IMG = b"\xff\xd8\xff-fake-jpeg-"


def _pexels_handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "api.pexels.com":
        if "/videos/" in str(request.url):
            return httpx.Response(200, json={"videos": []})
        return httpx.Response(200, json={"photos": [{"id": 9, "width": 1080, "height": 1920,
                                                      "url": "https://www.pexels.com/photo/tabby-cat-9/",
                                                      "photographer": "Bo", "alt": "A tabby cat",
                                                      "src": {"large2x": "https://images.pexels.com/9.jpg"}}]})
    if host == "images.pexels.com":
        return httpx.Response(200, content=IMG + str(request.url).encode())
    return httpx.Response(404)


LLM = {"keywords": "llm", "scenes": "fake", "render": "fake", "sources": ["pexels"]}


class KeywordsSuggestApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, self.stages = fake_registry()
        self.suggest_stage = FakeSuggestKeywords()
        reg.register_keywords("llm", lambda: self.suggest_stage)
        reg.source_transport = httpx.MockTransport(_pexels_handler)
        reg.register_source("pexels", lambda: PexelsSource(api_key="k", videos=True))
        app = create_app(Orchestrator(JobStore(self.tmp.name), reg), settings={})
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def wait_for(self, job_id, state, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            j = self.client.get(f"/jobs/{job_id}").json()
            if j["state"] == state:
                return j
            time.sleep(0.02)
        self.fail(f"job never reached {state}; last: {j['state']}")

    def to_assets_review(self):
        jid = self.client.post("/jobs", json={"subject": "cats", "providers": LLM}).json()["id"]
        self.client.post(f"/jobs/{jid}/start")
        self.wait_for(jid, "script_review")
        self.client.post(f"/jobs/{jid}/script/approve", json={"reviewer": "Aly"})
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md) -- no keywords/review call
        # needed here, the job runs straight through to sourcing/vetting on its own.
        return jid, self.wait_for(jid, "assets_review")

    def test_suggest_then_approve_over_http(self):
        jid, job = self.to_assets_review()

        r = self.client.post(f"/jobs/{jid}/keywords/suggest", json={"feedback": "more of the building", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["suggested_ids"]), 2)
        by_id = {k["id"]: k for k in body["keywords"]}
        suggested = [by_id[i] for i in body["suggested_ids"]]
        self.assertEqual({k["term"] for k in suggested}, {"suggested case term", "suggested stock term"})
        self.assertTrue(all(not k["approved"] for k in suggested))

        case_id = next(k["id"] for k in suggested if k["term"] == "suggested case term")
        r2 = self.client.post(f"/jobs/{jid}/keywords/approve-suggested", json={"keyword_ids": [case_id], "reviewer": "Aly"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["state"], "assets_review")   # no state transition, still Gate 2
        approved_terms = [k["term"] for k in r2.json()["keywords"] if k["approved"]]
        self.assertIn("suggested case term", approved_terms)
        self.assertNotIn("suggested stock term", approved_terms)

    def test_suggest_outside_asset_review_is_a_409(self):
        jid = self.client.post("/jobs", json={"subject": "cats", "providers": LLM}).json()["id"]
        self.client.post(f"/jobs/{jid}/start")
        self.wait_for(jid, "script_review")   # still Gate 1, never reached assets_review
        r = self.client.post(f"/jobs/{jid}/keywords/suggest", json={"reviewer": "Aly"})
        self.assertEqual(r.status_code, 422)   # suggest_keywords raises ValueError, not a TransitionError
        self.assertIn("asset review", r.json()["error"])

    def test_approve_suggested_needs_a_reviewer_name(self):
        jid, job = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/keywords/suggest", json={})
        keyword_id = r.json()["suggested_ids"][0]
        r2 = self.client.post(f"/jobs/{jid}/keywords/approve-suggested", json={"keyword_ids": [keyword_id]})
        self.assertEqual(r2.status_code, 422)

    def test_approve_suggested_with_an_unknown_id_is_a_422(self):
        jid, job = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/keywords/approve-suggested", json={"keyword_ids": ["nope"], "reviewer": "Aly"})
        self.assertEqual(r.status_code, 422)

    def test_suggest_with_the_manual_provider_is_a_422(self):
        jid = self.client.post("/jobs", json={"subject": "cats",
                                               "providers": {"keywords": "fake", "scenes": "fake", "render": "fake",
                                                             "sources": ["pexels"]}}).json()["id"]
        self.client.post(f"/jobs/{jid}/start")
        self.wait_for(jid, "script_review")
        self.client.post(f"/jobs/{jid}/script/approve", json={"reviewer": "Aly"})
        self.wait_for(jid, "assets_review")
        r = self.client.post(f"/jobs/{jid}/keywords/suggest", json={"reviewer": "Aly"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("llm", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
