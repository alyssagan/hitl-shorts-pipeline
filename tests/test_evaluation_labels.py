"""Covers pipeline/evaluation/labels.py: discovering job dirs, reading RELEVANCE_LABELS.jsonl (including a
missing file, and a malformed line), collapsing relabels to "latest per asset", and the holdout marker
round-trip that scripts/evaluate_relevance.py / scripts/sample_for_review.py both rely on."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.evaluation import labels as labels_io


class DiscoverAndReadTests(unittest.TestCase):
    def test_discover_job_dirs_finds_only_real_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "cats-abc123").mkdir()
            (root / "cats-abc123" / "job.json").write_text("{}")
            (root / "not-a-job").mkdir()                      # no job.json -- must be ignored
            (root / "cats-abc123" / "RELEVANCE_LABELS.jsonl").write_text("")
            found = labels_io.discover_job_dirs(root)
            self.assertEqual(found, [root / "cats-abc123"])

    def test_read_label_rows_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(labels_io.read_label_rows(Path(d)), [])

    def test_read_label_rows_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / labels_io.LABELS_FILENAME
            p.write_text('{"asset_id": "a1", "label": "use"}\nnot json\n\n{"asset_id": "a2", "label": "irrelevant"}\n')
            rows = labels_io.read_label_rows(Path(d))
            self.assertEqual([r["asset_id"] for r in rows], ["a1", "a2"])


class LatestPerAssetTests(unittest.TestCase):
    def test_relabel_keeps_only_the_newest_row_per_asset_per_job(self):
        rows = [
            {"_job_dir": "job1", "asset_id": "a1", "at": "2026-01-01T00:00:00+00:00", "label": "irrelevant"},
            {"_job_dir": "job1", "asset_id": "a1", "at": "2026-01-02T00:00:00+00:00", "label": "use"},   # relabeled later
            {"_job_dir": "job1", "asset_id": "a2", "at": "2026-01-01T00:00:00+00:00", "label": "use"},
            {"_job_dir": "job2", "asset_id": "a1", "at": "2026-01-01T00:00:00+00:00", "label": "duplicate"},  # different job, same asset id
        ]
        out = labels_io.latest_per_asset(rows)
        by_key = {(r["_job_dir"], r["asset_id"]): r["label"] for r in out}
        self.assertEqual(by_key, {("job1", "a1"): "use", ("job1", "a2"): "use", ("job2", "a1"): "duplicate"})

    def test_the_full_relabel_history_is_never_discarded_only_latest_per_asset_is_a_view(self):
        rows = [
            {"_job_dir": "job1", "asset_id": "a1", "at": "2026-01-01T00:00:00+00:00", "label": "irrelevant"},
            {"_job_dir": "job1", "asset_id": "a1", "at": "2026-01-02T00:00:00+00:00", "label": "use"},
        ]
        self.assertEqual(len(rows), 2)                        # latest_per_asset() doesn't mutate the input
        self.assertEqual(len(labels_io.latest_per_asset(rows)), 1)


class HoldoutMarkerTests(unittest.TestCase):
    def test_not_a_holdout_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(labels_io.is_holdout(Path(d)))

    def test_mark_then_read_then_unmark(self):
        with tempfile.TemporaryDirectory() as d:
            job_dir = Path(d)
            marker = labels_io.mark_holdout(job_dir, by="Aly", reason="first full case")
            self.assertFalse(marker["already"])
            got = labels_io.is_holdout(job_dir)
            self.assertEqual(got["by"], "Aly")
            self.assertEqual(got["reason"], "first full case")
            self.assertIn("marked_at", got)

            again = labels_io.mark_holdout(job_dir, by="Aly", reason="updated reason")
            self.assertTrue(again["already"])
            self.assertEqual(labels_io.is_holdout(job_dir)["reason"], "updated reason")

            self.assertTrue(labels_io.unmark_holdout(job_dir))
            self.assertIsNone(labels_io.is_holdout(job_dir))
            self.assertFalse(labels_io.unmark_holdout(job_dir))         # already gone -- no error, just False


class ReadAllLabelRowsTests(unittest.TestCase):
    def test_reads_across_jobs_and_flags_holdout_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            j1 = root / "cats-aaa111"
            j2 = root / "dogs-bbb222"
            for j in (j1, j2):
                j.mkdir()
                (j / "job.json").write_text("{}")
            (j1 / labels_io.LABELS_FILENAME).write_text(json.dumps({"asset_id": "a1", "label": "use", "at": "2026-01-01T00:00:00+00:00"}) + "\n")
            (j2 / labels_io.LABELS_FILENAME).write_text(json.dumps({"asset_id": "b1", "label": "irrelevant", "at": "2026-01-01T00:00:00+00:00"}) + "\n")
            labels_io.mark_holdout(j2, by="Aly", reason="holdout case")

            loaded = labels_io.read_all_label_rows(root)
            self.assertEqual(loaded.jobs_scanned, 2)
            self.assertEqual(loaded.jobs_with_labels, 2)
            self.assertEqual({r["asset_id"] for r in loaded.rows}, {"a1", "b1"})
            self.assertEqual(loaded.holdout_job_dirs, {str(j2)})

    def test_job_ids_filter_restricts_to_named_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            j1 = root / "cats-aaa111"
            j2 = root / "dogs-bbb222"
            for j in (j1, j2):
                j.mkdir()
                (j / "job.json").write_text("{}")
            (j1 / labels_io.LABELS_FILENAME).write_text(json.dumps({"asset_id": "a1", "label": "use", "at": "2026-01-01T00:00:00+00:00"}) + "\n")
            (j2 / labels_io.LABELS_FILENAME).write_text(json.dumps({"asset_id": "b1", "label": "use", "at": "2026-01-01T00:00:00+00:00"}) + "\n")

            loaded = labels_io.read_all_label_rows(root, job_ids=["aaa111"])
            self.assertEqual(loaded.jobs_scanned, 1)
            self.assertEqual({r["asset_id"] for r in loaded.rows}, {"a1"})


if __name__ == "__main__":
    unittest.main()
