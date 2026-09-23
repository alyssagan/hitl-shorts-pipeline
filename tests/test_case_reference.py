"""Case reference sheet + visual checklist (#6): storage and edit API. Everything here is only ever
written by an explicit human action through these methods -- never by sourcing/vetting/scoring -- and is
editable at any point in a job's life, not gated behind a pipeline state."""
import tempfile
import unittest

from starlette.testclient import TestClient

from pipeline.api.app import create_app
from pipeline.core.models import Job, Keyword, ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from tests.fakes import fake_registry

FAKE = ProviderChoice(keywords="fake", scenes="fake", render="fake")


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, self.stages = fake_registry()
        self.store = JobStore(self.tmp.name)
        self.orch = Orchestrator(self.store, reg, {"review": {}})

    async def job(self) -> Job:
        return await self.orch.create_job("The Dyatlov Pass Incident", FAKE)


class CanonicalNameTests(Base):
    async def test_set_and_persists(self):
        j = await self.job()
        j = await self.orch.set_case_canonical_name(j.id, "The Dyatlov Pass Incident", reviewer="Aly")
        self.assertEqual(j.case_reference.canonical_name, "The Dyatlov Pass Incident")
        reloaded = self.orch.get(j.id)
        self.assertEqual(reloaded.case_reference.canonical_name, "The Dyatlov Pass Incident")

    async def test_requires_a_reviewer(self):
        j = await self.job()
        with self.assertRaises(ValueError):
            await self.orch.set_case_canonical_name(j.id, "X", reviewer="")

    async def test_editable_at_any_job_state_not_gated(self):
        # create_job() alone leaves the job at CREATED, well before any review gate -- setting the
        # canonical name must not require any particular state.
        j = await self.job()
        self.assertEqual(j.state.value, "created")
        j = await self.orch.set_case_canonical_name(j.id, "X", reviewer="Aly")
        self.assertEqual(j.case_reference.canonical_name, "X")


class CaseFactTests(Base):
    async def test_add_fact(self):
        j = await self.job()
        j = await self.orch.add_case_fact(j.id, "person", "Corazon Amurao", detail="sole survivor",
                                          source_links=["https://en.wikipedia.org/wiki/Richard_Speck"],
                                          reviewer="Aly")
        self.assertEqual(len(j.case_reference.facts), 1)
        f = j.case_reference.facts[0]
        self.assertEqual((f.kind, f.text, f.detail, f.status, f.added_by), ("person", "Corazon Amurao", "sole survivor", "confirmed", "Aly"))

    async def test_add_fact_rejects_blank_text(self):
        j = await self.job()
        with self.assertRaises(ValueError):
            await self.orch.add_case_fact(j.id, "person", "   ", reviewer="Aly")

    async def test_add_fact_rejects_bad_kind_or_status(self):
        j = await self.job()
        with self.assertRaises(ValueError):
            await self.orch.add_case_fact(j.id, "not-a-kind", "x", reviewer="Aly")
        with self.assertRaises(ValueError):
            await self.orch.add_case_fact(j.id, "person", "x", status="not-a-status", reviewer="Aly")

    async def test_conflicting_fact_keeps_its_note(self):
        j = await self.job()
        j = await self.orch.add_case_fact(j.id, "date", "July 13 or 14, 1966", status="conflicting",
                                          conflict_note="sources disagree on which night", reviewer="Aly")
        f = j.case_reference.facts[0]
        self.assertEqual(f.status, "conflicting")
        self.assertIn("disagree", f.conflict_note)

    async def test_edit_fact_partial_update(self):
        j = await self.job()
        j = await self.orch.add_case_fact(j.id, "person", "Corazon Amurao", reviewer="Aly")
        fid = j.case_reference.facts[0].id
        j = await self.orch.edit_case_fact(j.id, fid, detail="night-shift nursing student", reviewer="Aly")
        f = j.case_reference.facts[0]
        self.assertEqual(f.text, "Corazon Amurao")           # untouched
        self.assertEqual(f.detail, "night-shift nursing student")
        self.assertEqual(f.updated_by, "Aly")
        self.assertTrue(f.updated_at)

    async def test_edit_fact_cannot_blank_out_text(self):
        j = await self.job()
        j = await self.orch.add_case_fact(j.id, "person", "Corazon Amurao", reviewer="Aly")
        fid = j.case_reference.facts[0].id
        with self.assertRaises(ValueError):
            await self.orch.edit_case_fact(j.id, fid, text="   ", reviewer="Aly")

    async def test_edit_unknown_fact_errors(self):
        j = await self.job()
        with self.assertRaises(ValueError):
            await self.orch.edit_case_fact(j.id, "nope", text="x", reviewer="Aly")

    async def test_remove_fact(self):
        j = await self.job()
        j = await self.orch.add_case_fact(j.id, "person", "Corazon Amurao", reviewer="Aly")
        fid = j.case_reference.facts[0].id
        j = await self.orch.remove_case_fact(j.id, fid, reviewer="Aly")
        self.assertEqual(j.case_reference.facts, [])

    async def test_remove_unknown_fact_errors(self):
        j = await self.job()
        with self.assertRaises(ValueError):
            await self.orch.remove_case_fact(j.id, "nope", reviewer="Aly")


