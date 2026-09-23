"""End-to-end: real Orchestrator.label_asset() writes to a real RELEVANCE_LABELS.jsonl, and the two CLI scripts
(scripts/evaluate_relevance.py, scripts/sample_for_review.py) read it back correctly -- this is the seam most
likely to break silently (a field name that doesn't quite match between what `_write_label` writes and what
`pipeline/evaluation/*` expects), so it's covered here against the real orchestrator, not just hand-built rows."""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pipeline.core.models import Job, JobState, Keyword, ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.evaluation import labels as labels_io
from tests.fakes import fake_registry

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mk(id_, title=""):
    from pipeline.core.models import Asset
    return Asset(id=id_, source="x", path="/x", title=title, license="CC0", source_url="https://x/y", author="a")


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects_dir = Path(self.tmp.name)
        reg, _ = fake_registry()
        self.orch = Orchestrator(JobStore(self.projects_dir), reg, settings={})

        job = Job(subject="jack the ripper", providers=ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=[]))
        job.keywords = [Keyword(term="whitechapel 1888", source="human", approved=True)]
        # a1/a2 score "relevant" (tfidf), a3 scores "not_relevant" -- vet_all needs real scoring, so use it directly.
        from pipeline.vetting.rules import vet_all
        a1, a2, a3 = mk("a1", "Whitechapel Road 1888"), mk("a2", "Whitechapel murders victim"), mk("a3", "unrelated cats")
        job.assets = [a1, a2, a3]
        vet_all(job.assets, ["whitechapel 1888"], 0.5)
        job.state = JobState.ASSETS_REVIEW      # so review_assets() (label-via-review-flow test) is allowed; label_asset() doesn't care either way
        self.orch.store.save(job)
        self.job = job

    async def test_label_asset_writes_rows_the_report_script_can_read(self):
        await self.orch.label_asset(self.job.id, "a1", "use", reviewer="Aly")
        await self.orch.label_asset(self.job.id, "a2", "irrelevant", reviewer="Aly", reason="wrong era")
        await self.orch.label_asset(self.job.id, "a3", "use", reviewer="Aly")   # a machine-rejected item the human wants -> a "missed use"

        evaluate = _load_script("evaluate_relevance")
        out = io.StringIO()
        old_argv = sys.argv
        try:
            sys.argv = ["evaluate_relevance.py", "--projects-dir", str(self.projects_dir)]
            with redirect_stdout(out):
                rc = evaluate.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(rc, 0)
        text = out.getvalue()
        # This fixture calls vet_all() with no tfidf/llm score batches, so it exercises the keyword-match
        # fallback path -- real runs (Orchestrator._apply_vetting) always pass a tfidf batch at minimum.
        self.assertIn("keyword-match [keyword-match-v1]", text)
        self.assertIn("Use yield", text)
        self.assertIn("Missed Use items", text)

    async def test_relabeling_only_the_latest_counts_in_the_report(self):
        await self.orch.label_asset(self.job.id, "a1", "irrelevant", reviewer="Aly")
        await self.orch.label_asset(self.job.id, "a1", "use", reviewer="Aly", note="changed my mind")

        job_dir = self.orch.store.job_dir(self.job.id)
        rows = labels_io.read_label_rows(job_dir)
        self.assertEqual(len(rows), 2)                       # both relabel events kept on disk, append-only
        current = labels_io.latest_per_asset(rows)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["label"], "use")

    async def test_review_assets_can_also_save_a_label_inline(self):
        # review_assets() (the normal approve/reject flow) can optionally carry a label too (docs/EVALUATION.md);
        # this is the path the OLD review_page.py 2-button UI used to exercise indirectly. High-risk asset a1
        # here has no LOW_RES/etc flags, so no note is required for "approve".
        resp = await self.orch.review_assets(self.job.id, {"a1": {"decision": "approve", "label": "use"}}, reviewer="Aly")
        self.assertEqual(next(a for a in resp.assets if a.id == "a1").status, "approved")
        rows = labels_io.read_label_rows(self.orch.store.job_dir(self.job.id))
        self.assertEqual(rows[0]["label"], "use")

    async def test_sample_and_mark_holdout_cli_round_trip(self):
        sampler = _load_script("sample_for_review")
        out = io.StringIO()
        old_argv = sys.argv
        try:
            sys.argv = ["sample_for_review.py", "--projects-dir", str(self.projects_dir), "sample", self.job.id,
                        "--random-selected", "2", "--random-rejected", "1", "--by", "Aly"]
            with redirect_stdout(out):
                rc = sampler.main()
            self.assertEqual(rc, 0)
            self.assertIn("random_selected", out.getvalue())

            out2 = io.StringIO()
            sys.argv = ["sample_for_review.py", "--projects-dir", str(self.projects_dir), "mark-holdout", self.job.id,
                        "--by", "Aly", "--reason", "test holdout"]
            with redirect_stdout(out2):
                rc2 = sampler.main()
            self.assertEqual(rc2, 0)

            out3 = io.StringIO()
            sys.argv = ["sample_for_review.py", "--projects-dir", str(self.projects_dir), "list-holdouts"]
            with redirect_stdout(out3):
                sampler.main()
            self.assertIn(self.job.id, out3.getvalue())

            # Sampling from a holdout job without acknowledging it should now refuse.
            out4 = io.StringIO()
            sys.argv = ["sample_for_review.py", "--projects-dir", str(self.projects_dir), "sample", self.job.id, "--random-selected", "1"]
            with redirect_stdout(out4):
                rc4 = sampler.main()
            self.assertEqual(rc4, 1)
        finally:
            sys.argv = old_argv

    async def test_holdout_job_excluded_from_default_evaluation_report(self):
        await self.orch.label_asset(self.job.id, "a1", "use", reviewer="Aly")
        labels_io.mark_holdout(self.orch.store.job_dir(self.job.id), by="Aly", reason="holdout")

        evaluate = _load_script("evaluate_relevance")
        out = io.StringIO()
        old_argv = sys.argv
        try:
            sys.argv = ["evaluate_relevance.py", "--projects-dir", str(self.projects_dir)]
            with redirect_stdout(out):
                evaluate.main()
        finally:
            sys.argv = old_argv
        text = out.getvalue()
        self.assertIn("No labeled data yet", text)           # the only labeled job is a holdout -> excluded by default
        self.assertIn("Excluded 1 holdout project", text)

        out2 = io.StringIO()
        old_argv = sys.argv
        try:
            sys.argv = ["evaluate_relevance.py", "--projects-dir", str(self.projects_dir), "--only-holdout"]
            with redirect_stdout(out2):
                evaluate.main()
        finally:
            sys.argv = old_argv
        self.assertIn("keyword-match [keyword-match-v1]", out2.getvalue())


if __name__ == "__main__":
    unittest.main()
