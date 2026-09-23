"""keywords_proposed.json / keywords_approved.json, always written per job (docs/RUNNING.md "Keyword files,
always"), and the optional reusable library copy at [library] keywords_dir -- see
pipeline/core/orchestrator.py's _write_keywords_proposed_file/_write_keywords_approved_file, and
scripts/poc.py's choose_library_set/edit_keywords_draft/resolve_keywords_source for the CLI side."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.core.models import ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from tests.fakes import fake_registry

FAKE = ProviderChoice(keywords="fake", scenes="fake", render="fake")


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make(self, *, library_dir: Path | None = None):
        reg, self.stages = fake_registry()
        self.store = JobStore(self.root / "projects")
        settings = {"library": {"keywords_dir": str(library_dir)}} if library_dir else {}
        return Orchestrator(self.store, reg, settings)

    async def to_keywords_review(self, orch):
        job = await orch.create_job("cats", FAKE, reviewer="Aly")
        await orch.start(job.id)
        return await orch.run_pending(job.id)


class KeywordsProposedFileTests(Base):
    async def test_written_after_the_keyword_stage_runs(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        path = orch.store.job_dir(job.id) / "keywords_proposed.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["provider"], "fake")
        self.assertEqual([k["term"] for k in data["keywords"]], ["kw1", "kw2", "kw3"])
        self.assertIn("generated_at", data)

    async def test_rewritten_on_a_second_round_after_rejection(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        job = await orch.reject_keywords(job.id, "too generic")
        job = await orch.run_pending(job.id)
        path = orch.store.job_dir(job.id) / "keywords_proposed.json"
        data = json.loads(path.read_text())
        # FakeKeywords always returns kw1..kw3 regardless of feedback -- the point here is just that the file
        # still exists and reflects the latest run, not a stale first-round copy.
        self.assertEqual([k["term"] for k in data["keywords"]], ["kw1", "kw2", "kw3"])


class KeywordsApprovedFileTests(Base):
    async def test_written_after_gate_1_approval_with_added_and_omitted_terms(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        job = await orch.review_keywords(job.id, [job.keywords[0].id], extra_terms=["my own term"], reviewer="Aly", note="good set")
        path = orch.store.job_dir(job.id) / "keywords_approved.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["keywords"], ["kw1", "my own term"])
        self.assertEqual(data["added_by_human"], ["my own term"])
        self.assertEqual(data["rejected_by_omission"], ["kw2", "kw3"])
        self.assertEqual(data["reviewer"], "Aly")
        self.assertEqual(data["note"], "good set")
        self.assertEqual(data["job_id"], job.id)
        self.assertEqual(data["subject"], "cats")

    async def test_not_written_if_approval_is_refused(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        with self.assertRaises(Exception):
            await orch.review_keywords(job.id, [])          # zero approved -> refused
        path = orch.store.job_dir(job.id) / "keywords_approved.json"
        self.assertFalse(path.exists())


class KeywordsLibraryCopyTests(Base):
    async def test_off_by_default_no_settings_configured(self):
        orch = self.make()
        self.assertIsNone(orch.keywords_library_dir)
        job = await self.to_keywords_review(orch)
        await orch.review_keywords(job.id, [job.keywords[0].id], reviewer="Aly")
        # Nothing written anywhere outside this job's own sandboxed project folder.
        self.assertFalse((self.root / "library").exists())

    async def test_copied_into_a_per_subject_library_when_configured(self):
        lib = self.root / "keywords_lib"
        orch = self.make(library_dir=lib)
        job = await self.to_keywords_review(orch)
        job = await orch.review_keywords(job.id, [job.keywords[0].id], reviewer="Aly")
        copy_path = lib / job.slug / f"{job.id}.json"
        self.assertTrue(copy_path.exists())
        data = json.loads(copy_path.read_text())
        self.assertEqual(data["keywords"], ["kw1"])
        self.assertEqual(data["job_id"], job.id)
        # Same content as the job's own file.
        own = json.loads((orch.store.job_dir(job.id) / "keywords_approved.json").read_text())
        self.assertEqual(data, own)

    async def test_a_second_job_on_the_same_subject_gets_its_own_file_not_an_overwrite(self):
        lib = self.root / "keywords_lib"
        orch = self.make(library_dir=lib)
        job1 = await self.to_keywords_review(orch)
        job1 = await orch.review_keywords(job1.id, [job1.keywords[0].id], reviewer="Aly")

        job2 = await orch.create_job("cats", FAKE, reviewer="Aly")     # same subject/slug as job1
        await orch.start(job2.id)
        job2 = await orch.run_pending(job2.id)
        job2 = await orch.review_keywords(job2.id, [job2.keywords[1].id], reviewer="Aly")

        self.assertNotEqual(job1.id, job2.id)
        entries = sorted(p.name for p in (lib / "cats").glob("*.json"))
        self.assertEqual(entries, sorted([f"{job1.id}.json", f"{job2.id}.json"]))
        # job1's own saved set is untouched by job2 existing.
        first = json.loads((lib / "cats" / f"{job1.id}.json").read_text())
        self.assertEqual(first["keywords"], ["kw1"])
        second = json.loads((lib / "cats" / f"{job2.id}.json").read_text())
        self.assertEqual(second["keywords"], ["kw2"])


if __name__ == "__main__":
    unittest.main()
