#!/usr/bin/env python3
"""Pick assets for you to go label (Use/Duplicate/Irrelevant) so scripts/evaluate_relevance.py has an honest,
non-biased sample to report on -- not just whatever you happened to look at (docs/EVALUATION.md).

    # 8 random machine-selected + 8 random machine-rejected/hidden + 5 near-the-threshold extras:
    python3 scripts/sample_for_review.py sample ace07f01d680 --random-selected 8 --random-rejected 8 --borderline 5

    # set a case aside as a stable holdout -- never tune a scoring change against this, then compare against it later:
    python3 scripts/sample_for_review.py mark-holdout ace07f01d680 --by Aly --reason "first full true-crime case, keep clean for comparisons"
    python3 scripts/sample_for_review.py list-holdouts
    python3 scripts/sample_for_review.py unmark-holdout ace07f01d680

This only PICKS and TAGS candidates (writes SAMPLE_TAGS.jsonl in the project folder, append-only, never
overwritten by a later run) and prints the review page link for each -- actually labeling still happens by
clicking Use/Duplicate/Irrelevant in the review page (any job state now works, not just "assets review": the
label endpoint doesn't require the assets-review gate, see docs/EVALUATION.md). Random picks
(`random_selected`/`random_rejected`) are a plain, unbiased draw from what the machine selected/hid; borderline
picks are deliberately NOT random (closest to the relevance threshold) -- both count toward N in the report,
but tagged separately, precisely so a report never mistakes "only the uncertain ones got reviewed" for
representative coverage. By default assets already labeled, or already suggested by an earlier run of this
script, are skipped so repeated runs surface fresh candidates -- see --include-labeled / --include-resampled.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.core.store import JobNotFound, JobStore                                     # noqa: E402
from pipeline.evaluation import labels as labels_io                                       # noqa: E402
from pipeline.evaluation import sampling                                                  # noqa: E402


def cmd_sample(args: argparse.Namespace) -> int:
    store = JobStore(args.projects_dir)
    try:
        job = store.load(args.job_id)
    except JobNotFound:
        print(f"no such job: {args.job_id} (looked under {args.projects_dir})", file=sys.stderr)
        return 1
    job_dir = store.job_dir(job.id)

    if labels_io.is_holdout(job_dir) and not args.i_know_this_is_a_holdout:
        marker = labels_io.is_holdout(job_dir)
        print(f"'{job.subject}' ({job.id}) is marked as a HOLDOUT project ({marker.get('reason', '(no reason given)')}), "
              f"set aside by {marker.get('by', '?')} on {marker.get('marked_at', '?')}.", file=sys.stderr)
        print("Sampling from it for routine review risks biasing it into a set you've now looked at and tuned "
              "against, defeating the point of the holdout (docs/EVALUATION.md). Pass --i-know-this-is-a-holdout "
              "if you're doing the deliberate, one-time final comparison.", file=sys.stderr)
        return 1

    already_labeled = {r["asset_id"] for r in labels_io.latest_per_asset(labels_io.read_label_rows(job_dir))}
    already_sampled = sampling.already_sampled_ids(job_dir) if not args.include_resampled else set()

    items, seed_used = sampling.pick_sample(
        job, n_random_selected=args.random_selected, n_random_rejected=args.random_rejected, n_borderline=args.borderline,
        seed=args.seed, exclude_asset_ids=already_sampled, already_labeled_ids=already_labeled,
        include_labeled=args.include_labeled)

    if not items:
        print("Nothing to sample -- either this job has no scored assets yet, or every candidate is already "
              "labeled/previously sampled (try --include-labeled or --include-resampled).")
        return 0

    sampling.write_sample_tags(job_dir, job.id, items, seed=seed_used, by=args.by, note=args.note)

    url = f"{args.base_url.rstrip('/')}/review/{job.id}"
    print(f"{len(items)} candidate(s) for '{job.subject}' ({job.id}), seed={seed_used} (recorded in SAMPLE_TAGS.jsonl "
          f"for exact reproducibility). Open {url} and label each with Use / Duplicate / Irrelevant:\n")
    by_tag: dict[str, list[sampling.SampleItem]] = {}
    for it in items:
        by_tag.setdefault(it.sampled_as, []).append(it)
    for tag in ("random_selected", "random_rejected", "borderline"):
        rows = by_tag.get(tag, [])
        if not rows:
            continue
        print(f"-- {tag} ({len(rows)}) --")
        for it in rows:
            score = "n/a" if it.score is None else f"{round(it.score * 100)}%"
            already = "  [already labeled -- relabeling]" if it.already_labeled else ""
            print(f"  {it.asset_id}  score={score}  decision={it.machine_decision or '?'}  "
                  f"{it.scoring_method or '?'}[{it.method_version or '?'}]  \"{it.title[:60]}\"{already}")
        print()
    return 0


def cmd_mark_holdout(args: argparse.Namespace) -> int:
    store = JobStore(args.projects_dir)
    try:
        job = store.load(args.job_id)
    except JobNotFound:
        print(f"no such job: {args.job_id}", file=sys.stderr)
        return 1
    if not args.by:
        print("--by is required (who decided this is a holdout, for the record)", file=sys.stderr)
        return 1
    marker = labels_io.mark_holdout(store.job_dir(job.id), by=args.by, reason=args.reason or "")
    verb = "re-marked (was already a holdout)" if marker.get("already") else "marked"
    print(f"{verb} '{job.subject}' ({job.id}) as a holdout: {marker}")
    return 0


def cmd_unmark_holdout(args: argparse.Namespace) -> int:
    store = JobStore(args.projects_dir)
    try:
        job = store.load(args.job_id)
    except JobNotFound:
        print(f"no such job: {args.job_id}", file=sys.stderr)
        return 1
    removed = labels_io.unmark_holdout(store.job_dir(job.id))
    print(f"{'removed' if removed else 'was not'} a holdout marker for '{job.subject}' ({job.id}).")
    return 0


def cmd_list_holdouts(args: argparse.Namespace) -> int:
    projects_dir = Path(args.projects_dir)
    found = False
    for job_dir in labels_io.discover_job_dirs(projects_dir):
        marker = labels_io.is_holdout(job_dir)
        if marker:
            found = True
            print(f"{job_dir.name}: by={marker.get('by', '?')} at={marker.get('marked_at', '?')} reason={marker.get('reason', '(none)')!r}")
    if not found:
        print("no holdout projects marked.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects-dir", default=str(ROOT / "projects"), help="default: ./projects")
    ap.add_argument("--base-url", default="http://localhost:8000", help="where the review page is served (default http://localhost:8000)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="pick assets for review and tag how each was picked")
    s.add_argument("job_id")
    s.add_argument("--random-selected", type=int, default=0, help="random draw from what the machine selected (approved-by-scoring)")
    s.add_argument("--random-rejected", type=int, default=0, help="random draw from what the machine rejected/hid")
    s.add_argument("--borderline", type=int, default=0, help="extras closest to the relevance threshold, from either pool (tagged separately, not random)")
    s.add_argument("--seed", type=int, default=None, help="reproducible random draw; omit to get (and record) a fresh one each run")
    s.add_argument("--include-labeled", action="store_true", help="also consider assets you've already labeled (default: skipped)")
    s.add_argument("--include-resampled", action="store_true", help="also re-suggest assets a previous run of this script already surfaced")
    s.add_argument("--i-know-this-is-a-holdout", action="store_true", help="sample from a job marked as a holdout anyway (see docs/EVALUATION.md)")
    s.add_argument("--by", default="", help="your name, recorded on each SAMPLE_TAGS.jsonl row")
    s.add_argument("--note", default="", help="optional free-text note recorded with this batch")
    s.set_defaults(func=cmd_sample)

    m = sub.add_parser("mark-holdout", help="set a job/case aside as a stable evaluation holdout")
    m.add_argument("job_id")
    m.add_argument("--by", default="", help="your name (required)")
    m.add_argument("--reason", default="", help="why this one, for the record")
    m.set_defaults(func=cmd_mark_holdout)

    u = sub.add_parser("unmark-holdout", help="remove a job's holdout marker")
    u.add_argument("job_id")
    u.set_defaults(func=cmd_unmark_holdout)

    lh = sub.add_parser("list-holdouts", help="list every project currently marked as a holdout")
    lh.set_defaults(func=cmd_list_holdouts)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
