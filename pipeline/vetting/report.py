"""VETTING_REPORT.md: the per-job, readable record of what the risk/relevance vetting step
actually found -- one row per pending-or-decided asset, with its risk level, every flag that
fired and why, its relevance score (if any), and the human decision made on it so far.

This is a DIFFERENT thing from docs/VETTING.md (which explains the rules themselves, once, for
the whole repo) and from RELEVANCE_LABELS.jsonl (a reviewer-label dataset used to evaluate scoring
methods, docs/EVALUATION.md) -- this file is per-project, regenerated every time the job changes,
same convention as SOURCES.md (pipeline/stages/sourcing.py) and CREDITS.md."""
from __future__ import annotations

from pathlib import Path

from ..core.models import Job


def _cell(x: object) -> str:
    return str(x or "").replace("|", "/").replace("\n", " ").strip()


def write_vetting_report(job: Job, project_dir: Path) -> Path | None:
    """Nothing is written until at least one asset has actually been vetted (`asset.vetting` set) --
    before that, this stage simply hasn't run yet this round, same guard `write_manifests` uses for
    SOURCES.md. Rewritten in full every call; never appended to."""
    vetted = [a for a in job.assets if a.vetting is not None]
    if not vetted:
        return None

    by_risk: dict[str, int] = {}
    for a in vetted:
        by_risk[a.vetting.risk] = by_risk.get(a.vetting.risk, 0) + 1
    methods_used = sorted({a.vetting.method_version for a in vetted if a.vetting.method_version})

    lines = [f"# Vetting: {job.subject}", "",
             "Risk is explainable rules (docs/VETTING.md): license, people/sensitive-content wording, resolution, "
             "duplicates, and more -- the highest-severity flag that fired sets the risk level. Relevance is a "
             "separate score (docs/SCORING.md) against the approved keywords; it never affects risk. Nothing here "
             "approves or rejects anything -- every asset still goes to a human at Gate 2.", "",
             f"**Risk breakdown:** {', '.join(f'{n} {r}' for r, n in sorted(by_risk.items(), key=lambda kv: -kv[1]))} "
             f"out of {len(vetted)} vetted.  ",
             f"**Scoring method(s) used:** {', '.join(methods_used) if methods_used else '(not yet scored)'}", "",
             "| # | Source | Title | Risk | Flags | Relevance | Your decision | Usable |",
             "|---|---|---|---|---|---|---|---|"]
    for i, a in enumerate(vetted, 1):
        v = a.vetting
        flags = "; ".join(f"{f.rule} ({f.severity}): {f.message}" for f in v.flags) or "none"
        rel = "n/a" if v.relevance is None else f"{round(v.relevance * 100)}%"
        lines.append(f"| {i} | {_cell(a.source)} | {_cell(a.title)} | {v.risk} | {_cell(flags)} | {rel} | "
                     f"{_cell(a.status)} | {'yes' if v.usable else 'no'} |")
    lines.append("")

    flagged = [a for a in vetted if a.vetting.flags]
    if flagged:
        lines += ["## Why each flag fired", ""]
        for a in flagged:
            lines.append(f"### {_cell(a.title) or a.id} ({_cell(a.source)})")
            for f in a.vetting.flags:
                lines.append(f"- **{f.rule}** ({f.severity}): {f.message}" + (f"  \n  evidence: {_cell(f.evidence)}" if f.evidence else ""))
            lines.append("")

    p = project_dir / "VETTING_REPORT.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
