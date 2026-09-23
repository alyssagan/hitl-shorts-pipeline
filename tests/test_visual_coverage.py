"""Pre-render visual coverage check (#12): check_visual_coverage() and the gate it puts in front of
approve_scenes(). The point is never to silently substitute generic footage for case material -- a job
that used the visual checklist can't render past an unresolved item without either resolving it
(fulfilled/not_available/skipped, each already forced through a note/asset_id by update_checklist_item,
see test_case_reference.py) or an explicit, logged override. A job that never touched the checklist at
all isn't gated by any of this -- it's an opt-in feature, not a new requirement forced onto every job."""
from __future__ import annotations

import unittest

from pipeline.core.models import JobState as S

from tests.test_scene_assets import Base


class CheckVisualCoverageTests(Base):
    async def test_no_checklist_at_all_is_ready(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        report = orch.check_visual_coverage(job.id)
        self.assertFalse(report["has_checklist"])
        self.assertTrue(report["ready"])
        self.assertEqual(report["unresolved"], [])

    async def test_an_item_left_at_needed_is_unresolved(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.add_checklist_item(job.id, "a period photo of the case", reviewer="Aly")
        report = orch.check_visual_coverage(job.id)
        self.assertTrue(report["has_checklist"])
        self.assertFalse(report["ready"])
        self.assertEqual(len(report["unresolved"]), 1)
        self.assertEqual(report["unresolved"][0]["status"], "needed")
        self.assertEqual(len(report["remediation_options"]), 5)

    async def test_candidates_found_is_still_unresolved(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.add_checklist_item(job.id, "x", reviewer="Aly")
        iid = job.visual_checklist[0].id
        job = await orch.update_checklist_item(job.id, iid, status="candidates_found", note="found some maybes", reviewer="Aly")
        report = orch.check_visual_coverage(job.id)
        self.assertFalse(report["ready"])
        self.assertEqual(report["unresolved"][0]["status"], "candidates_found")

    async def test_fulfilled_not_available_and_skipped_all_count_as_resolved(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        # Give the job an approved asset to point "fulfilled" at, the same way a human would.
        job = await orch.add_scene_asset(job.id, job.scenes[0].id, self._asset(), reviewer="Aly")
        aid = job.assets[0].id
        job = await orch.add_checklist_item(job.id, "a", reviewer="Aly")
        job = await orch.add_checklist_item(job.id, "b", reviewer="Aly")
        job = await orch.add_checklist_item(job.id, "c", reviewer="Aly")
        ids = [i.id for i in job.visual_checklist]
        job = await orch.update_checklist_item(job.id, ids[0], status="fulfilled", asset_id=aid, reviewer="Aly")
        job = await orch.update_checklist_item(job.id, ids[1], status="not_available", note="nothing found", reviewer="Aly")
        job = await orch.update_checklist_item(job.id, ids[2], status="skipped", note="not needed after all", reviewer="Aly")
        report = orch.check_visual_coverage(job.id)
        self.assertTrue(report["ready"])
        self.assertEqual(report["unresolved"], [])
        self.assertEqual(report["counts"]["fulfilled"], 1)
        self.assertEqual(report["counts"]["not_available"], 1)
        self.assertEqual(report["counts"]["skipped"], 1)

    async def test_cross_references_scenes_by_shared_search_term(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        term = job.approved_keywords[0].term
        job = await orch.add_checklist_item(job.id, "x", linked_keyword_term=term, reviewer="Aly")
        report = orch.check_visual_coverage(job.id)
        # FakeScenes gives every one of its 3 scenes the same first approved keyword as its search_terms.
        self.assertEqual(len(report["unresolved"][0]["linked_scene_ids"]), 3)

    async def test_callable_at_any_job_state_not_just_scenes_review(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.add_checklist_item(job.id, "x", reviewer="Aly")
        job = await orch.approve_scenes(job.id, reviewer="Aly", override_note="ship it, follow up later")
        self.assertEqual(job.state, S.RENDERING)
        report = orch.check_visual_coverage(job.id)     # still readable well past scenes_review
        self.assertFalse(report["ready"])

    @staticmethod
    def _asset():
        from pipeline.core.models import Asset
        return Asset(source="upload", kind="image", path="/tmp/x.jpg", rel_path="x.jpg", source_url="upload://x.jpg",
                     title="x.jpg", license="CC0", width=1920, height=1080, sha256="covtest1")


class ApproveScenesCoverageGateTests(Base):
    async def test_approves_normally_with_no_checklist(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.approve_scenes(job.id, reviewer="Aly")
        self.assertEqual(job.state, S.RENDERING)

    async def test_refuses_to_render_past_an_unresolved_item_without_an_override(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.add_checklist_item(job.id, "a period photo of the victim", reviewer="Aly")
        with self.assertRaises(ValueError) as ctx:
            await orch.approve_scenes(job.id, reviewer="Aly")
        self.assertIn("period photo of the victim", str(ctx.exception))
        # The job never moved -- refusing the render doesn't half-apply the state transition.
        self.assertEqual(orch.get(job.id).state, S.SCENES_REVIEW)

    async def test_an_explicit_override_note_lets_it_through_and_gets_logged(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.add_checklist_item(job.id, "a period photo of the victim", reviewer="Aly")
        job = await orch.approve_scenes(job.id, reviewer="Aly",
                                         override_note="couldn't find one in time, using the historical b-roll instead")
        self.assertEqual(job.state, S.RENDERING)
        entries = orch.store.decisions(job.id).entries()
        approved = next(e for e in entries if e["action"] == "approved_scenes")
        self.assertEqual(approved["outputs"]["rendered_with_unresolved_visual_needs"], ["a period photo of the victim"])
        self.assertIn("couldn't find one in time", approved["outputs"]["override_note"])

    async def test_resolving_every_item_lets_it_through_with_no_override_needed(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.add_checklist_item(job.id, "x", reviewer="Aly")
        iid = job.visual_checklist[0].id
        job = await orch.update_checklist_item(job.id, iid, status="not_available", note="not found anywhere", reviewer="Aly")
        job = await orch.approve_scenes(job.id, reviewer="Aly")
        self.assertEqual(job.state, S.RENDERING)

    async def test_whitespace_only_override_note_still_refuses(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.add_checklist_item(job.id, "x", reviewer="Aly")
        with self.assertRaises(ValueError):
            await orch.approve_scenes(job.id, reviewer="Aly", override_note="   ")


if __name__ == "__main__":
    unittest.main()