class VisualChecklistGenerateTests(Base):
    async def test_generates_draft_items_from_approved_keywords(self):
        j = await self.job()

        def fn(job):
            job.keywords = [
                Keyword(term="corazon amurao interview", group="case", visual_needed="a period photo of Corazon Amurao", approved=True),
                Keyword(term="chicago streets 1960s", group="historical", approved=True),
                Keyword(term="unapproved term", group="stock", approved=False),
            ]
        j = await self.orch._mutate(j.id, fn)
        j = await self.orch.generate_visual_checklist(j.id, reviewer="Aly")
        self.assertEqual(len(j.visual_checklist), 2)   # only approved keywords
        by_term = {i.linked_keyword_term: i for i in j.visual_checklist}
        self.assertEqual(by_term["corazon amurao interview"].label, "a period photo of Corazon Amurao")
        self.assertEqual(by_term["chicago streets 1960s"].label, "chicago streets 1960s")  # falls back to term
        self.assertEqual(by_term["chicago streets 1960s"].group, "historical")
        self.assertTrue(all(i.status == "needed" for i in j.visual_checklist))   # never pre-fulfilled

    async def test_generate_is_idempotent_does_not_duplicate(self):
        j = await self.job()

        def fn(job):
            job.keywords = [Keyword(term="corazon amurao interview", group="case", approved=True)]
        j = await self.orch._mutate(j.id, fn)
        j = await self.orch.generate_visual_checklist(j.id, reviewer="Aly")
        j = await self.orch.generate_visual_checklist(j.id, reviewer="Aly")
        self.assertEqual(len(j.visual_checklist), 1)

    async def test_generate_skips_terms_already_manually_added(self):
        j = await self.job()
        j = await self.orch.add_checklist_item(j.id, "a photo of the mountain", linked_keyword_term="dyatlov pass", reviewer="Aly")

        def fn(job):
            job.keywords = [Keyword(term="dyatlov pass", group="historical", approved=True)]
        j = await self.orch._mutate(j.id, fn)
        j = await self.orch.generate_visual_checklist(j.id, reviewer="Aly")
        self.assertEqual(len(j.visual_checklist), 1)   # not duplicated


class VisualChecklistEditTests(Base):
    async def test_add_manual_item(self):
        j = await self.job()
        j = await self.orch.add_checklist_item(j.id, "a photo of the mountain", group="historical", reviewer="Aly")
        self.assertEqual(len(j.visual_checklist), 1)
        self.assertEqual(j.visual_checklist[0].status, "needed")

    async def test_add_rejects_blank_label_or_bad_group(self):
        j = await self.job()
        with self.assertRaises(ValueError):
            await self.orch.add_checklist_item(j.id, "  ", reviewer="Aly")
        with self.assertRaises(ValueError):
            await self.orch.add_checklist_item(j.id, "x", group="not-a-group", reviewer="Aly")

    async def test_update_status_requires_valid_value(self):
        j = await self.job()
        j = await self.orch.add_checklist_item(j.id, "x", reviewer="Aly")
        iid = j.visual_checklist[0].id
        with self.assertRaises(ValueError):
            await self.orch.update_checklist_item(j.id, iid, status="not-a-status", reviewer="Aly")

    async def test_update_to_fulfilled_requires_a_real_asset_id(self):
        j = await self.job()
        j = await self.orch.add_checklist_item(j.id, "x", reviewer="Aly")
        iid = j.visual_checklist[0].id
        with self.assertRaises(ValueError):
            await self.orch.update_checklist_item(j.id, iid, status="fulfilled", asset_id="does-not-exist", reviewer="Aly")

    async def test_update_note_and_status(self):
        j = await self.job()
        j = await self.orch.add_checklist_item(j.id, "x", reviewer="Aly")
        iid = j.visual_checklist[0].id
        j = await self.orch.update_checklist_item(j.id, iid, status="candidates_found",
                                                    note="found 3 maybe-matches", reviewer="Aly")
        item = j.visual_checklist[0]
        self.assertEqual(item.status, "candidates_found")
        self.assertEqual(item.note, "found 3 maybe-matches")
        self.assertEqual(item.updated_by, "Aly")

    async def test_remove_item(self):
        j = await self.job()
        j = await self.orch.add_checklist_item(j.id, "x", reviewer="Aly")
        iid = j.visual_checklist[0].id
        j = await self.orch.remove_checklist_item(j.id, iid, reviewer="Aly")
        self.assertEqual(j.visual_checklist, [])

    async def test_remove_unknown_item_errors(self):
        j = await self.job()
        with self.assertRaises(ValueError):
            await self.orch.remove_checklist_item(j.id, "nope", reviewer="Aly")


