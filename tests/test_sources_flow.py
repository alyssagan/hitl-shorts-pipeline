"""Sources, vetting, asset review and the decision log, all offline (httpx MockTransport)."""
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core import state_machine as sm
from pipeline.core.decisions import DecisionLog, human
from pipeline.core.models import Asset, Job, JobState as S, ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.sources.commons import CommonsSource
from pipeline.sources.folder import FolderSource
from pipeline.sources.pexels import PexelsSource
from pipeline.sources.wikipedia import WikipediaSource
from pipeline.vetting.rules import vet_asset
from tests.fakes import fake_registry

IMG = b"\xff\xd8\xff-fake-jpeg-"


def handler(request: httpx.Request) -> httpx.Response:
    u, p = str(request.url), request.url.params
    host = request.url.host
    if host == "en.wikipedia.org":
        if p.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": "Cat"}]}})
        return httpx.Response(200, json={"query": {"pages": {"1": {"extract": "Cats are small mammals.", "fullurl": "https://en.wikipedia.org/wiki/Cat"}}}})
    if host == "commons.wikimedia.org":
        def page(i, title, lic, w=1280, h=1280, desc="", mime="image/jpeg"):
            return {"index": i, "title": f"File:{title}.jpg", "imageinfo": [{
                "thumburl": f"https://upload.wikimedia.org/t{i}.jpg", "thumbwidth": w, "thumbheight": h, "url": f"https://upload.wikimedia.org/o{i}.jpg",
                "width": w, "height": h, "mime": mime, "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}.jpg",
                "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<a>Ann</a>"}, "ImageDescription": {"value": desc}}}]}
        return httpx.Response(200, json={"query": {"pages": {
            "a": page(1, "Cat sleeping", "CC BY-SA 4.0"),
            "b": page(2, "Cat photo", "CC BY-NC 2.0"),
            "c": page(3, "Tiny cat", "CC0", w=200, h=200),
            "d": page(4, "Cat svg", "CC0", mime="image/svg+xml"),
        }}})
    if host == "api.pexels.com":
        if "/videos/" in u:
            return httpx.Response(200, json={"videos": []})
        return httpx.Response(200, json={"photos": [{"id": 9, "width": 1080, "height": 1920, "url": "https://www.pexels.com/photo/tabby-cat-9/",
                                                      "photographer": "Bo", "alt": "A tabby cat", "src": {"large2x": "https://images.pexels.com/9.jpg"}}]})
    if host in ("upload.wikimedia.org", "images.pexels.com"):
        return httpx.Response(200, content=IMG + u.encode())
    return httpx.Response(404)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make(self, keys=True):
        reg, self.stages = fake_registry()
        reg.source_transport = httpx.MockTransport(handler)
        reg.register_source("wikipedia", lambda: WikipediaSource())
        reg.register_source("commons", lambda: CommonsSource(per_query=5))
        reg.register_source("pexels", lambda: PexelsSource(api_key="k" if keys else "", videos=True))
        self.scraped = self.root / "scraped"
        (self.scraped / "sub").mkdir(parents=True)
        (self.scraped / "sub" / "x.jpg").write_bytes(IMG + b"folder")
        reg.register_source("folder", lambda: FolderSource(str(self.scraped)))
        self.store = JobStore(self.root / "projects")
        return Orchestrator(self.store, reg, {"review": {}})

    async def to_assets_review(self, orch, sources=("wikipedia", "commons", "pexels", "folder")):
        job = await orch.create_job("Cute cats!", ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=list(sources)))
        await orch.start(job.id)
        job = await orch.run_pending(job.id)
        job = await orch.review_keywords(job.id, [job.keywords[0].id], reviewer="Aly")
        self.assertEqual(job.state, S.SOURCING_RUNNING)
        return await orch.run_pending(job.id)


