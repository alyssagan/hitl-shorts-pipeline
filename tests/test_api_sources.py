import tempfile
import time
import unittest
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from pipeline.api.app import create_app
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.sources.commons import CommonsSource
from tests.fakes import fake_registry
from tests.test_sources_flow import handler


class ApiSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, _ = fake_registry()
        reg.source_transport = httpx.MockTransport(handler)
        reg.register_source("commons", lambda: CommonsSource(per_query=5))
        self.client = TestClient(create_app(Orchestrator(JobStore(self.tmp.name), reg), settings={}))
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

    def test_asset_gate_and_decision_log_over_http(self):
        bad = self.client.post("/jobs", json={"subject": "cats", "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["nope"]}})
        self.assertEqual(bad.status_code, 422)
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        j = self.wait(jid, "assets_review")
        self.assertTrue(all(a["vetting"]["summary"] for a in j["assets"]))

        # pending assets block approval (409); missing reviewer is a 422
        self.assertEqual(self.client.post(f"/jobs/{jid}/assets/approve", json={"reviewer": "Aly"}).status_code, 409)
        ok = next(a for a in j["assets"] if a["vetting"]["risk"] != "high")
        self.assertEqual(self.client.post(f"/jobs/{jid}/assets/review", json={"decisions": {ok["id"]: {"decision": "approve"}}}).status_code, 422)
        # file preview
        f = self.client.get(f"/jobs/{jid}/assets/{ok['id']}/file")
        self.assertEqual(f.status_code, 200)

        decisions = {a["id"]: ({"decision": "approve"} if a["id"] == ok["id"] else {"decision": "reject", "note": "not needed"}) for a in j["assets"]}
        self.assertEqual(self.client.post(f"/jobs/{jid}/assets/review", json={"decisions": decisions, "reviewer": "Aly"}).status_code, 200)
        self.assertEqual(self.client.post(f"/jobs/{jid}/assets/approve", json={"reviewer": "Aly"}).status_code, 200)
        self.wait(jid, "scenes_review")
        self.client.post(f"/jobs/{jid}/scenes/approve", json={"reviewer": "Aly"})
        self.wait(jid, "completed")

        d = self.client.get(f"/jobs/{jid}/decisions").json()
        self.assertTrue(d["intact"])
        self.assertIn("asset_reviewed", [e["action"] for e in d["entries"]])
        md = self.client.get(f"/jobs/{jid}/decisions?format=md")
        self.assertIn("Aly", md.text)
        self.assertEqual(self.client.get("/jobs/zzz/decisions").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class LabelTests(ApiSourcesTests):
    def test_labels_saved_only_for_explicit_clicks(self):
        import json
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        j = self.wait(jid, "assets_review")
        a, b, c = j["assets"][:3]
        dec = {a["id"]: {"decision": "approve", "label": "relevant"}, b["id"]: {"decision": "reject", "note": "x", "label": "irrelevant"},
               c["id"]: {"decision": "reject", "note": "no decision"}}
        if (a.get("vetting") or {}).get("risk") == "high":
            dec[a["id"]]["note"] = "ok"
        self.client.post(f"/jobs/{jid}/assets/review", json={"decisions": dec, "reviewer": "Aly"})
        path = Path(self.tmp.name) / f"{j['slug']}-{jid}" / "RELEVANCE_LABELS.jsonl"
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        self.assertEqual({r["asset_id"]: r["label"] for r in rows}, {a["id"]: "relevant", b["id"]: "irrelevant"})
        self.assertIn("machine_score", rows[0])


class LogEndpointTests(ApiSourcesTests):
    def test_activity_log_covers_the_run(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        self.wait(jid, "assets_review")
        text = self.client.get(f"/jobs/{jid}/log").text
        for expect in ("created 'cats'", "START keywords_running", "START sourcing_running", "pulling from commons",
                       "commons done in", "vetted", "keywords_running -> keywords_review", "keywords=['kw1']"):
            self.assertIn(expect, text)
        self.assertNotIn(" http ", text)                                  # DEBUG hidden by default
        self.assertIn(" http ", self.client.get(f"/jobs/{jid}/log?level=DEBUG").text)
        js = self.client.get(f"/jobs/{jid}/log?format=json&after=3").json()
        self.assertIn("next", js)


class RelevanceScorerWiringTests(unittest.TestCase):
    def test_orchestrator_uses_configured_relevance_scorer(self):
        from pipeline.vetting.llm_relevance import LlmRelevanceScorer
        def llm_handler(req: httpx.Request):
            import json
            body = json.loads(req.content)
            text = body["messages"][0]["content"]
            ids = [ln.split("id=")[1].split()[0] for ln in text.splitlines() if ln.strip() and ln[0].isdigit()]
            reply = [{"id": i, "score": 5, "why": "not really about it"} for i in ids]
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
        with tempfile.TemporaryDirectory() as tmp:
            reg, _ = fake_registry()
            reg.source_transport = httpx.MockTransport(handler)
            reg.register_source("commons", lambda: CommonsSource(per_query=5))
            reg._relevance_scorer_factory = lambda: LlmRelevanceScorer(
                "http://llm", "key", "m", transport=httpx.MockTransport(llm_handler), retry_waits=())
            # A wide borderline band forces every asset to get the LLM's (stubbed) opinion regardless of its real
            # TF-IDF score, so this test can check the wiring (the configured scorer is actually used and its
            # score wins) without depending on real TF-IDF math for "cats" vs these fixtures.
            settings = {"relevance": {"borderline_band": 1.0}}
            with TestClient(create_app(Orchestrator(JobStore(tmp), reg, settings=settings), settings=settings)) as client:
                r = client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                               "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
                jid = r.json()["id"]
                client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
                end = time.time() + 5
                while time.time() < end and client.get(f"/jobs/{jid}").json()["state"] != "keywords_review":
                    time.sleep(0.02)
                j = client.get(f"/jobs/{jid}").json()
                client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
                end = time.time() + 5
                while time.time() < end and client.get(f"/jobs/{jid}").json()["state"] != "assets_review":
                    time.sleep(0.02)
                j = client.get(f"/jobs/{jid}").json()
                self.assertEqual(j["state"], "assets_review")
                self.assertTrue(all(a["vetting"]["relevance_method"] == "llm-semantic" for a in j["assets"]))
                self.assertTrue(all(a["vetting"]["relevance"] == 0.05 for a in j["assets"]))
