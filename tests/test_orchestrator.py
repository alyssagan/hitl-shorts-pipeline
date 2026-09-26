import asyncio
import tempfile
import unittest

from pipeline.core import state_machine as sm
from pipeline.core.models import Asset, Job, JobState as S, Keyword, ProviderChoice, Scene
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from tests.fakes import Boom, FakeKeywords, FakeRender, FakeScenes, Slow, fake_registry

FAKE = ProviderChoice(keywords="fake", scenes="fake", render="fake")
FAKE_MANUAL_KEYWORDS = ProviderChoice(keywords="fake", scenes="fake", render="fake",
                                       options={"auto_approve_keywords": False})


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def make(self, **stages):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, self.stages = fake_registry(**stages)
        self.store = JobStore(self.tmp.name)
        return Orchestrator(self.store, reg)

    async def to_script_review(self, orch, providers=FAKE):
        job = await orch.create_job("cats", providers)
        await orch.start(job.id)
        return await orch.run_pending(job.id)          # SCRIPT_RUNNING -> SCRIPT_REVIEW

    async def to_keywords_running(self, orch, providers=FAKE):
        job = await self.to_script_review(orch, providers)
        return await orch.approve_script(job.id, reviewer="tester")   # -> KEYWORDS_RUNNING

    async def to_scenes_running(self, orch, providers=FAKE):
        """FAKE has no sources, so auto-approving keywords (the default) skips straight past
        sourcing/vetting/asset review to SCENES_RUNNING -- this is the normal, no-human-gate path."""
        job = await self.to_keywords_running(orch, providers)
        return await orch.run_pending(job.id)

    async def test_full_human_in_the_loop_flow(self):
        orch = self.make()
        job = await self.to_script_review(orch)
        self.assertEqual(job.state, S.SCRIPT_REVIEW)
        self.assertTrue(job.script)
        self.assertEqual(len(job.scenes), 3)

        # Gate 1: human approves the narration as-is.
        job = await orch.approve_script(job.id, reviewer="tester")
        self.assertEqual(job.state, S.KEYWORDS_RUNNING)

        # Keywords are derived from the script and, by default, auto-approved straight through --
        # folded into gate 2 rather than a screen of their own (docs/PIPELINE_STAGES.md).
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SCENES_RUNNING)
        self.assertTrue(all(k.approved for k in job.keywords))
        # Every scene ended up with a search term of its own (Keyword.scene_index -> Scene.search_terms).
        self.assertEqual(sorted({t for s in job.scenes for t in s.search_terms}), ["kw1", "kw2", "kw3"])

        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SCENES_REVIEW)
        self.assertEqual(self.stages["scenes"].calls[0][0], ["kw1", "kw2", "kw3"])

        # Human reorders scenes, edits narration and a private note, then approves.
        ids = [s.id for s in job.scenes]
        job = await orch.edit_scenes(job.id, order=[ids[2], ids[0], ids[1]],
                                     edits={ids[0]: {"narration": "edited", "note": "double-check this date"}})
        self.assertEqual(next(s for s in job.scenes if s.id == ids[0]).note, "double-check this date")
        job = await orch.approve_scenes(job.id)
        self.assertEqual(job.state, S.RENDERING)

        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.COMPLETED)
        # Renderer received the human's order and edit.
        self.assertEqual(self.stages["render"].orders[0], ["scene 2", "edited", "scene 1"])
        self.assertTrue(job.output_path.endswith("final.mp4"))

    async def test_manual_keyword_gate_still_works_when_auto_approve_is_off(self):
        """Decision 2026-09-25: auto-approve is the default, but flipping one option is meant to be
        enough to pivot back to a real, human-facing 4th gate -- KEYWORDS_REVIEW/review_keywords()/
        reject_keywords() themselves needed zero changes for this."""
        orch = self.make()
        job = await self.to_keywords_running(orch, FAKE_MANUAL_KEYWORDS)
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.KEYWORDS_REVIEW)
        self.assertFalse(any(k.approved for k in job.keywords))

        job = await orch.review_keywords(job.id, [job.keywords[0].id, job.keywords[2].id], extra_terms=["my own"])
        self.assertEqual(job.state, S.SCENES_RUNNING)
        self.assertEqual([k.term for k in job.approved_keywords], ["kw1", "kw3", "my own"])

    async def test_nothing_runs_at_a_human_gate(self):
        orch = self.make()
        job = await self.to_script_review(orch)
        again = await orch.run_pending(job.id)          # no-op at a review state
        self.assertEqual(again.state, S.SCRIPT_REVIEW)
        self.assertEqual(len(self.stages["scenes"].script_calls), 1)

    async def test_script_rejection_feeds_feedback_into_rerun(self):
        orch = self.make()
        job = await self.to_script_review(orch)
        job = await orch.reject_script(job.id, "too dry")
        self.assertEqual(job.state, S.SCRIPT_RUNNING)
        self.assertEqual(job.script, "")
        self.assertEqual(job.scenes, [])
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SCRIPT_REVIEW)
        self.assertEqual(self.stages["scenes"].script_calls[-1], ["too dry"])

    async def test_approve_script_can_replace_the_draft_before_approving(self):
        orch = self.make()
        job = await self.to_script_review(orch)
        job = await orch.approve_script(job.id, reviewer="tester", edited_script="Rewritten.\n\nStill here.")
        self.assertEqual(job.state, S.KEYWORDS_RUNNING)
        self.assertEqual(job.script, "Rewritten.\n\nStill here.")
        self.assertEqual([s.narration for s in job.scenes], ["Rewritten.", "Still here."])

    async def test_keyword_rejection_feeds_feedback_into_rerun(self):
        orch = self.make()
        job = await self.to_keywords_running(orch, FAKE_MANUAL_KEYWORDS)
        job = await orch.run_pending(job.id)
        job = await orch.reject_keywords(job.id, "too generic")
        self.assertEqual(job.state, S.KEYWORDS_RUNNING)
        self.assertEqual(job.keywords, [])
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.KEYWORDS_REVIEW)
        self.assertEqual(self.stages["keywords"].calls[-1], ["too generic"])

    async def test_scene_rejection_feeds_feedback_and_keeps_script(self):
        orch = self.make()
        job = await self.to_scenes_running(orch)
        script_before = job.script
        job = await orch.run_pending(job.id)
        job = await orch.reject_scenes(job.id, "make it punchier")
        self.assertEqual(job.state, S.SCENES_RUNNING)
        self.assertEqual(job.script, script_before)          # scene rejection no longer touches the script
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.SCENES_REVIEW)
        self.assertEqual(self.stages["scenes"].calls[-1][1], ["make it punchier"])

    async def test_approving_zero_keywords_is_refused_and_state_is_unchanged(self):
        orch = self.make()
        job = await self.to_keywords_running(orch, FAKE_MANUAL_KEYWORDS)
        job = await orch.run_pending(job.id)
        with self.assertRaises(sm.TransitionError):
            await orch.review_keywords(job.id, [])
        self.assertEqual(orch.get(job.id).state, S.KEYWORDS_REVIEW)

    async def test_unknown_keyword_id_rejected(self):
        orch = self.make()
        job = await self.to_keywords_running(orch, FAKE_MANUAL_KEYWORDS)
        job = await orch.run_pending(job.id)
        with self.assertRaises(ValueError):
            await orch.review_keywords(job.id, ["nope"])
        self.assertFalse(any(k.approved for k in orch.get(job.id).keywords))

    async def test_edit_scenes_validates_order_and_state(self):
        orch = self.make()
        job = await self.to_keywords_running(orch)
        with self.assertRaises(sm.TransitionError):       # wrong state (still KEYWORDS_RUNNING)
            await orch.edit_scenes(job.id, order=[])
        job = await orch.run_pending(job.id)              # -> SCENES_RUNNING (auto-approved keywords)
        job = await orch.run_pending(job.id)               # -> SCENES_REVIEW
        with self.assertRaises(ValueError):               # incomplete order
            await orch.edit_scenes(job.id, order=[job.scenes[0].id])
        with self.assertRaises(ValueError):               # unknown scene
            await orch.edit_scenes(job.id, edits={"zzz": {"narration": "x"}})

    async def test_stage_failure_lands_in_failed_and_retry_resumes(self):
        orch = self.make(keywords=Boom(FakeKeywords(), failures=1))
        job = await self.to_keywords_running(orch)
        job = await orch.run_pending(job.id)
        self.assertEqual(job.state, S.FAILED)
        self.assertIn("provider exploded", job.error)
        job = await orch.retry(job.id)
        self.assertEqual(job.state, S.KEYWORDS_RUNNING)
        job = await orch.run_pending(job.id)
        # auto-approved by default -> skips straight through KEYWORDS_REVIEW to SCENES_RUNNING
        self.assertEqual(job.state, S.SCENES_RUNNING)
        self.assertIsNone(job.error)

    async def test_cancel_while_stage_running_discards_result(self):
        orch = self.make(keywords=Slow(FakeKeywords(), 0.15))
        job = await self.to_keywords_running(orch)
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
        await orch.start(job.id)                          # now SCRIPT_RUNNING, nothing executed yet
        started = []
        orch2 = Orchestrator(JobStore(self.tmp.name), fake_registry()[0])   # "restarted" process
        orch2.on_running = started.append
        self.assertEqual(orch2.resume_all(), [job.id])
        self.assertEqual(started, [job.id])
        self.assertEqual((await orch2.run_pending(job.id)).state, S.SCRIPT_REVIEW)

    async def test_back_to_keywords_from_scene_review(self):
        orch = self.make()
        job = await self.to_scenes_running(orch)
        job = await orch.run_pending(job.id)
        job = await orch.back_to_keywords(job.id)
        self.assertEqual(job.state, S.KEYWORDS_REVIEW)

    async def test_concurrent_run_pending_runs_stage_once(self):
        orch = self.make(keywords=Slow(FakeKeywords(), 0.1))
        job = await self.to_keywords_running(orch)
        await asyncio.gather(orch.run_pending(job.id), orch.run_pending(job.id))
        self.assertEqual(len(self.stages["keywords"].inner.calls), 1)

    async def test_empty_subject_rejected(self):
        with self.assertRaises(ValueError):
            await self.make().create_job("   ")


