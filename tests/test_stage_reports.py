"""Unit tests for the two per-stage report writers added alongside SOURCES.md/CREDITS.md:
pipeline/vetting/report.py (VETTING_REPORT.md) and pipeline/stages/scenes/report.py (SCRIPT.md).
Full-pipeline coverage (both files present and correct at the end of a real run) lives in
tests/test_sources_flow.py's FlowTests.test_full_flow_with_gate_rules_and_log; this file only
covers the writers' own edge cases in isolation."""
import tempfile
import unittest
from pathlib import Path

from pipeline.core.models import Asset, Flag, Job, Scene, Vetting
from pipeline.stages.scenes.report import write_script_md
from pipeline.vetting.report import write_vetting_report


class VettingReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_nothing_written_before_any_asset_is_vetted(self):
        job = Job(subject="Cats", assets=[Asset(source="commons", path="x.jpg", title="Cat")])
        self.assertIsNone(write_vetting_report(job, self.dir))
        self.assertFalse((self.dir / "VETTING_REPORT.md").exists())

    def test_written_once_an_asset_has_vetting_and_lists_flags_and_decision(self):
        a = Asset(source="commons", path="x.jpg", title="Risky cat", status="rejected",
                   vetting=Vetting(risk="high", usable=True, relevance=0.8,
                                    flags=[Flag(rule="LIC_NC", severity="high", message="Non-commercial license", evidence="CC BY-NC")]))
        b = Asset(source="pexels", path="y.jpg", title="Fine cat", status="approved",
                   vetting=Vetting(risk="low", usable=True, relevance=0.9))
        job = Job(subject="Cats", assets=[a, b])
        p = write_vetting_report(job, self.dir)
        self.assertEqual(p, self.dir / "VETTING_REPORT.md")
        text = p.read_text()
        self.assertIn("Risky cat", text)
        self.assertIn("Fine cat", text)
        self.assertIn("LIC_NC", text)
        self.assertIn("Non-commercial license", text)
        self.assertIn("rejected", text)
        self.assertIn("## Why each flag fired", text)

    def test_unvetted_assets_alongside_vetted_ones_do_not_crash_and_are_left_out_of_the_table(self):
        vetted = Asset(source="commons", path="x.jpg", title="Vetted", vetting=Vetting(risk="low"))
        pending = Asset(source="commons", path="y.jpg", title="Not yet vetted")
        job = Job(subject="Cats", assets=[vetted, pending])
        text = write_vetting_report(job, self.dir).read_text()
        self.assertIn("Vetted", text)
        self.assertNotIn("Not yet vetted", text)


class ScriptReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_nothing_written_before_the_scene_stage_has_run(self):
        job = Job(subject="Cats")
        self.assertIsNone(write_script_md(job, self.dir))
        self.assertFalse((self.dir / "SCRIPT.md").exists())

    def test_written_once_scenes_exist_with_search_terms_and_clip_info(self):
        job = Job(subject="Cats", script="Cats are great.\n\nThey sleep a lot.",
                   scenes=[
                       Scene(index=0, narration="Cats are great.", search_terms=["cat portrait"],
                             clip_path="assets/cat1.jpg", asset_id="a1", clip_reason="matched 'cat portrait'", approved=True),
                       Scene(index=1, narration="They sleep a lot.", search_terms=["sleeping cat"], approved=False),
                   ])
        p = write_script_md(job, self.dir)
        text = p.read_text()
        self.assertIn("# Script: Cats", text)
        self.assertIn("## Scene 1 (approved)", text)
        self.assertIn("## Scene 2 (not yet approved)", text)
        self.assertIn("cat portrait", text)
        self.assertIn("assets/cat1.jpg", text)
        self.assertIn("matched 'cat portrait'", text)
        self.assertIn("(no clip matched)", text)   # scene 2 has no clip_path


if __name__ == "__main__":
    unittest.main()