class VettingTests(unittest.TestCase):
    def a(self, **kw):
        base = dict(source="x", kind="image", path="p", rel_path="p", source_url="https://x/1", page_url="https://x/p", width=1000, height=1000, sha256="h")
        base.update(kw)
        return Asset(**base)

    def rules(self, a):
        return {f.rule: f for f in vet_asset(a, [a]).flags}

    def test_license_risk_levels_with_reasons(self):
        self.assertEqual(vet_asset(self.a(license="CC BY-NC 2.0"), []).risk, "high")
        self.assertEqual(vet_asset(self.a(license=""), []).risk, "high")
        self.assertEqual(vet_asset(self.a(license="CC BY-SA 4.0", author="A"), []).risk, "medium")
        self.assertEqual(vet_asset(self.a(license="CC BY 4.0", author="A"), []).risk, "low")
        v = vet_asset(self.a(license="CC0"), [])
        self.assertEqual(v.risk, "low")
        self.assertIn("not an approval", v.summary)
        v = vet_asset(self.a(license="CC BY-NC 2.0"), [])
        self.assertIn("LIC_NC", v.summary)
        self.assertTrue(all(f.message and f.evidence for f in v.flags))

    def test_content_and_people_flags_explain_evidence(self):
        r = self.rules(self.a(license="CC0", title="Child at a crime scene"))
        self.assertEqual(r["PEOPLE_MINOR"].severity, "high")
        self.assertIn("child", r["PEOPLE_MINOR"].evidence)
        self.assertIn("CONTENT_SENSITIVE", r)

    def test_low_res_is_unusable_and_flagged(self):
        v = vet_asset(self.a(license="CC0", width=300, height=300), [])
        self.assertFalse(v.usable)
        self.assertEqual(v.risk, "high")

    def test_duplicate_flag(self):
        first, second = self.a(id="a1", license="CC0"), self.a(id="a2", license="CC0")
        self.assertIn("DUPLICATE", {f.rule for f in vet_asset(second, [first, second]).flags})


class DecisionLogTests(unittest.TestCase):
    def test_chain_verify_and_tamper_detection_and_markdown(self):
        with tempfile.TemporaryDirectory() as t:
            log = DecisionLog(t)
            log.record(job_id="j", stage="keywords", action="a", actor=human("Aly"), decision="approve", reason="because")
            log.record(job_id="j", stage="assets", action="b", actor=human("Aly"), logic={"why": "x"})
            self.assertEqual(log.verify(), (True, None))
            self.assertIn("because", (Path(t) / "DECISIONS.md").read_text())
            lines = (Path(t) / "decisions.jsonl").read_text().splitlines()
            lines[0] = lines[0].replace("because", "changed")
            (Path(t) / "decisions.jsonl").write_text("\n".join(lines) + "\n")
            self.assertEqual(log.verify(), (False, 0))


