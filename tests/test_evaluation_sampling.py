"""Covers pipeline/evaluation/sampling.py: random draws stay within their own pool (never crossing
selected/rejected), borderline picks are the closest-to-threshold ones and never double-picked with a random
draw, exclusions (already labeled / already sampled) are respected, and SAMPLE_TAGS.jsonl round-trips."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.core.models import Asset, Job, Vetting
from pipeline.evaluation import sampling


def mk(id_, *, decision, score, threshold=0.5, dup_flag=False):
    a = Asset(id=id_, source="x", path="/x", title=f"asset {id_}", license="CC0", source_url="https://x/y", author="a")
    flags = [{"rule": "DUPLICATE", "severity": "low", "message": "dup", "evidence": "x"}] if dup_flag else []
    a.vetting = Vetting(risk="low", relevance=score, relevance_decision=decision, relevance_threshold=threshold,
                        scoring_method="tfidf", method_version="tfidf-v1", flags=flags)
    return a


def mk_job(assets):
    j = Job(subject="jack the ripper")
    j.assets = assets
    return j


class PoolSeparationTests(unittest.TestCase):
    def test_random_selected_only_comes_from_machine_selected_pool(self):
        assets = [mk("s1", decision="relevant", score=0.9), mk("s2", decision="relevant", score=0.8),
                  mk("r1", decision="not_relevant", score=0.1)]
        job = mk_job(assets)
        items, _ = sampling.pick_sample(job, n_random_selected=2, seed=1)
        self.assertEqual({i.asset_id for i in items}, {"s1", "s2"})
        self.assertTrue(all(i.sampled_as == "random_selected" for i in items))

    def test_random_rejected_only_comes_from_machine_rejected_pool(self):
        assets = [mk("s1", decision="relevant", score=0.9), mk("r1", decision="not_relevant", score=0.1),
                  mk("r2", decision="not_relevant", score=0.2)]
        job = mk_job(assets)
        items, _ = sampling.pick_sample(job, n_random_rejected=2, seed=1)
        self.assertEqual({i.asset_id for i in items}, {"r1", "r2"})
        self.assertTrue(all(i.sampled_as == "random_rejected" for i in items))

    def test_unscored_assets_are_never_sampled(self):
        a = Asset(id="unscored", source="x", path="/x", title="t", license="CC0", source_url="https://x/y", author="a")
        job = mk_job([a, mk("s1", decision="relevant", score=0.9)])
        items, _ = sampling.pick_sample(job, n_random_selected=5)
        self.assertEqual({i.asset_id for i in items}, {"s1"})


class BorderlineTests(unittest.TestCase):
    def test_borderline_picks_closest_to_threshold_first(self):
        assets = [mk("far", decision="relevant", score=0.95, threshold=0.5),
                  mk("near", decision="relevant", score=0.52, threshold=0.5),
                  mk("mid", decision="not_relevant", score=0.4, threshold=0.5)]
        job = mk_job(assets)
        items, _ = sampling.pick_sample(job, n_borderline=1)
        self.assertEqual(items[0].asset_id, "near")
        self.assertEqual(items[0].sampled_as, "borderline")

    def test_borderline_never_double_picks_something_a_random_draw_already_took(self):
        assets = [mk("near", decision="relevant", score=0.51, threshold=0.5),
                  mk("other", decision="relevant", score=0.9, threshold=0.5)]
        job = mk_job(assets)
        # forces "near" into the random draw every time (only one candidate); borderline must then skip it.
        items, _ = sampling.pick_sample(job, n_random_selected=1, n_borderline=1, seed=1)
        tags = {i.asset_id: i.sampled_as for i in items}
        self.assertEqual(len(items), 2)
        self.assertEqual(sum(1 for t in tags.values() if t == "random_selected"), 1)
        self.assertEqual(sum(1 for t in tags.values() if t == "borderline"), 1)


class ExclusionTests(unittest.TestCase):
    def test_already_labeled_excluded_by_default(self):
        assets = [mk("s1", decision="relevant", score=0.9), mk("s2", decision="relevant", score=0.8)]
        job = mk_job(assets)
        items, _ = sampling.pick_sample(job, n_random_selected=5, already_labeled_ids={"s1"})
        self.assertEqual({i.asset_id for i in items}, {"s2"})

    def test_include_labeled_flag_allows_relabeling(self):
        assets = [mk("s1", decision="relevant", score=0.9)]
        job = mk_job(assets)
        items, _ = sampling.pick_sample(job, n_random_selected=5, already_labeled_ids={"s1"}, include_labeled=True)
        self.assertEqual({i.asset_id for i in items}, {"s1"})
        self.assertTrue(items[0].already_labeled)

    def test_exclude_asset_ids_removes_previously_sampled(self):
        assets = [mk("s1", decision="relevant", score=0.9), mk("s2", decision="relevant", score=0.8)]
        job = mk_job(assets)
        items, _ = sampling.pick_sample(job, n_random_selected=5, exclude_asset_ids={"s1"})
        self.assertEqual({i.asset_id for i in items}, {"s2"})


class ReproducibilityTests(unittest.TestCase):
    def test_same_seed_gives_same_sample(self):
        assets = [mk(f"s{i}", decision="relevant", score=0.9) for i in range(10)]
        job = mk_job(assets)
        items1, seed1 = sampling.pick_sample(job, n_random_selected=3, seed=42)
        items2, seed2 = sampling.pick_sample(job, n_random_selected=3, seed=42)
        self.assertEqual(seed1, seed2, 42)
        self.assertEqual({i.asset_id for i in items1}, {i.asset_id for i in items2})

    def test_no_seed_still_returns_a_seed_that_was_used(self):
        assets = [mk("s1", decision="relevant", score=0.9)]
        job = mk_job(assets)
        _, seed_used = sampling.pick_sample(job, n_random_selected=1)
        self.assertIsInstance(seed_used, int)


class SampleTagsRoundTripTests(unittest.TestCase):
    def test_write_then_read_then_already_sampled(self):
        with tempfile.TemporaryDirectory() as d:
            job_dir = Path(d)
            assets = [mk("s1", decision="relevant", score=0.9)]
            job = mk_job(assets)
            items, seed = sampling.pick_sample(job, n_random_selected=1, seed=1)
            sampling.write_sample_tags(job_dir, "job123", items, seed=seed, by="Aly", note="first batch")

            rows = sampling.read_sample_tags(job_dir)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["asset_id"], "s1")
            self.assertEqual(rows[0]["sampled_as"], "random_selected")
            self.assertEqual(rows[0]["by"], "Aly")

            self.assertEqual(sampling.already_sampled_ids(job_dir), {"s1"})

    def test_write_is_append_only_across_two_runs(self):
        with tempfile.TemporaryDirectory() as d:
            job_dir = Path(d)
            assets = [mk("s1", decision="relevant", score=0.9), mk("s2", decision="relevant", score=0.8)]
            job = mk_job(assets)
            items1, seed1 = sampling.pick_sample(job, n_random_selected=1, seed=1)
            sampling.write_sample_tags(job_dir, "job123", items1, seed=seed1, by="Aly")
            items2, seed2 = sampling.pick_sample(job, n_random_selected=1, seed=2,
                                                 exclude_asset_ids=sampling.already_sampled_ids(job_dir))
            sampling.write_sample_tags(job_dir, "job123", items2, seed=seed2, by="Aly")

            rows = sampling.read_sample_tags(job_dir)
            self.assertEqual(len(rows), 2)                     # both runs' rows kept, nothing overwritten
            self.assertEqual({r["asset_id"] for r in rows}, {"s1", "s2"})


if __name__ == "__main__":
    unittest.main()
