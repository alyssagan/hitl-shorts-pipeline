"""Computes the evaluation report from RELEVANCE_LABELS.jsonl rows (docs/EVALUATION.md): is a given scoring
method+version actually finding what you want to Use, and how often is it wrong in each direction.

Every number here comes from fields already recorded ON THE ROW at label time (`machine_decision`,
`relevance_threshold`, `scoring_method`, `method_version` -- see `Orchestrator._write_label`) -- this module
NEVER recomputes a historical score or decision against today's config. A version bump changes what NEW rows
say; it never changes what an old row says happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UNVERSIONED_KEY = "(unversioned / missing provenance)"
DEFAULT_SPARSE_N = 5          # a percentage backed by fewer labeled rows than this gets a "(sparse)" warning


def _pct(n: int, d: int) -> float | None:
    return None if d == 0 else n / d


@dataclass
class RateStat:
    """One percentage, with the sample size it's built from -- the spec's "sample size N shown for every
    percentage with sparse-data warnings" (docs/EVALUATION.md)."""
    n: int              # numerator
    d: int               # denominator ("machine-selected", "machine-rejected", "comparable", etc. -- see caller)
    label: str = ""       # what the denominator means, for display

    @property
    def rate(self) -> float | None:
        return _pct(self.n, self.d)

    def sparse(self, sparse_n: int = DEFAULT_SPARSE_N) -> bool:
        return self.d < sparse_n

    def render(self, sparse_n: int = DEFAULT_SPARSE_N) -> str:
        if self.d == 0:
            return f"n/a (n=0{', ' + self.label if self.label else ''})"
        pct = round(self.rate * 100, 1)
        warn = " ⚠ sparse" if self.sparse(sparse_n) else ""
        return f"{pct}% (n={self.d}{warn})"


@dataclass
class GroupReport:
    scoring_method: str
    method_version: str
    n: int = 0                                    # total labeled rows (current, deduped) in this group
    use: int = 0
    duplicate: int = 0
    irrelevant: int = 0
    thresholds_seen: set[float] = field(default_factory=set)
    unknown_machine_decision: int = 0             # labeled rows with no machine_decision on record (old data)

    # denominator = machine-selected labeled rows (machine_decision == "relevant") -- label distribution among
    # what the machine put forward.
    use_yield: RateStat = field(default=None)
    irrelevant_selection_rate: RateStat = field(default=None)
    duplicate_selection_rate: RateStat = field(default=None)

    # denominator = machine-rejected labeled rows (machine_decision == "not_relevant"), any label.
    missed_use: RateStat = field(default=None)

    # denominator = "comparable" rows only: label in {use, irrelevant} AND machine_decision known. Duplicate-
    # labeled rows are excluded entirely here (docs/EVALUATION.md: a duplicate may still be relevant, so it
    # isn't evidence either way about relevance accuracy).
    agreement: RateStat = field(default=None)
    false_positive: RateStat = field(default=None)     # machine selected it, human says irrelevant
    false_negative: RateStat = field(default=None)      # machine rejected it, human says use

    # Duplicate detection, evaluated separately against its own machine signal (the DUPLICATE flag), never
    # folded into the relevance agreement numbers above.
    dup_flag_precision: RateStat = field(default=None)   # of flag-fired rows, how many labeled duplicate
    dup_flag_recall: RateStat = field(default=None)       # of duplicate-labeled rows, how many had the flag fired

    @property
    def key(self) -> str:
        if not self.scoring_method:
            return UNVERSIONED_KEY
        return f"{self.scoring_method} [{self.method_version or '?'}]"


@dataclass
class EvaluationReport:
    groups: list[GroupReport]
    total_rows_considered: int
    holdout_jobs_excluded: int
    holdout_rows_excluded: int
    jobs_scanned: int
    jobs_with_labels: int


def _group_key(row: dict[str, Any]) -> tuple[str, str]:
    sm = (row.get("scoring_method") or "").strip()
    mv = (row.get("method_version") or "").strip()
    if not sm:
        return "", ""
    return sm, mv


