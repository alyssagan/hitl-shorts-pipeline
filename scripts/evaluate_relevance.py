#!/usr/bin/env python3
"""How good is a relevance-scoring method actually doing at finding assets you want to Use? (docs/EVALUATION.md)

    python3 scripts/evaluate_relevance.py
    python3 scripts/evaluate_relevance.py --job ace07f01d680 --job 9c1e2f...
    python3 scripts/evaluate_relevance.py --include-holdout
    python3 scripts/evaluate_relevance.py --only-holdout
    python3 scripts/evaluate_relevance.py --out /tmp/report.md

Reads RELEVANCE_LABELS.jsonl from every project under `projects/` (override with --projects-dir), collapses
relabels to the latest per asset, groups by (scoring_method, method_version) -- with rows that have neither
(older data, or a never-scored asset) in their own "(unversioned / missing provenance)" bucket -- and prints
Use yield / Irrelevant selection rate / Duplicate selection rate / Missed Use items / relevance agreement,
false-positive and false-negative rates / duplicate-flag precision and recall, each with the sample size it's
built from. Every number comes from what was recorded AT LABEL TIME (the threshold and machine decision that
actually applied then); this script never recalculates a historical score against today's config.

No database, nothing else has to be running -- it just reads the same flat files the pipeline already writes.

Projects marked as a holdout (`scripts/sample_for_review.py mark-holdout`) are excluded from the default report
so a scoring change can never be (even accidentally) tuned against them and then presented as an independent
test -- see docs/EVALUATION.md for the discipline this is meant to protect. Use --include-holdout to fold them
into the same report, or --only-holdout to see JUST the holdout set on its own (e.g. the one time you actually
do the honest comparison: freeze a candidate version, run it against the holdout project(s), evaluate here).

Example output (abbreviated):

    # Relevance-scoring evaluation

    Scanned 3 project(s), 2 with at least one label. 41 current (deduped) labeled row(s) considered.

    ## tfidf [tfidf-v1]

    - N = 28  (use=19, duplicate=2, irrelevant=7)
    - Threshold at scoring time: 50%

    - Use yield: 82.6% (n=23)  -- of what the machine selected, how much you actually want to use
    - Irrelevant selection rate: 13.0% (n=23)  -- of what the machine selected, how much you called irrelevant
    - Duplicate selection rate: 4.3% (n=23)  -- ...
    - Missed Use items: 20.0% (n=5)  -- of what the machine rejected/hid, how much you'd still have used

    - Relevance agreement (use/irrelevant labels only, vs. machine relevant/not_relevant): 84.6% (n=26)
      - False positives (machine selected, you said irrelevant): 13.0% (n=23)
      - False negatives (machine rejected, you said use): 33.3% (n=3 ⚠ sparse)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.evaluation import labels as labels_io          # noqa: E402
from pipeline.evaluation.report import build_report, format_report, DEFAULT_SPARSE_N   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects-dir", default=str(ROOT / "projects"), help="default: ./projects")
    ap.add_argument("--job", action="append", default=[], help="restrict to this job id (repeatable); default: every project")
    ap.add_argument("--include-holdout", action="store_true", help="fold holdout project(s) into the same report instead of excluding them")
    ap.add_argument("--only-holdout", action="store_true", help="report ONLY the holdout project(s), on their own")
    ap.add_argument("--sparse-n", type=int, default=DEFAULT_SPARSE_N, help=f"warn on any percentage backed by fewer than this many rows (default {DEFAULT_SPARSE_N})")
    ap.add_argument("--out", default="", help="also write the report to this file (markdown)")
    args = ap.parse_args()

    projects_dir = Path(args.projects_dir)
    if not projects_dir.exists():
        print(f"no such projects directory: {projects_dir}", file=sys.stderr)
        return 1

    loaded = labels_io.read_all_label_rows(projects_dir, job_ids=args.job or None)
    current = labels_io.latest_per_asset(loaded.rows)

    holdout_rows = [r for r in current if r.get("_job_dir") in loaded.holdout_job_dirs]
    normal_rows = [r for r in current if r.get("_job_dir") not in loaded.holdout_job_dirs]

    if args.only_holdout:
        rows, title = holdout_rows, "Relevance-scoring evaluation -- HOLDOUT set only"
        holdout_jobs_excluded = holdout_rows_excluded = 0
    elif args.include_holdout:
        rows, title = current, "Relevance-scoring evaluation -- including holdout project(s)"
        holdout_jobs_excluded = holdout_rows_excluded = 0
    else:
        rows, title = normal_rows, "Relevance-scoring evaluation"
        holdout_jobs_excluded = len(loaded.holdout_job_dirs)
        holdout_rows_excluded = len(holdout_rows)

    report = build_report(rows, jobs_scanned=loaded.jobs_scanned, jobs_with_labels=loaded.jobs_with_labels,
                           holdout_jobs_excluded=holdout_jobs_excluded, holdout_rows_excluded=holdout_rows_excluded)
    text = format_report(report, sparse_n=args.sparse_n, title=title)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\n(also written to {args.out})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