class HttpApiTests(unittest.TestCase):
    """The routes themselves -- request/response wiring, not the orchestrator logic already covered above."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, self.stages = fake_registry()
        app = create_app(Orchestrator(JobStore(self.tmp.name), reg), settings={})
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.jid = self.client.post("/jobs", json={"subject": "cats", "providers": {"keywords": "fake", "scenes": "fake", "render": "fake"}}).json()["id"]

    def test_full_case_reference_round_trip_over_http(self):
        r = self.client.post(f"/jobs/{self.jid}/case-reference", json={"canonical_name": "The Cat Case", "reviewer": "Aly"})
        self.assertEqual(r.json()["case_reference"]["canonical_name"], "The Cat Case")

        r = self.client.post(f"/jobs/{self.jid}/case-reference/facts",
                             json={"kind": "person", "text": "Whiskers", "detail": "the cat", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200)
        fid = r.json()["case_reference"]["facts"][0]["id"]

        r = self.client.patch(f"/jobs/{self.jid}/case-reference/facts/{fid}", json={"detail": "an orange tabby", "reviewer": "Aly"})
        self.assertEqual(r.json()["case_reference"]["facts"][0]["detail"], "an orange tabby")

        r = self.client.post(f"/jobs/{self.jid}/case-reference/facts/{fid}/remove", json={"reviewer": "Aly"})
        self.assertEqual(r.json()["case_reference"]["facts"], [])

    def test_case_reference_missing_reviewer_is_422(self):
        r = self.client.post(f"/jobs/{self.jid}/case-reference", json={"canonical_name": "X"})
        self.assertEqual(r.status_code, 422)

    def test_full_visual_checklist_round_trip_over_http(self):
        r = self.client.post(f"/jobs/{self.jid}/visual-checklist", json={"label": "a photo of a cat", "reviewer": "Aly"})
        self.assertEqual(r.status_code, 200)
        iid = r.json()["visual_checklist"][0]["id"]

        r = self.client.patch(f"/jobs/{self.jid}/visual-checklist/{iid}", json={"status": "candidates_found", "reviewer": "Aly"})
        self.assertEqual(r.json()["visual_checklist"][0]["status"], "candidates_found")

        r = self.client.post(f"/jobs/{self.jid}/visual-checklist/{iid}/remove", json={"reviewer": "Aly"})
        self.assertEqual(r.json()["visual_checklist"], [])

    def test_visual_checklist_generate_over_http(self):
        job = self.client.post(f"/jobs/{self.jid}/start").json()
        # fake registry's keyword stage proposes one keyword -- approve it so generate has something to draft from.
        job = self.client.get(f"/jobs/{self.jid}").json()
        # Poll briefly for keywords_review since start() runs the keyword stage asynchronously.
        import time
        end = time.time() + 5
        while job["state"] != "keywords_review" and time.time() < end:
            time.sleep(0.02)
            job = self.client.get(f"/jobs/{self.jid}").json()
        self.assertEqual(job["state"], "keywords_review")
        kw_id = job["keywords"][0]["id"]
        self.client.post(f"/jobs/{self.jid}/keywords/review", json={"approved_ids": [kw_id]})

        r = self.client.post(f"/jobs/{self.jid}/visual-checklist/generate", json={"reviewer": "Aly"})
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()["visual_checklist"]), 1)

    def test_unknown_fact_or_item_is_422(self):
        self.assertEqual(self.client.patch(f"/jobs/{self.jid}/case-reference/facts/nope", json={"text": "x", "reviewer": "Aly"}).status_code, 422)
        self.assertEqual(self.client.patch(f"/jobs/{self.jid}/visual-checklist/nope", json={"status": "needed", "reviewer": "Aly"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