def build_report(rows: list[dict[str, Any]], *, jobs_scanned: int = 0, jobs_with_labels: int = 0,
                  holdout_jobs_excluded: int = 0, holdout_rows_excluded: int = 0) -> EvaluationReport:
    """`rows` must already be deduped to latest-per-asset (`labels.latest_per_asset`) and already have any
    holdout rows filtered out by the caller if that's wanted -- this function just computes group metrics over
    whatever rows it's handed, so it's equally usable for the normal report and for a `--only-holdout` run."""
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        by_group.setdefault(_group_key(r), []).append(r)

    groups: list[GroupReport] = []
    for (sm, mv), grows in by_group.items():
        g = GroupReport(scoring_method=sm, method_version=mv, n=len(grows))
        selected, rejected, unknown = [], [], []
        comparable = []
        for r in grows:
            label = r.get("label")
            if label == "use":
                g.use += 1
            elif label == "duplicate":
                g.duplicate += 1
            elif label == "irrelevant":
                g.irrelevant += 1
            th = r.get("relevance_threshold")
            if isinstance(th, (int, float)):
                g.thresholds_seen.add(th)
            md = r.get("machine_decision") or ""
            if md == "relevant":
                selected.append(r)
            elif md == "not_relevant":
                rejected.append(r)
            else:
                unknown.append(r)
            if label in ("use", "irrelevant") and md in ("relevant", "not_relevant"):
                comparable.append(r)
        g.unknown_machine_decision = len(unknown)

        g.use_yield = RateStat(sum(1 for r in selected if r.get("label") == "use"), len(selected), "machine-selected")
        g.irrelevant_selection_rate = RateStat(sum(1 for r in selected if r.get("label") == "irrelevant"), len(selected), "machine-selected")
        g.duplicate_selection_rate = RateStat(sum(1 for r in selected if r.get("label") == "duplicate"), len(selected), "machine-selected")
        g.missed_use = RateStat(sum(1 for r in rejected if r.get("label") == "use"), len(rejected), "machine-rejected")

        agree_n = sum(1 for r in comparable if (r.get("label") == "use") == (r.get("machine_decision") == "relevant"))
        g.agreement = RateStat(agree_n, len(comparable), "comparable")
        comparable_selected = [r for r in comparable if r.get("machine_decision") == "relevant"]
        comparable_rejected = [r for r in comparable if r.get("machine_decision") == "not_relevant"]
        g.false_positive = RateStat(sum(1 for r in comparable_selected if r.get("label") == "irrelevant"),
                                     len(comparable_selected), "comparable, machine-selected")
        g.false_negative = RateStat(sum(1 for r in comparable_rejected if r.get("label") == "use"),
                                     len(comparable_rejected), "comparable, machine-rejected")

        flagged = [r for r in grows if r.get("duplicate_flag_fired")]
        dup_labeled = [r for r in grows if r.get("label") == "duplicate"]
        g.dup_flag_precision = RateStat(sum(1 for r in flagged if r.get("label") == "duplicate"), len(flagged), "DUPLICATE-flagged")
        g.dup_flag_recall = RateStat(sum(1 for r in dup_labeled if r.get("duplicate_flag_fired")), len(dup_labeled), "labeled duplicate")

        groups.append(g)

    groups.sort(key=lambda g: (g.key == UNVERSIONED_KEY, g.key))
    return EvaluationReport(groups=groups, total_rows_considered=len(rows), jobs_scanned=jobs_scanned,
                             jobs_with_labels=jobs_with_labels, holdout_jobs_excluded=holdout_jobs_excluded,
                             holdout_rows_excluded=holdout_rows_excluded)


def format_report(report: EvaluationReport, *, sparse_n: int = DEFAULT_SPARSE_N, title: str = "Relevance-scoring evaluation") -> str:
    lines: list[str] = [f"# {title}", ""]
    lines.append(f"Scanned {report.jobs_scanned} project(s), {report.jobs_with_labels} with at least one label. "
                 f"{report.total_rows_considered} current (deduped) labeled row(s) considered.")
    if report.holdout_jobs_excluded:
        lines.append(f"Excluded {report.holdout_jobs_excluded} holdout project(s) ({report.holdout_rows_excluded} row(s)) "
                     f"from this report -- run with --include-holdout or --only-holdout to see them (docs/EVALUATION.md).")
    lines.append("")
    if not report.groups:
        lines.append("No labeled data yet. Label some assets Use / Duplicate / Irrelevant in the review page, then re-run this.")
        return "\n".join(lines)

    for g in report.groups:
        lines.append(f"## {g.key}")
        if g.key == UNVERSIONED_KEY:
            lines.append("_Rows with no recorded scoring_method/method_version -- older data from before this tracking existed, "
                         "or an asset that was never scored. Nothing here is guessed; see docs/SCORING_CHANGELOG.md._")
        lines.append("")
        lines.append(f"- N = {g.n}  (use={g.use}, duplicate={g.duplicate}, irrelevant={g.irrelevant})")
        if g.unknown_machine_decision:
            lines.append(f"- {g.unknown_machine_decision} of these have no recorded machine decision (older data) and are "
                         f"excluded from the yield/missed-use/agreement denominators below.")
        if g.thresholds_seen:
            th = sorted(g.thresholds_seen)
            th_text = f"{round(th[0]*100)}%" if len(th) == 1 else f"{round(th[0]*100)}%-{round(th[-1]*100)}% (varied across jobs/rounds)"
            lines.append(f"- Threshold at scoring time: {th_text} (recorded per row, never recalculated against today's config)")
        lines.append("")
        lines.append(f"- Use yield: {g.use_yield.render(sparse_n)}  -- of what the machine selected, how much you actually want to use")
        lines.append(f"- Irrelevant selection rate: {g.irrelevant_selection_rate.render(sparse_n)}  -- of what the machine selected, how much you called irrelevant")
        lines.append(f"- Duplicate selection rate: {g.duplicate_selection_rate.render(sparse_n)}  -- of what the machine selected, how much was a duplicate (not necessarily wrong -- a duplicate may still be relevant)")
        lines.append(f"- Missed Use items: {g.missed_use.render(sparse_n)}  -- of what the machine rejected/hid, how much you'd still have used")
        lines.append("")
        lines.append(f"- Relevance agreement (use/irrelevant labels only, vs. machine relevant/not_relevant): {g.agreement.render(sparse_n)}")
        lines.append(f"  - False positives (machine selected, you said irrelevant): {g.false_positive.render(sparse_n)}")
        lines.append(f"  - False negatives (machine rejected, you said use): {g.false_negative.render(sparse_n)}")
        lines.append("")
        lines.append(f"- Duplicate-flag precision (of DUPLICATE-flagged items, labeled duplicate): {g.dup_flag_precision.render(sparse_n)}")
        lines.append(f"- Duplicate-flag recall (of duplicate-labeled items, DUPLICATE flag had fired): {g.dup_flag_recall.render(sparse_n)}")
        lines.append("")
    return "\n".join(lines)
