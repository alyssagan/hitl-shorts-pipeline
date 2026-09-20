import asyncio
import tempfile
import unittest

from pipeline.core import state_machine as sm
from pipeline.core.models import JobState as S, ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from tests.fakes import Boom, FakeKeywords, FakeRender, FakeScenes, Slow, fake_registry

FAKE = ProviderChoice(keywords="fake", scenes="fake", render="fake")


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def make(self, **stages):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, self.stages = fake_registry(**stages)
        self.store = JobStore(self.tmp.name)
        return Orchestrator(self.store, reg)

    async def to_keywords_review(self, orch):
        job = await orch.create_job("cats", FAKE)
        await orch.start(job.id)
        return await orch.run_pending(job.id)

    async def test_full_human_in_the_loop_flow(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        self.assertEqual(job.state, S.KEYWORDS_REVIEW)
        self.assertEqual(len(job.keywords), 3)

        # Human approves two keywords and adds their own.
        job = await orch.review_keywords(job.id, [job.keywords[0].id, job.keywords[2].id], extra_terms=["my own"])
        self.assertEqual(job.state, S.SCENES_RUNNING)
        self.assertEqual([k.term for k in job.approved_keywords], ["kw1", "kw3", "my own"])

        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SCENES_REVIEW)
        # Scene stage only ever sees approved keywords.
        self.assertEqual(self.stages["scenes"].calls[0][0], ["kw1", "kw3", "my own"])

        # Human reorders scenes, edits narration, then approves.
        ids = [s.id for s in job.scenes]
        job = await orch.edit_scenes(job.id, order=[ids[2], ids[0], ids[1]], edits={ids[0]: {"narration": "edited"}})
        job = await orch.approve_scenes(job.id)
        self.assertEqual(job.state, S.RENDERING)

        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.COMPLETED)
        # Renderer received the human's order and edit.
        self.assertEqual(self.stages["render"].orders[0], ["scene 2", "edited", "scene 1"])
        self.assertTrue(job.output_path.endswith("final.mp4"))

    async def test_nothing_runs_at_a_human_gate(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        again = await orch.run_pending(job.id)          # no-op at a review state
        self.assertEqual(again.state, S.KEYWORDS_REVIEW)
        self.assertEqual(len(self.stages["keywords"].calls), 1)

    async def test_keyword_rejection_feeds_feedback_into_rerun(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        job = await orch.reject_keywords(job.id, "too generic")
        self.assertEqual(job.state, S.KEYWORDS_RUNNING)
        self.assertEqual(job.keywords, [])
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.KEYWORDS_REVIEW)
        self.assertEqual(self.stages["keywords"].calls[-1], ["too generic"])

    async def test_scene_rejection_feeds_feedback_and_keeps_keywords(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        job = await orch.review_keywords(job.id, [job.keywords[0].id])
        job = await orch.run_pending(job.id)
        job = await orch.reject_scenes(job.id, "make it punchier")
        self.assertEqual(job.state, S.SCENES_RUNNING)
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SCENES_REVIEW)
        self.assertEqual(self.stages["scenes"].calls[-1], (["kw1"], ["make it punchier"]))

    async def test_approving_zero_keywords_is_refused_and_state_is_unchanged(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        with self.assertRaises(sm.TransitionError):
            await orch.review_keywords(job.id, [])
        self.assertEqual(orch.get(job.id).state, S.KEYWORDS_REVIEW)

    async def test_unknown_keyword_id_rejected(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        with self.assertRaises(ValueError):
            await orch.review_keywords(job.id, ["nope"])
        self.assertFalse(any(k.approved for k in orch.get(job.id).keywords))

    async def test_edit_scenes_validates_order_and_state(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        with self.assertRaises(sm.TransitionError):       # wrong state
            await orch.edit_scenes(job.id, order=[])
        job = await orch.review_keywords(job.id, [job.keywords[0].id])
        job = await orch.run_pending(job.id)
        with self.assertRaises(ValueError):               # incomplete order
            await orch.edit_scenes(job.id, order=[job.scenes[0].id])
        with self.assertRaises(ValueError):               # unknown scene
            await orch.edit_scenes(job.id, edits={"zzz": {"narration": "x"}})

    async def test_stage_failure_lands_in_failed_and_retry_resumes(self):
        orch = self.make(keywords=Boom(FakeKeywords(), failures=1))
        job = await orch.create_job("cats", FAKE)
        await orch.start(job.id)
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.FAILED)
        self.assertIn("provider exploded", job.error)
        job = await orch.retry(job.id)
        self.assertEqual(job.state, S.KEYWORDS_RUNNING)
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.KEYWORDS_REVIEW)
        self.assertIsNone(job.error)

    async def test_cancel_while_stage_running_discards_result(self):
        orch = self.make(keywords=Slow(FakeKeywords(), 0.15))
        job = await orch.create_job("cats", FAKE)
        await orch.start(job.id)
        task = asyncio.create_task(orch.run_pending(job.id))
        await asyncio.sleep(0.03)
        await orch.cancel(job.id)
        await task
        final = orch.get(job.id)
        self.assertEqual(final.state, S.CANCELLED)
        self.assertEqual(final.keywords, [])

    async def test_state_survives_restart_and_resume_all_finds_running_jobs(self):
        orch = self.make()
        job = await orch.create_job("cats", FAKE)
        await orch.start(job.id)                          # now KEYWORDS_RUNNING, nothing executed yet
        started = []
        orch2 = Orchestrator(JobStore(self.tmp.name), fake_registry()[0])   # "restarted" process
        orch2.on_running = started.append
        self.assertEqual(orch2.resume_all(), [job.id])
        self.assertEqual(started, [job.id])
        self.assertEqual((await orch2.run_pending(job.id)).state, S.KEYWORDS_REVIEW)

    async def test_back_to_keywords_from_scene_review(self):
        orch = self.make()
        job = await self.to_keywords_review(orch)
        job = await orch.review_keywords(job.id, [job.keywords[0].id])
        job = await orch.run_pending(job.id)
        job = await orch.back_to_keywords(job.id)
        self.assertEqual(job.state, S.KEYWORDS_REVIEW)

    async def test_concurrent_run_pending_runs_stage_once(self):
        orch = self.make(keywords=Slow(FakeKeywords(), 0.1))
        job = await orch.create_job("cats", FAKE)
        await orch.start(job.id)
        await asyncio.gather(orch.run_pending(job.id), orch.run_pending(job.id))
        self.assertEqual(len(self.stages["keywords"].inner.calls), 1)

    async def test_empty_subject_rejected(self):
        with self.assertRaises(ValueError):
            await self.make().create_job("   ")


if __name__ == "__main__":
    unittest.main()