class ApplyKeywordTermsToScenesTests(unittest.TestCase):
    """docs/ROADMAP.md backlog item F: Scene.search_terms must end up as an ORDERED shot list -- a
    keyword's own most specific term first, then its broader alternatives -- since
    pipeline/stages/scenes/clips.py's AssetClipSource.fetch() now tries them in that order."""

    def make_orch(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, _ = fake_registry()
        return Orchestrator(JobStore(self.tmp.name), reg)

    def test_scene_search_terms_is_term_then_alternatives_in_order(self):
        orch = self.make_orch()
        job = Job(subject="dyatlov pass")
        job.scenes = [Scene(index=0, narration="scene 0")]
        job.keywords = [Keyword(term="specific shot", alternatives=["broader shot", "broadest shot"],
                                scene_index=0, approved=True)]
        orch._apply_keyword_terms_to_scenes(job)
        self.assertEqual(job.scenes[0].search_terms, ["specific shot", "broader shot", "broadest shot"])

    def test_dedups_a_term_repeated_across_two_keywords_for_the_same_scene(self):
        orch = self.make_orch()
        job = Job(subject="dyatlov pass")
        job.scenes = [Scene(index=0, narration="scene 0")]
        job.keywords = [Keyword(term="specific shot", alternatives=["shared fallback"], scene_index=0, approved=True),
                        Keyword(term="shared fallback", scene_index=0, approved=True, source="human")]
        orch._apply_keyword_terms_to_scenes(job)
        # "shared fallback" only appears once, at the position it first appeared (as the alternative).
        self.assertEqual(job.scenes[0].search_terms, ["specific shot", "shared fallback"])

    def test_scene_with_no_scene_specific_keyword_falls_back_to_every_approved_shot_list(self):
        orch = self.make_orch()
        job = Job(subject="dyatlov pass")
        job.scenes = [Scene(index=0, narration="scene 0"), Scene(index=1, narration="scene 1")]
        job.keywords = [Keyword(term="scene 0 specific", alternatives=["scene 0 broad"], scene_index=0, approved=True),
                        Keyword(term="manual extra", approved=True, source="human")]   # no scene_index
        orch._apply_keyword_terms_to_scenes(job)
        self.assertEqual(job.scenes[0].search_terms, ["scene 0 specific", "scene 0 broad"])
        self.assertEqual(job.scenes[1].search_terms, ["scene 0 specific", "scene 0 broad", "manual extra"])


class ScenesNeedingFallbackTests(unittest.TestCase):
    """docs/ROADMAP.md "two-pass hybrid": Pass 2's own trigger logic, unit-tested directly against
    Orchestrator._scenes_needing_fallback()/_next_fallback_query() -- pure functions of the job, no
    store or state-machine plumbing needed."""

    def make_orch(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, _ = fake_registry()
        return Orchestrator(JobStore(self.tmp.name), reg)

    def _job(self, *, covered_scene_has_asset=True):
        # Deliberately disjoint vocabulary between the two scenes' shot lists (and the covered asset's
        # own text) -- any shared word would spuriously "match" across scenes and defeat the test.
        job = Job(subject="dyatlov pass", providers=ProviderChoice(keywords="fake", scenes="fake",
                                                                    render="fake", sources=["commons"]))
        job.scenes = [Scene(index=0, narration="scene zero"), Scene(index=1, narration="scene one")]
        job.keywords = [
            Keyword(term="alpha portrait", alternatives=["alpha wide shot"], scene_index=0, approved=True),
            Keyword(term="bravo document", alternatives=["bravo broad view"], scene_index=1, approved=True),
        ]
        job.scenes[0].search_terms = ["alpha portrait", "alpha wide shot"]
        job.scenes[1].search_terms = ["bravo document", "bravo broad view"]
        if covered_scene_has_asset:
            job.assets = [Asset(source="commons", path="/x/a", title="alpha portrait image",
                                query="alpha portrait", status="approved")]
        return job

    def test_scene_with_no_matching_asset_and_an_untried_alternative_needs_fallback(self):
        orch = self.make_orch()
        job = self._job()
        gaps = orch._scenes_needing_fallback(job)
        self.assertEqual([(s.index, q) for s, q in gaps], [(1, "bravo broad view")])

    def test_scene_with_a_matching_asset_needs_no_fallback(self):
        orch = self.make_orch()
        job = self._job()
        job.assets.append(Asset(source="commons", path="/x/b", title="bravo document image",
                                query="bravo document", status="approved"))
        self.assertEqual(orch._scenes_needing_fallback(job), [])

    def test_exhausted_shot_list_needs_no_fallback(self):
        orch = self.make_orch()
        job = self._job()
        job.providers.options["fallback_queries_tried"] = ["bravo broad view"]   # already tried, no ranks left
        self.assertEqual(orch._scenes_needing_fallback(job), [])

    def test_job_without_sources_never_needs_fallback(self):
        orch = self.make_orch()
        job = self._job()
        job.providers.sources = []
        self.assertEqual(orch._scenes_needing_fallback(job), [])


class ApproveAssetsFallbackTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end through Orchestrator.approve_assets(): a coverage gap redirects to SOURCING_RUNNING
    (the exact same transition/mechanism a human's own "Search again" already uses) instead of
    SCENES_RUNNING, scoped to just the activated fallback quer(y/ies) -- and a human's existing
    approve/reject decisions on every other asset are left exactly as they were."""

    def make(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reg, self.stages = fake_registry()
        self.store = JobStore(self.tmp.name)
        return Orchestrator(self.store, reg)

    def seed(self, orch, **overrides) -> Job:
        # "gap specific"/"gap broad" vs. the placeholder asset's own disjoint vocabulary -- a genuine
        # non-match, not an accidental word collision.
        job = Job(subject="dyatlov pass", state=S.ASSETS_REVIEW,
                  providers=ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=["commons"]))
        job.scenes = [Scene(index=0, narration="gap scene", search_terms=["gap specific", "gap broad"])]
        job.keywords = [Keyword(term="gap specific", alternatives=["gap broad"], scene_index=0, approved=True)]
        job.assets = [Asset(source="commons", path="/x/a", title="zzz unrelated placeholder", query="zzz filler",
                            status="approved")]
        for k, v in overrides.items():
            setattr(job, k, v)
        orch.store.save(job)
        return job

    async def test_coverage_gap_redirects_to_sourcing_scoped_to_the_fallback_query(self):
        orch = self.make()
        job = self.seed(orch)
        # "unrelated photo" shares no words with "gap specific" or "gap broad" -- a real gap.
        result = await orch.approve_assets(job.id, reviewer="tester")
        self.assertEqual(result.state, S.SOURCING_RUNNING)
        self.assertEqual(result.providers.options["fallback_only_queries"], ["gap broad"])
        self.assertEqual(result.providers.options["fallback_queries_tried"], ["gap broad"])
        # The asset's own approved status is untouched by the redirect.
        self.assertEqual(result.assets[0].status, "approved")

    async def test_no_gap_approves_normally(self):
        orch = self.make()
        asset = Asset(source="commons", path="/x/a", title="gap specific photo", query="gap specific", status="approved")
        job = self.seed(orch, assets=[asset])
        result = await orch.approve_assets(job.id, reviewer="tester")
        self.assertEqual(result.state, S.SCENES_RUNNING)

    async def test_exhausted_fallback_still_approves_normally(self):
        orch = self.make()
        job = self.seed(orch)
        job.providers.options["fallback_queries_tried"] = ["gap broad"]   # Pass 2 already tried and failed
        orch.store.save(job)
        result = await orch.approve_assets(job.id, reviewer="tester")
        self.assertEqual(result.state, S.SCENES_RUNNING)

    async def test_pending_asset_is_still_refused_even_with_a_coverage_gap(self):
        orch = self.make()
        pending = Asset(source="commons", path="/x/p", title="p", query="gap specific", status="pending")
        job = self.seed(orch, assets=[pending])
        with self.assertRaises(sm.TransitionError):
            await orch.approve_assets(job.id, reviewer="tester")


if __name__ == "__main__":
    unittest.main()
