"""Provenance: every stage's start/finish/duration goes into the decision log (never just the ephemeral
activity log), and Orchestrator.timing_summary() rolls it up into total time, time per stage, and time spent
waiting on a human review. See docs/LOGGING.md "Timing / provenance"."""
import json
import tempfile
import unittest

from pipeline.core.models import ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from tests.fakes import Boom, FakeKeywords, fake_registry


class TimingSummaryMathTests(unittest.TestCase):
    """Feeds a hand-built decisions.jsonl (no real sleeping, no flakiness) straight to timing_summary() so the
    arithmetic itself is checked exactly, independent of how fast the machine running the tests happens to be."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = JobStore(self.tmp.name)
        reg, _ = fake_registry()
        self.orch = Orchestrator(self.store, reg, settings={})

    def _write(self, job_id: str, rows: list[dict]) -> None:
        d = self.store.job_dir(job_id, slug="x")
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text("{}", encoding="utf-8")           # only needs to exist for job_dir() to find it
        with open(d / "decisions.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def _entry(self, at: str, stage: str, action: str, actor_type: str = "machine", **extra) -> dict:
        return {"at": at, "stage": stage, "action": action, "actor": {"type": actor_type, "name": "x"},
                "subject": extra.get("subject", {}), "outputs": extra.get("outputs", {})}

    def test_no_entries_is_all_zero(self):
        self._write("j1", [])
        self.assertEqual(self.orch.timing_summary("j1"),
                         {"total_wall_seconds": 0.0, "time_per_stage_seconds": {}, "time_waiting_on_you_seconds": 0.0,
                          "waits": []})

    def test_stage_time_and_human_wait_are_computed_correctly(self):
        rows = [
            self._entry("2026-01-01T00:00:00+00:00", "project", "created", "human"),
            self._entry("2026-01-01T00:00:01+00:00", "keywords", "stage_started"),
            self._entry("2026-01-01T00:00:06+00:00", "keywords", "stage_finished",
                       subject={"stage": "keywords_running"}, outputs={"duration_seconds": 5.0}),
            # human takes 10s to approve keywords after the gate opens
            self._entry("2026-01-01T00:00:16+00:00", "keywords", "approved_keywords", "human"),
            self._entry("2026-01-01T00:00:17+00:00", "sourcing", "stage_started"),
            self._entry("2026-01-01T00:00:37+00:00", "sourcing", "stage_finished",
                       subject={"stage": "sourcing_running"}, outputs={"duration_seconds": 20.0}),
            # human takes 4s to approve assets
            self._entry("2026-01-01T00:00:41+00:00", "assets", "approved_asset_pool", "human"),
        ]
        self._write("j2", rows)
        out = self.orch.timing_summary("j2")
        self.assertEqual(out["total_wall_seconds"], 41.0)
        self.assertEqual(out["time_per_stage_seconds"], {"keywords_running": 5.0, "sourcing_running": 20.0})
        self.assertEqual(out["stage_run_counts"], {"keywords_running": 1, "sourcing_running": 1})
        self.assertEqual(out["time_waiting_on_you_seconds"], 14.0)
        self.assertEqual([w["waited_seconds"] for w in out["waits"]], [10.0, 4.0])

    def test_a_stage_that_runs_twice_sums_its_duration(self):
        """'Search again' re-enters sourcing_running a second time -- its time should add up, not overwrite."""
        rows = [
            self._entry("2026-01-01T00:00:00+00:00", "project", "created", "human"),
            self._entry("2026-01-01T00:00:00+00:00", "sourcing", "stage_started"),
            self._entry("2026-01-01T00:00:10+00:00", "sourcing", "stage_finished",
                       subject={"stage": "sourcing_running"}, outputs={"duration_seconds": 10.0}),
            self._entry("2026-01-01T00:00:20+00:00", "assets", "rejected_asset_pool", "human"),
            self._entry("2026-01-01T00:00:21+00:00", "sourcing", "stage_started"),
            self._entry("2026-01-01T00:00:36+00:00", "sourcing", "stage_finished",
                       subject={"stage": "sourcing_running"}, outputs={"duration_seconds": 15.0}),
        ]
        self._write("j3", rows)
        out = self.orch.timing_summary("j3")
        self.assertEqual(out["time_per_stage_seconds"], {"sourcing_running": 25.0})
        self.assertEqual(out["stage_run_counts"], {"sourcing_running": 2})

    def test_only_the_first_human_action_closes_a_wait_window(self):
        """Two human decisions in a row (e.g. a note-only action right after an approval) must not double-count
        the same waiting window."""
        rows = [
            self._entry("2026-01-01T00:00:00+00:00", "project", "created", "human"),
            self._entry("2026-01-01T00:00:00+00:00", "keywords", "stage_started"),
            self._entry("2026-01-01T00:00:05+00:00", "keywords", "stage_finished",
                       subject={"stage": "keywords_running"}, outputs={"duration_seconds": 5.0}),
            self._entry("2026-01-01T00:00:15+00:00", "keywords", "approved_keywords", "human"),
            self._entry("2026-01-01T00:00:16+00:00", "keywords", "some_followup_note", "human"),
        ]
        self._write("j4", rows)
        out = self.orch.timing_summary("j4")
        self.assertEqual(out["time_waiting_on_you_seconds"], 10.0)
        self.assertEqual(len(out["waits"]), 1)


class StageTimingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Drives a real (fake-provider) job through the orchestrator and checks the decision log actually got the
    timing entries -- not just that the standalone math in timing_summary() is correct."""

    async def test_completed_job_gets_stage_timings_and_a_final_summary(self):
        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry()
            orch = Orchestrator(JobStore(d), reg, settings={})
            job = await orch.create_job("cats", ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=[]),
                                        reviewer="Aly")
            await orch.start(job.id, reviewer="Aly")
            await orch.run_pending(job.id)                          # script_running -> script_review
            job = orch.get(job.id)
            self.assertEqual(job.state.value, "script_review")
            job = await orch.approve_script(job.id, reviewer="Aly")
            self.assertEqual(job.state.value, "keywords_running")
            # Keywords are auto-approved by default (docs/PIPELINE_STAGES.md), so this one run_pending
            # call both generates them and carries the job straight through to scenes.
            await orch.run_pending(job.id)
            job = orch.get(job.id)
            self.assertEqual(job.state.value, "scenes_running")
            await orch.run_pending(job.id)                          # scenes_running -> scenes_review
            job = orch.get(job.id)
            self.assertEqual(job.state.value, "scenes_review")
            job = await orch.approve_scenes(job.id, reviewer="Aly")
            self.assertEqual(job.state.value, "rendering")
            await orch.run_pending(job.id)                          # rendering -> completed
            job = orch.get(job.id)
            self.assertEqual(job.state.value, "completed")

            entries = orch.store.decisions(job.id).entries()
            started = [e for e in entries if e["action"] == "stage_started"]
            finished = [e for e in entries if e["action"] == "stage_finished"]
            expected_stages = {"script_running", "keywords_running", "scenes_running", "rendering"}
            self.assertEqual({e["subject"]["stage"] for e in started}, expected_stages)
            self.assertEqual({e["subject"]["stage"] for e in finished}, expected_stages)
            for e in finished:
                self.assertIsInstance(e["outputs"]["duration_seconds"], (int, float))
                self.assertGreaterEqual(e["outputs"]["duration_seconds"], 0.0)

            summaries = [e for e in entries if e["action"] == "job_summary"]
            self.assertEqual(len(summaries), 1)                     # written exactly once, on reaching COMPLETED
            out = summaries[0]["outputs"]
            self.assertEqual(set(out["time_per_stage_seconds"]), expected_stages)
            self.assertEqual(out, orch.timing_summary(job.id))       # matches what a fresh computation would give

    async def test_failed_stage_records_duration_and_a_summary_too(self):
        with tempfile.TemporaryDirectory() as d:
            reg, _ = fake_registry(keywords=Boom(FakeKeywords(), failures=99))
            orch = Orchestrator(JobStore(d), reg, settings={})
            job = await orch.create_job("cats", ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=[]),
                                        reviewer="Aly")
            await orch.start(job.id, reviewer="Aly")
            await orch.run_pending(job.id)                          # script_running -> script_review
            job = await orch.approve_script(job.id, reviewer="Aly")  # -> keywords_running
            self.assertEqual(job.state.value, "keywords_running")
            await orch.run_pending(job.id)
            job = orch.get(job.id)
            self.assertEqual(job.state.value, "failed")

            entries = orch.store.decisions(job.id).entries()
            failed = [e for e in entries if e["action"] == "stage_failed"]
            self.assertEqual(len(failed), 1)
            self.assertIsInstance(failed[0]["outputs"]["duration_seconds"], (int, float))
            self.assertTrue(any(e["action"] == "job_summary" for e in entries))


if __name__ == "__main__":
    unittest.main()
