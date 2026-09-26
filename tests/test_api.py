import tempfile
import time
import unittest

from starlette.testclient import TestClient

from pipeline.api.app import create_app
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from tests.fakes import fake_registry

FAKE = {"keywords": "fake", "scenes": "fake", "render": "fake"}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, self.stages = fake_registry()
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

    def test_full_flow_over_http(self):
        r = self.client.post("/jobs", json={"subject": "cats", "providers": FAKE})
        self.assertEqual(r.status_code, 201)
        jid = r.json()["id"]
        self.assertEqual(r.json()["allowed_events"], ["cancel", "start"])

        self.client.post(f"/jobs/{jid}/start")
        job = self.wait_for(jid, "script_review")
        self.assertNotIn("approve_keywords", job["allowed_events"])   # guard visible to the UI
        self.assertTrue(job["script"])

        r = self.client.post(f"/jobs/{jid}/script/approve", json={"reviewer": "tester"})
        self.assertEqual(r.json()["state"], "keywords_running")
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md), folded into gate 2 --
        # no keywords/review call needed here, unlike the old flow.
        job = self.wait_for(jid, "scenes_review")

        ids = [s["id"] for s in job["scenes"]]
        r = self.client.patch(f"/jobs/{jid}/scenes", json={"order": ids[::-1]})
        self.assertEqual([s["id"] for s in r.json()["scenes"]], ids[::-1])

        self.assertEqual(self.client.post(f"/jobs/{jid}/scenes/approve").json()["state"], "rendering")
        self.wait_for(jid, "completed")
        out = self.client.get(f"/jobs/{jid}/output")
        self.assertEqual((out.status_code, out.content), (200, b"fake-mp4"))

    def test_errors_map_to_http_codes(self):
        self.assertEqual(self.client.get("/jobs/missing").status_code, 404)
        self.assertEqual(self.client.post("/jobs", json={"subject": ""}).status_code, 422)
        self.assertEqual(self.client.post("/jobs", json={"subject": "x", "providers": {"keywords": "nope"}}).status_code, 422)
        jid = self.client.post("/jobs", json={"subject": "x", "providers": FAKE}).json()["id"]
        r = self.client.post(f"/jobs/{jid}/scenes/approve")           # skipping ahead
        self.assertEqual(r.status_code, 409)
        self.assertIn("not allowed", r.json()["error"])

    def test_providers_and_health(self):
        self.assertEqual(self.client.get("/health").json(), {"ok": True})
        self.assertEqual(self.client.get("/providers").json()["keywords"], ["fake"])

    def test_bad_json_is_a_400(self):
        r = self.client.post("/jobs", content=b"{not json", headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()


class ReviewPageTest(unittest.TestCase):
    def test_review_page_is_served(self):
        from starlette.testclient import TestClient
        from pipeline.api.app import create_app
        import tempfile
        from pipeline.core.orchestrator import Orchestrator
        from pipeline.core.store import JobStore
        from pipeline.stages.registry import build_default_registry
        with tempfile.TemporaryDirectory() as d:
            app = create_app(Orchestrator(JobStore(d), build_default_registry({}), {}), {})
            with TestClient(app) as c:
                for path in ("/review", "/review/abc"):
                    r = c.get(path)
                    self.assertEqual(r.status_code, 200)
                    self.assertIn("Asset review", r.text)
