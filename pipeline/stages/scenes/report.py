"""SCRIPT.md: the per-job, readable record of the scene stage's output -- the full narration,
scene by scene, next to what each scene actually got matched to (search term, clip, why, audio).

Gate 3 currently shows this same information in the review UI, but until now it existed nowhere
as its own file in the project folder -- job.script/job.scenes only ever lived in job.json and
scattered decisions.jsonl entries. Same convention as SOURCES.md/CREDITS.md/VETTING_REPORT.md:
regenerated in full every time the job changes, never appended to."""
from __future__ import annotations

from pathlib import Path

from ...core.models import Job


def _cell(x: object) -> str:
    return str(x or "").replace("|", "/").replace("\n", " ").strip()


def write_script_md(job: Job, project_dir: Path) -> Path | None:
    """Nothing is written until the scene stage has actually run (`job.scenes` non-empty)."""
    if not job.scenes:
        return None

    lines = [f"# Script: {job.subject}", "",
             f"{len(job.scenes)} scene(s), {len(job.script)} characters of narration.", ""]
    for s in job.scenes:
        lines.append(f"## Scene {s.index + 1} ({'approved' if s.approved else 'not yet approved'})")
        lines.append("")
        lines.append(s.narration)
        lines.append("")
        terms = ", ".join(s.search_terms) or "(none)"
        clip = f"`{_cell(s.clip_path)}`" if s.clip_path else "(no clip matched)"
        meta = [f"**Search term(s):** {terms}", f"**Clip:** {clip}"]
        if s.asset_id:
            meta.append(f"**Asset:** {s.asset_id}")
        if s.clip_reason:
            meta.append(f"**Why this clip:** {_cell(s.clip_reason)}")
        meta.append(f"**Audio:** {'generated' if s.audio_path else 'none'}")
        if s.duration is not None:
            meta.append(f"**Duration:** {s.duration}s")
        if s.note:
            meta.append(f"**Reviewer note:** {_cell(s.note)}")
        lines.append("  \n".join(meta))
        lines.append("")

    p = project_dir / "SCRIPT.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