class SourceAdapterTests(Base):
    async def test_layout_logs_and_flags(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        self.assertEqual(job.state, S.ASSETS_REVIEW)
        d = self.store.job_dir(job.id)
        self.assertTrue(d.name.startswith("cute-cats"))
        for name in ("wikipedia", "commons", "pexels", "folder"):
            self.assertTrue((d / "sources" / name / "manifest.json").exists(), name)
        for name in ("wikipedia", "commons", "pexels"):
            reqs = [json.loads(l) for l in (d / "sources" / name / "requests.jsonl").read_text().splitlines()]
            self.assertTrue(reqs and all(r["url"] and r["purpose"] for r in reqs))
        # svg skipped, everything else kept: 3 commons + 1 pexels + 1 folder
        by_src = {}
        for a in job.assets:
            by_src.setdefault(a.source, []).append(a)
        self.assertEqual(len(by_src["commons"]), 3)
        self.assertEqual(len(by_src["pexels"]), 1)
        self.assertEqual(len(by_src["folder"]), 1)
        self.assertEqual(len(job.references), 1)
        for a in job.assets:
            self.assertIn(f"sources/{a.source}/files/", a.rel_path)
            self.assertIsNotNone(a.vetting)
            self.assertEqual(a.status, "pending")
        risks = {a.title: a.vetting.risk for a in job.assets if a.source == "commons"}
        self.assertEqual(risks["Cat photo"], "high")       # NC
        self.assertEqual(risks["Cat sleeping"], "medium")  # SA
        self.assertEqual(risks["Tiny cat"], "high")        # low res
        self.assertEqual(by_src["folder"][0].vetting.risk, "high")   # no license
        # Nothing was filtered out for risk.
        self.assertTrue(any(not a.vetting.usable for a in job.assets))

    async def test_missing_pexels_key_is_logged_not_fatal(self):
        orch = self.make(keys=False)
        job = await self.to_assets_review(orch, sources=("commons", "pexels"))
        self.assertEqual(job.state, S.ASSETS_REVIEW)
        ents = [e for e in self.store.decisions(job.id).entries() if e["action"] == "searched_source" and e["decision"] == "skipped"]
        self.assertTrue(ents)
        self.assertIn("PEXELS_API_KEY", json.dumps(ents))


class FlowTests(Base):
    async def test_full_flow_with_gate_rules_and_log(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        cid = {a.title: a.id for a in job.assets if a.source == "commons"}

        # Guard: can't approve the pool while assets are undecided.
        with self.assertRaises(sm.TransitionError):
            await orch.approve_assets(job.id, reviewer="Aly")
        # Reviewer name required.
        with self.assertRaises(ValueError):
            await orch.review_assets(job.id, {cid["Cat sleeping"]: {"decision": "approve"}})
        # High-risk approval needs a note.
        with self.assertRaises(ValueError):
            await orch.review_assets(job.id, {cid["Cat photo"]: {"decision": "approve"}}, reviewer="Aly")
        # Unusable (too small) can't be approved at all.
        with self.assertRaises(ValueError):
            await orch.review_assets(job.id, {cid["Tiny cat"]: {"decision": "approve", "note": "ok"}}, reviewer="Aly")

        decisions = {}
        for a in job.assets:
            if a.title == "Cat photo":
                decisions[a.id] = {"decision": "approve", "note": "Editorial use, personal channel, not monetized"}
            elif a.vetting.usable and a.title != "Cat sleeping":
                decisions[a.id] = {"decision": "approve"} if a.vetting.risk != "high" else {"decision": "reject", "note": "no license"}
            else:
                decisions[a.id] = {"decision": "reject", "note": "too small or SA"}
        job = await orch.review_assets(job.id, decisions, reviewer="Aly")
        job = await orch.approve_assets(job.id, reviewer="Aly")
        self.assertEqual(job.state, S.SCENES_RUNNING)

        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SCENES_REVIEW)
        job = await orch.approve_scenes(job.id, reviewer="Aly")
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.COMPLETED)

        d = self.store.job_dir(job.id)
        credits = (d / "CREDITS.md").read_text()
        self.assertIn("Cat photo", credits)
        self.assertNotIn("Tiny cat", credits)

        log = self.store.decisions(job.id)
        self.assertEqual(log.verify(), (True, None))
        ents = log.entries()
        actions = [e["action"] for e in ents]
        for needed in ("vetted_asset", "asset_reviewed", "approved_asset_pool", "render_completed"):
            self.assertIn(needed, actions)
        types = {e["actor"]["type"] for e in ents}
        self.assertIn("human", types)
        self.assertIn("machine", types)
        hi = [e for e in ents if e["action"] == "asset_reviewed" and e["subject"].get("title") == "Cat photo"][0]
        self.assertTrue(hi["logic"]["high_risk_acknowledged"])
        self.assertIn("LIC_NC:high", hi["logic"]["flags_shown_to_reviewer"])
        md = (d / "DECISIONS.md").read_text()
        self.assertIn("Aly", md)

    async def test_reject_assets_goes_back_to_sourcing_with_new_queries(self):
        orch = self.make()
        job = await self.to_assets_review(orch, sources=("commons",))
        job = await orch.reject_assets(job.id, "not what I want", ["kitten"], reviewer="Aly")
        self.assertEqual(job.state, S.SOURCING_RUNNING)
        self.assertIn("kitten", job.providers.options["extra_queries"])
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.ASSETS_REVIEW)
        # Same files are not downloaded twice.
        self.assertEqual(len({a.source_url for a in job.assets}), len(job.assets))

    async def test_scene_edit_cannot_use_unapproved_clip(self):
        orch = self.make()
        job = await self.to_assets_review(orch, sources=("pexels",))
        pid = job.assets[0].id
        other = "/tmp/not-approved.jpg"
        job = await orch.review_assets(job.id, {pid: {"decision": "approve"}}, reviewer="Aly")
        job = await orch.approve_assets(job.id, reviewer="Aly")
        job = await orch.run_pending(job.id)
        with self.assertRaises(Exception):
            await orch.edit_scenes(job.id, edits={job.scenes[0].id: {"clip_path": other}})


if __name__ == "__main__":
    unittest.main()
