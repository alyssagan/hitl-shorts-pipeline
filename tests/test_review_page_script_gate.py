"""Gate 1 (script_review), added to the review page in the 2026-09-25 reorder (docs/PIPELINE_STAGES.md).

Before this, pipeline/api/review_page.py had NO handling at all for job.state == "script_review": a job
sitting at the new Gate 1 would just show the generic "this project is in state X, not asset or scene
review" banner, with no way to see the script or call POST /jobs/{id}/script/approve|reject from the
browser -- even though the HTTP API for it worked fine (tests/test_api.py's test_full_flow_over_http
exercises that). This file checks the page itself: that script_review gets its own body (not the fallback
banner), that the wiring points at the real endpoints, and that a browser-driven approve/reject actually
carries the job forward exactly like the terminal/API paths do.
"""
from __future__ import annotations

import re
import tempfile
import time
import unittest

from starlette.testclient import TestClient

from pipeline.api.app import create_app
from pipeline.api.review_page import PAGE
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from tests.fakes import fake_registry

FAKE = {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": []}


class PageWiringTests(unittest.TestCase):
    """Static checks on the PAGE template itself -- no server needed."""

    def test_script_review_has_its_own_body_function(self):
        self.assertIn("function scriptReviewBody()", PAGE)
        self.assertIn('job.state==="script_review"', PAGE)

    def test_approve_and_reject_call_the_real_endpoints(self):
        self.assertIn("/script/approve", PAGE)
        self.assertIn("/script/reject", PAGE)

    def test_fallback_banner_no_longer_fires_for_script_review(self):
        # The generic "not asset or scene review" banner's condition must exclude inScript, or a job sitting
        # at the new Gate 1 would show that banner instead of the actual script/approve/reject panel.
        m = re.search(r"\(!reviewing && !inScenes && !inScript\)\?h\(\"div\",\{class:\"banner\"\}", PAGE)
        self.assertIsNotNone(m, "banner condition no longer excludes script_review -- Gate 1 would be hidden again")


class ScriptGateHttpTests(unittest.TestCase):
    """Drives a real job to script_review through the ASGI app and checks the served page + the
    approve/reject endpoints the page's approveScript()/rejectScript() call."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, self.stages = fake_registry()
        app = create_app(Orchestrator(JobStore(self.tmp.name), reg), settings={})
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def wait_for(self, jid, state, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            j = self.client.get(f"/jobs/{jid}").json()
            if j["state"] == state:
                return j
            time.sleep(0.02)
        self.fail(f"job never reached {state}; last: {j['state']}")

    def make_job(self):
        jid = self.client.post("/jobs", json={"subject": "cats", "providers": FAKE}).json()["id"]
        self.client.post(f"/jobs/{jid}/start")
        self.wait_for(jid, "script_review")
        return jid

    def test_review_page_loads_for_a_job_sitting_at_script_review(self):
        # The page is a client-rendered single-page app -- the server sends the SAME static HTML/JS no
        # matter what state the job is in, and which panel actually shows up is decided client-side by
        # job.state (checked statically in PageWiringTests above). This just confirms the page still loads
        # for a job at the new Gate 1 rather than erroring, and that the client-side code it ships knows
        # about that job (same JOB id parsed from the URL every other gate's tests rely on).
        jid = self.make_job()
        page = self.client.get(f"/review/{jid}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Approve script", page.text)
        self.assertIn("Ask for a rewrite instead", page.text)

    def test_approve_script_over_http_carries_the_job_forward(self):
        jid = self.make_job()
        job = self.client.get(f"/jobs/{jid}").json()
        r = self.client.post(f"/jobs/{jid}/script/approve", json={"reviewer": "Aly", "edited_script": job["script"]})
        self.assertEqual(r.status_code, 200)
        # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md) -- the job runs straight through.
        self.wait_for(jid, "scenes_review")

    def test_reject_script_over_http_loops_back_for_a_rewrite(self):
        jid = self.make_job()
        r = self.client.post(f"/jobs/{jid}/script/reject", json={"reviewer": "Aly", "feedback": "too dry"})
        self.assertEqual(r.status_code, 200)
        job = self.wait_for(jid, "script_review")
        self.assertEqual(job["script_feedback"], ["too dry"])

    def test_approve_without_a_reviewer_name_is_a_422(self):
        # approve_script() requires a reviewer (Orchestrator._who(..., require=True)) -- the same rule the
        # page's "Your name" field enforces for every other gate; check it isn't silently skipped here.
        jid = self.make_job()
        r = self.client.post(f"/jobs/{jid}/script/approve", json={})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
