import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from starlette.testclient import TestClient

from pipeline.api.app import create_app
from pipeline.core.models import Asset
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
        # "use"/"duplicate"/"irrelevant" are the three human review labels (docs/EVALUATION.md) -- independent
        # of `decision` (approve/reject), which still gates the pipeline's approved pool.
        dec = {a["id"]: {"decision": "approve", "label": "use"}, b["id"]: {"decision": "reject", "note": "x", "label": "irrelevant"},
               c["id"]: {"decision": "reject", "note": "no decision"}}
        if (a.get("vetting") or {}).get("risk") == "high":
            dec[a["id"]]["note"] = "ok"
        resp = self.client.post(f"/jobs/{jid}/assets/review", json={"decisions": dec, "reviewer": "Aly"})
        self.assertEqual(resp.status_code, 200, resp.text)
        path = Path(self.tmp.name) / f"{j['slug']}-{jid}" / "RELEVANCE_LABELS.jsonl"
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        self.assertEqual({r["asset_id"]: r["label"] for r in rows}, {a["id"]: "use", b["id"]: "irrelevant"})
        self.assertIn("machine_score", rows[0])
        self.assertIn("scoring_method", rows[0])
        self.assertIn("method_version", rows[0])


class AssetsAddUrlEndpointTests(ApiSourcesTests):
    """POST /jobs/{id}/assets/add-url -- Gate 2's "Add links" (#9): a synchronous, per-link add so a bad
    or duplicate link fails right away with a reason, rather than being silently queued for the next sourcing
    round. The actual download is mocked (UrlListSource.fetch_one) the same way an orchestrator-level test
    would construct an already-fetched Asset -- no real network/yt-dlp involved."""
    def to_assets_review(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        j = self.wait(jid, "assets_review")
        return jid, j

    def fake_asset(self, **kw):
        base = dict(source="urls", kind="video", path="/tmp/w.mp4", rel_path="w.mp4", source_url="https://files.example.com/w.mp4",
                    title="w.mp4", license="CC0", width=1920, height=1080, sha256="httpurl1", import_method="manual_url")
        base.update(kw)
        return Asset(**base)

    def test_url_is_required(self):
        jid, _ = self.to_assets_review()
        r = self.client.post(f"/jobs/{jid}/assets/add-url", json={"reviewer": "Aly"})
        self.assertEqual(r.status_code, 400)

    def test_wrong_job_state_is_a_409_before_any_download_is_attempted(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake"}})
        jid = r.json()["id"]      # still keywords_running/keywords_review, not assets_review
        r = self.client.post(f"/jobs/{jid}/assets/add-url", json={"url": "https://files.example.com/w.mp4", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 409)

    def test_successful_add_lands_pending_with_the_right_provenance(self):
        jid, _ = self.to_assets_review()
        with patch("pipeline.api.app.UrlListSource") as cls:
            cls.return_value.fetch_one = AsyncMock(return_value=self.fake_asset())
            r = self.client.post(f"/jobs/{jid}/assets/add-url", json={"url": "https://files.example.com/w.mp4", "note": "b-roll", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        added = next(a for a in r.json()["assets"] if a["source_url"] == "https://files.example.com/w.mp4")
        self.assertEqual(added["status"], "pending")        # never auto-approved -- Gate 2 IS the review
        self.assertEqual(added["import_method"], "manual_url")

    def test_duplicate_url_is_a_422_and_does_not_download_again(self):
        jid, _ = self.to_assets_review()
        with patch("pipeline.api.app.UrlListSource") as cls:
            cls.return_value.fetch_one = AsyncMock(return_value=self.fake_asset())
            first = self.client.post(f"/jobs/{jid}/assets/add-url", json={"url": "https://files.example.com/w.mp4", "reviewer": "Aly"})
            self.assertEqual(first.status_code, 200)
            second = self.client.post(f"/jobs/{jid}/assets/add-url", json={"url": "https://files.example.com/w.mp4", "reviewer": "Aly"})
            self.assertEqual(second.status_code, 422)
            self.assertEqual(cls.return_value.fetch_one.await_count, 1)   # no second download attempted
        after = self.client.get(f"/jobs/{jid}").json()
        self.assertEqual(sum(a["source_url"] == "https://files.example.com/w.mp4" for a in after["assets"]), 1)

    def test_download_failure_is_a_422_with_the_reason(self):
        jid, _ = self.to_assets_review()
        with patch("pipeline.api.app.UrlListSource") as cls:
            cls.return_value.fetch_one = AsyncMock(side_effect=RuntimeError("this doesn't look like a direct video/photo link"))
            r = self.client.post(f"/jobs/{jid}/assets/add-url", json={"url": "https://files.example.com/article", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("doesn't look like a direct video/photo link", r.json()["error"])


class FolderFilesEndpointTests(unittest.TestCase):
    """GET /jobs/{id}/folder-files (#10): browse-only listing of the server's own-footage folder, with
    which files (if any) are already queued for this specific job."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scraped = Path(self.tmp.name) / "scraped"
        (self.scraped / "sub").mkdir(parents=True)
        (self.scraped / "sub" / "x.jpg").write_bytes(b"\xff\xd8\xff-fake-jpeg-")
        reg, _ = fake_registry()
        settings = {"sources": {"folder_path": str(self.scraped)}}
        self.client = TestClient(create_app(Orchestrator(JobStore(self.tmp.name + "/projects"), reg, settings), settings=settings))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_lists_files_and_reflects_this_jobs_selection(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake",
                                                          "sources": [], "options": {"folder_files": ["sub/x.jpg"]}}})
        jid = r.json()["id"]
        out = self.client.get(f"/jobs/{jid}/folder-files").json()
        self.assertEqual(out["files"], [{"path": "sub/x.jpg", "size": 14, "kind": "image", "usable": True,
                                         "title": "", "has_sidecar": False, "selected_for_this_job": True}])

    def test_unselected_job_shows_nothing_selected(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": []}})
        jid = r.json()["id"]
        out = self.client.get(f"/jobs/{jid}/folder-files").json()
        self.assertFalse(out["files"][0]["selected_for_this_job"])

    def test_unknown_job_is_404(self):
        self.assertEqual(self.client.get("/jobs/nope/folder-files").status_code, 404)


class AssetIdentityRightsCategoryReportEndpointTests(ApiSourcesTests):
    """#13's write/read paths over HTTP: identity/rights/category edits and the per-job asset report."""
    def setup_job_in_assets_review(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        j = self.wait(jid, "assets_review")
        return jid, j

    def test_identity_rights_category_round_trip_and_show_in_the_asset(self):
        jid, j = self.setup_job_in_assets_review()
        aid = j["assets"][0]["id"]
        r = self.client.post(f"/jobs/{jid}/assets/{aid}/identity",
                              json={"status": "verified", "depicts": "the victim", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/jobs/{jid}/assets/{aid}/rights", json={"status": "cc0", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/jobs/{jid}/assets/{aid}/category", json={"category": "verified_case", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        after = self.client.get(f"/jobs/{jid}").json()
        a = next(x for x in after["assets"] if x["id"] == aid)
        self.assertEqual((a["identity_status"], a["depicts"], a["rights_status"], a["category"]),
                         ("verified", "the victim", "cc0", "verified_case"))

    def test_bad_status_is_a_422(self):
        jid, j = self.setup_job_in_assets_review()
        aid = j["assets"][0]["id"]
        r = self.client.post(f"/jobs/{jid}/assets/{aid}/identity", json={"status": "pretty_sure", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("status must be one of", r.json()["error"])

    def test_missing_reviewer_is_a_422(self):
        jid, j = self.setup_job_in_assets_review()
        aid = j["assets"][0]["id"]
        r = self.client.post(f"/jobs/{jid}/assets/{aid}/rights", json={"status": "cc0"})
        self.assertEqual(r.status_code, 422)

    def test_asset_report_breaks_down_by_source_and_includes_usage(self):
        jid, j = self.setup_job_in_assets_review()
        out = self.client.get(f"/jobs/{jid}/asset-report").json()
        self.assertEqual(out["total_assets"], len(j["assets"]))
        self.assertIn("commons", out["by_source"])
        self.assertIn("cost_usd", out["usage"])
        self.assertEqual(out["by_identity_status"].get("unverified"), len(j["assets"]))

    def test_unknown_job_is_404_for_report_and_edits(self):
        self.assertEqual(self.client.get("/jobs/nope/asset-report").status_code, 404)
        self.assertEqual(self.client.post("/jobs/nope/assets/x/identity", json={"status": "verified", "reviewer": "Aly"}).status_code, 404)


class VisualCoverageEndpointTests(ApiSourcesTests):
    """Pre-render visual coverage check (#12) over HTTP: GET /visual-coverage and the gate on
    POST /scenes/approve. See tests/test_visual_coverage.py for the orchestrator-level coverage."""
    def setup_job_in_scenes_review(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        j = self.wait(jid, "assets_review")
        decisions = {a["id"]: {"decision": "approve"} for a in j["assets"] if a["vetting"]["risk"] != "high"}
        decisions.update({a["id"]: {"decision": "reject", "note": "not needed"} for a in j["assets"] if a["id"] not in decisions})
        self.client.post(f"/jobs/{jid}/assets/review", json={"decisions": decisions, "reviewer": "Aly"})
        self.client.post(f"/jobs/{jid}/assets/approve", json={"reviewer": "Aly"})
        j = self.wait(jid, "scenes_review")
        return jid, j

    def test_ready_when_no_checklist_was_ever_used(self):
        jid, j = self.setup_job_in_scenes_review()
        out = self.client.get(f"/jobs/{jid}/visual-coverage").json()
        self.assertFalse(out["has_checklist"])
        self.assertTrue(out["ready"])

    def test_generate_from_keywords_over_http(self):
        # Gate 2's "Generate from approved keywords" button (docs/REVIEW_UI.md) -- without this, the
        # checklist stays empty in a real browser session and the coverage check never has anything to flag.
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        term = j["keywords"][0]["term"]
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        self.wait(jid, "assets_review")
        r = self.client.post(f"/jobs/{jid}/visual-checklist/generate", json={"reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["visual_checklist"]), 1)
        self.assertEqual(r.json()["visual_checklist"][0]["linked_keyword_term"], term)
        self.assertEqual(r.json()["visual_checklist"][0]["status"], "needed")

    def test_unresolved_item_blocks_approve_until_an_override_note_is_given(self):
        jid, j = self.setup_job_in_scenes_review()
        self.client.post(f"/jobs/{jid}/visual-checklist", json={"label": "a photo of the scene", "reviewer": "Aly"})
        out = self.client.get(f"/jobs/{jid}/visual-coverage").json()
        self.assertFalse(out["ready"])
        self.assertEqual(len(out["remediation_options"]), 5)

        blocked = self.client.post(f"/jobs/{jid}/scenes/approve", json={"reviewer": "Aly"})
        self.assertEqual(blocked.status_code, 422, blocked.text)
        self.assertIn("a photo of the scene", blocked.json()["error"])

        ok = self.client.post(f"/jobs/{jid}/scenes/approve",
                               json={"reviewer": "Aly", "override_note": "using the b-roll we already have"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.wait(jid, "completed")

    def test_unknown_job_is_404(self):
        self.assertEqual(self.client.get("/jobs/nope/visual-coverage").status_code, 404)


class SceneCropEndpointTests(ApiSourcesTests):
    """Gate 3's manual crop tool over HTTP: PATCH/remove .../scenes/{id}/crop, and GET /render-settings,
    which the tool needs client-side to draw a correctly-proportioned crop box. See tests/test_scene_crop.py
    for the orchestrator- and render-stage-level coverage (crop_box() math, ffmpeg invocation, fallbacks)."""
    def setup_job_in_scenes_review(self):
        # Same path VisualCoverageEndpointTests uses to reach scenes_review -- duplicated locally rather
        # than subclassed, so this class's own test_ methods run once each instead of also re-running
        # every inherited one.
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        j = self.wait(jid, "assets_review")
        decisions = {a["id"]: {"decision": "approve"} for a in j["assets"] if a["vetting"]["risk"] != "high"}
        decisions.update({a["id"]: {"decision": "reject", "note": "not needed"} for a in j["assets"] if a["id"] not in decisions})
        self.client.post(f"/jobs/{jid}/assets/review", json={"decisions": decisions, "reviewer": "Aly"})
        self.client.post(f"/jobs/{jid}/assets/approve", json={"reviewer": "Aly"})
        j = self.wait(jid, "scenes_review")
        return jid, j

    def test_render_settings_reports_the_configured_aspect(self):
        out = self.client.get("/render-settings").json()
        self.assertEqual(out["aspect"], "9:16")   # settings={} in ApiSourcesTests.setUp -> the documented default

    def give_a_scene_a_clip(self, jid, j):
        sid = j["scenes"][0]["id"]
        clip_path = next(a["path"] for a in j["assets"] if a["status"] == "approved")
        r = self.client.patch(f"/jobs/{jid}/scenes", json={"edits": {sid: {"clip_path": clip_path}}, "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        return sid

    def test_sets_a_crop_over_http(self):
        jid, j = self.setup_job_in_scenes_review()
        sid = self.give_a_scene_a_clip(jid, j)
        r = self.client.patch(f"/jobs/{jid}/scenes/{sid}/crop",
                              json={"center_x": 0.25, "center_y": 0.75, "zoom": 1.4, "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        scene = next(s for s in r.json()["scenes"] if s["id"] == sid)
        self.assertEqual(scene["crop"], {"center_x": 0.25, "center_y": 0.75, "zoom": 1.4,
                                          "updated_by": "Aly", "updated_at": scene["crop"]["updated_at"]})

    def test_removes_a_crop_over_http(self):
        jid, j = self.setup_job_in_scenes_review()
        sid = self.give_a_scene_a_clip(jid, j)
        self.client.patch(f"/jobs/{jid}/scenes/{sid}/crop", json={"zoom": 2.0, "reviewer": "Aly"})
        r = self.client.post(f"/jobs/{jid}/scenes/{sid}/crop/remove", json={"reviewer": "Aly"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(next(s for s in r.json()["scenes"] if s["id"] == sid)["crop"])

    def test_crop_without_a_clip_first_is_a_422(self):
        jid, j = self.setup_job_in_scenes_review()
        sid = j["scenes"][0]["id"]   # no clip_path given yet
        r = self.client.patch(f"/jobs/{jid}/scenes/{sid}/crop", json={"reviewer": "Aly"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_out_of_range_center_is_a_422(self):
        jid, j = self.setup_job_in_scenes_review()
        sid = self.give_a_scene_a_clip(jid, j)
        r = self.client.patch(f"/jobs/{jid}/scenes/{sid}/crop", json={"center_x": 1.5, "reviewer": "Aly"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_zoom_below_1_is_a_422(self):
        jid, j = self.setup_job_in_scenes_review()
        sid = self.give_a_scene_a_clip(jid, j)
        r = self.client.patch(f"/jobs/{jid}/scenes/{sid}/crop", json={"zoom": 0.5, "reviewer": "Aly"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_unknown_job_is_404(self):
        r = self.client.patch("/jobs/nope/scenes/whatever/crop", json={"reviewer": "Aly"})
        self.assertEqual(r.status_code, 404, r.text)


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


class TimingEndpointTests(ApiSourcesTests):
    def test_timing_endpoint_reports_stage_durations_and_matches_the_decision_log(self):
        r = self.client.post("/jobs", json={"subject": "cats", "reviewer": "Aly",
                                            "providers": {"keywords": "fake", "scenes": "fake", "render": "fake", "sources": ["commons"]}})
        jid = r.json()["id"]
        self.client.post(f"/jobs/{jid}/start", json={"reviewer": "Aly"})
        j = self.wait(jid, "keywords_review")
        self.client.post(f"/jobs/{jid}/keywords/review", json={"approved_ids": [j["keywords"][0]["id"]], "reviewer": "Aly"})
        self.wait(jid, "assets_review")

        out = self.client.get(f"/jobs/{jid}/timing").json()
        self.assertIn("keywords_running", out["time_per_stage_seconds"])
        self.assertIn("sourcing_running", out["time_per_stage_seconds"])
        self.assertGreaterEqual(out["total_wall_seconds"], 0.0)
        # time waiting on Aly to approve keywords should show up as exactly one wait
        self.assertEqual(len(out["waits"]), 1)

        decisions = self.client.get(f"/jobs/{jid}/decisions").json()["entries"]
        self.assertTrue(any(e["action"] == "stage_started" and e["subject"].get("stage") == "keywords_running" for e in decisions))
        self.assertTrue(any(e["action"] == "stage_finished" and e["subject"].get("stage") == "sourcing_running" for e in decisions))

    def test_timing_endpoint_404s_for_unknown_job(self):
        self.assertEqual(self.client.get("/jobs/does-not-exist/timing").status_code, 404)


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
                self.assertTrue(all(a["vetting"]["scoring_method"] == "llm-semantic" and a["vetting"]["method_version"] == "llm-semantic-v1"
                                     for a in j["assets"]))
                self.assertTrue(all(a["vetting"]["relevance"] == 0.05 for a in j["assets"]))
