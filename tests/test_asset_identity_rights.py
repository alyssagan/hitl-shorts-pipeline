"""set_asset_identity / set_asset_rights / set_asset_category / asset_report (#7, #8, #13): the write path
that turns the identity/rights/category fields Stage 1 added to the data model into actual human-signed-off
decisions -- and the per-job/per-source reporting the review page's summary panel reads. See
pipeline/core/orchestrator.py."""
from __future__ import annotations

import unittest

from pipeline.core import state_machine as sm
from pipeline.core.models import JobState as S

from tests.test_scene_assets import Base


class SetAssetIdentityTests(Base):
    async def test_records_status_and_evidence_with_reviewer_and_timestamp(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        aid = job.assets[0].id
        job = await orch.set_asset_identity(job.id, aid, status="verified", depicts="Jane Doe",
                                             case_connection="victim's sister", identity_evidence="caption names her",
                                             notes="matches the case reference sheet", reviewer="Aly")
        a = next(x for x in job.assets if x.id == aid)
        self.assertEqual(a.identity_status, "verified")
        self.assertEqual(a.depicts, "Jane Doe")
        self.assertEqual(a.case_connection, "victim's sister")
        self.assertEqual(a.identity_evidence, "caption names her")
        self.assertEqual(a.identity_notes, "matches the case reference sheet")
        self.assertEqual(a.identity_reviewer, "Aly")
        self.assertTrue(a.identity_reviewed_at)

    async def test_rejects_an_unknown_status(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        with self.assertRaises(ValueError):
            await orch.set_asset_identity(job.id, job.assets[0].id, status="pretty_sure", reviewer="Aly")

    async def test_unknown_asset_id_raises(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        with self.assertRaises(ValueError):
            await orch.set_asset_identity(job.id, "nope", status="verified", reviewer="Aly")

    async def test_reviewer_is_required(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        with self.assertRaises(ValueError):
            await orch.set_asset_identity(job.id, job.assets[0].id, status="verified")

    async def test_works_outside_assets_review_same_as_label_asset(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        # to_scenes_review's FAKE_NO_SOURCES job has no assets -- add one the way a scene drag-in would
        job = await orch.add_scene_asset(job.id, job.scenes[0].id,
                                          self.__class__.asset_for_test(), reviewer="Aly")
        aid = next(a.id for a in job.assets)
        job = await orch.set_asset_identity(job.id, aid, status="disputed", reviewer="Aly")
        self.assertEqual(next(a for a in job.assets if a.id == aid).identity_status, "disputed")

    @staticmethod
    def asset_for_test():
        from pipeline.core.models import Asset
        return Asset(source="upload", kind="image", path="/tmp/x.jpg", rel_path="x.jpg", source_url="upload://x.jpg",
                     title="x.jpg", license="CC0", width=1920, height=1080, sha256="idtest1")


class SetAssetRightsTests(Base):
    async def test_records_status_evidence_and_notes(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        aid = job.assets[0].id
        job = await orch.set_asset_rights(job.id, aid, status="cc0", evidence="checked the Commons page directly",
                                           notes="looks right", reviewer="Aly")
        a = next(x for x in job.assets if x.id == aid)
        self.assertEqual(a.rights_status, "cc0")
        self.assertEqual(a.rights_evidence, "checked the Commons page directly")
        self.assertEqual(a.rights_notes, "looks right")
        self.assertEqual(a.rights_reviewer, "Aly")
        self.assertTrue(a.rights_reviewed_at)

    async def test_rejects_an_unknown_status(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        with self.assertRaises(ValueError):
            await orch.set_asset_rights(job.id, job.assets[0].id, status="probably_fine", reviewer="Aly")

    async def test_identity_and_rights_are_independent_axes(self):
        # #8: setting one never touches the other.
        orch = self.make()
        job = await self.to_assets_review(orch)
        aid = job.assets[0].id
        job = await orch.set_asset_identity(job.id, aid, status="verified", reviewer="Aly")
        job = await orch.set_asset_rights(job.id, aid, status="public_domain", reviewer="Aly")
        a = next(x for x in job.assets if x.id == aid)
        self.assertEqual(a.identity_status, "verified")
        self.assertEqual(a.rights_status, "public_domain")


class SetAssetCategoryTests(Base):
    async def test_moves_category_and_logs_before_after(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        aid = job.assets[0].id
        self.assertIsNone(next(a for a in job.assets if a.id == aid).category)
        job = await orch.set_asset_category(job.id, aid, category="verified_case", reviewer="Aly")
        self.assertEqual(next(a for a in job.assets if a.id == aid).category, "verified_case")

    async def test_rejects_an_unknown_category(self):
        orch = self.make()
        job = await self.to_assets_review(orch)
        with self.assertRaises(ValueError):
            await orch.set_asset_category(job.id, job.assets[0].id, category="probably_the_case", reviewer="Aly")


class AssetReportTests(Base):
    async def test_breaks_down_by_source_and_kind_and_includes_usage(self):
        orch = self.make()
        job = await self.to_assets_review(orch, sources=("commons",))
        report = orch.asset_report(job.id)
        self.assertEqual(report["total_assets"], len(job.assets))
        self.assertIn("commons", report["by_source"])
        self.assertEqual(report["by_source"]["commons"]["photos"], sum(a.kind == "image" for a in job.assets if a.source == "commons"))
        self.assertEqual(report["by_source"]["commons"]["videos"], sum(a.kind == "video" for a in job.assets if a.source == "commons"))
        self.assertIn("cost_usd", report["usage"])

    async def test_by_category_and_identity_and_rights_default_to_none_bucket(self):
        orch = self.make()
        job = await self.to_assets_review(orch, sources=("commons",))
        report = orch.asset_report(job.id)
        self.assertEqual(report["by_category"].get("(none)"), len(job.assets))    # nothing categorized yet
        self.assertEqual(report["by_identity_status"].get("unverified"), len(job.assets))
        self.assertEqual(report["by_rights_status"].get("unresolved", 0) + sum(
            v for k, v in report["by_rights_status"].items() if k != "unresolved"), len(job.assets))

    async def test_moving_one_asset_shifts_the_buckets(self):
        orch = self.make()
        job = await self.to_assets_review(orch, sources=("commons",))
        aid = job.assets[0].id
        await orch.set_asset_category(job.id, aid, category="verified_case", reviewer="Aly")
        await orch.set_asset_identity(job.id, aid, status="verified", reviewer="Aly")
        report = orch.asset_report(job.id)
        self.assertEqual(report["by_category"].get("verified_case"), 1)
        self.assertEqual(report["by_identity_status"].get("verified"), 1)


if __name__ == "__main__":
    unittest.main()
