"""Sourcing stage: pull assets from every source chosen for the job.

Queries come only from the keywords a human approved (plus any extra search
terms the reviewer added when rejecting a batch of assets). Each source writes
into its own folder under the project, with its own request log and manifest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..core.decisions import machine
from ..core.models import Asset, Job, TextRef
from ..sources.base import DEFAULT_USER_AGENT, LoggedHttp, SourceAdapter, SourceContext, SourceUnavailable
from .base import StageContext


class SourcingError(RuntimeError):
    pass


@dataclass
class SourcingResult:
    assets: list[Asset] = field(default_factory=list)
    references: list[TextRef] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


def queries_for(job: Job, limit: int = 5) -> list[str]:
    terms = [k.term for k in job.approved_keywords]
    terms += [q for q in job.providers.options.get("extra_queries", []) if q not in terms]
    return terms[:limit]


class SourcingStage:
    actor = machine("sourcing", "1")

    def __init__(self, adapters: dict[str, SourceAdapter], user_agent: str = DEFAULT_USER_AGENT,
                 max_queries: int = 5, transport: httpx.AsyncBaseTransport | None = None):
        self.adapters = adapters
        self.user_agent = user_agent
        self.max_queries = max_queries
        self.transport = transport

    async def run(self, job: Job, ctx: StageContext) -> SourcingResult:
        if ctx.project_dir is None:
            raise SourcingError("project folder unknown")
        queries = queries_for(job, self.max_queries)
        if not queries:
            raise SourcingError("no approved keywords to search for")
        out = SourcingResult(queries=queries)
        wanted = queries_for(job, 10_000)
        if len(wanted) > len(queries):
            dropped = wanted[len(queries):]
            out.trace.append({"warning": f"max_queries={self.max_queries}: these approved terms were NOT searched: {dropped}. "
                                         "Raise [sources] max_queries in config/pipeline.toml or approve fewer keywords."})
        # How many times each (source, search) was already run: "search again" asks for the next page
        # of results instead of the same first page, so it brings new items instead of repeats.
        prior: dict[str, int] = {}
        for n in job.source_notes:
            if n.get("source") and n.get("query") and not n.get("error") and not n.get("skipped_source"):
                key = f"{n['source']}|{n['query']}"
                prior[key] = prior.get(key, 0) + 1
        problems = 0
        for name in job.providers.sources:
            adapter = self.adapters.get(name)
            if adapter is None:
                out.trace.append({"source": name, "error": f"unknown source '{name}'"})
                problems += 1
                continue
            src_dir = ctx.project_dir / "sources" / name
            (src_dir / "files").mkdir(parents=True, exist_ok=True)
            known = [a for a in job.assets if a.source == name]
            sctx = SourceContext(
                project_dir=ctx.project_dir, dir=src_dir, subject=job.subject, settings={**ctx.settings, "job_options": job.providers.options, "prior_searches": prior},
                http=LoggedHttp(src_dir, name, user_agent=self.user_agent, transport=self.transport),
                known_urls={a.source_url for a in known} | {r.url for r in job.references if r.source == name},
                known_hashes={a.sha256 for a in job.assets if a.sha256},
            )
            try:
                res = await adapter.fetch(queries, sctx)
            except SourceUnavailable as exc:
                out.trace.append({"source": name, "skipped_source": str(exc)})
                problems += 1
                continue
            except Exception as exc:
                out.trace.append({"source": name, "error": f"{type(exc).__name__}: {exc}"})
                problems += 1
                continue
            out.assets.extend(res.assets)
            out.references.extend(res.references)
            out.trace.extend(res.trace)
            # A source whose every query errored counts as a problem.
            if res.trace and all(t.get("error") for t in res.trace) and not res.assets and not res.references:
                problems += 1
        if not out.assets and not out.references and problems and problems >= len(job.providers.sources):
            detail = "; ".join(str(t.get("error") or t.get("skipped_source")) for t in out.trace if t.get("error") or t.get("skipped_source"))
            raise SourcingError(f"nothing could be sourced: {detail}")
        return out


def write_manifests(job: Job, project_dir: Path) -> None:
    """Write sources/<name>/manifest.json for each source, from the job's current
    assets (including vetting flags and review decisions)."""
    by_source: dict[str, dict[str, list]] = {}
    for a in job.assets:
        by_source.setdefault(a.source, {"assets": [], "references": []})["assets"].append(a.model_dump(mode="json"))
    for r in job.references:
        by_source.setdefault(r.source, {"assets": [], "references": []})["references"].append(r.model_dump(mode="json"))
    for name, data in by_source.items():
        d = project_dir / "sources" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps(
            {"source": name, "project": job.slug, "subject": job.subject, **data}, indent=2, ensure_ascii=False), encoding="utf-8")
    if job.assets or job.references or job.source_notes:
        write_sources_md(job, project_dir)


def _short_credit(a) -> str:
    """One compact line for a video description."""
    if a.author or a.attribution:
        who = a.author or a.attribution
        lic = f" ({a.license})" if a.license else ""
        return f"Photo: {who}{lic}" if a.kind != "video" else f"Video: {who}{lic}"
    return ""


def description_block(job: Job) -> str:
    """Paste-ready credits for a YouTube Shorts / TikTok description. Only assets that need
    a credit get a line; public-domain/CC0 items need none and are left out."""
    approved = job.approved_assets
    lines, licenses, seen = [], {}, set()
    for a in approved:
        line = _short_credit(a)
        kind = f"{a.license} {a.license_url}".lower()
        needs = any(k in kind for k in ("by", "sa", "gfdl", "pexels")) and "cc0" not in kind and "public domain" not in kind
        if line and needs and line not in seen:
            seen.add(line)
            lines.append(line)
            if a.license and a.license_url:
                licenses[a.license] = a.license_url
    text_refs = [r for r in job.references if r.license]
    for r in text_refs:
        line = f"Text: {r.title} ({r.license}), {r.url}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
            if r.license_url:
                licenses[r.license] = r.license_url
    if not lines:
        return ""
    lines += [f"{name}: {url}" for name, url in sorted(licenses.items())]
    return "\n".join(lines)


def write_sources_md(job: Job, project_dir: Path) -> Path:
    """SOURCES.md: everything that happened while gathering material, in one readable file.

    It has four parts: what was searched in each source, every photo/clip/text that was kept (approved
    or not, and where in the video it is used), everything that was found but NOT kept and why, and the
    sources that could not run. It is rewritten whenever the job changes."""
    def cell(x: object) -> str:
        return str(x or "").replace("|", "/").replace("\n", " ").strip()

    used: dict[str, list[int]] = {}
    for sc in job.scenes:
        key = sc.asset_id or sc.clip_path
        if key:
            used.setdefault(key, []).append(sc.index + 1)

    lines = [f"# Sources: {job.subject}", "",
             "Everything gathered for this project and where it came from. Files are in `sources/<name>/files/`; each source",
             "also has `manifest.json` (full details) and `requests.jsonl` (every request made, with status).", ""]

    notes = job.source_notes
    searched = [n for n in notes if "query" in n and not n.get("skipped_source")]
    if searched:
        lines += ["## What was searched", "",
                  "| Source | Search | Results page | Found | Kept | Not kept | Problem |", "|---|---|---|---|---|---|---|"]
        for n in searched:
            lines.append(f"| {cell(n.get('source'))} | {cell(n.get('query'))} | {n.get('page', 1)} | {n.get('found', 0)} | {n.get('kept', 0)} | "
                         f"{len(n.get('skipped') or [])} | {cell(n.get('error'))} |")
        lines.append("")
    unavailable = [n for n in notes if n.get("skipped_source") or (n.get("error") and "query" not in n)]
    if unavailable:
        lines += ["## Sources that could not run", ""]
        for n in unavailable:
            lines.append(f"- **{cell(n.get('source'))}**: {cell(n.get('skipped_source') or n.get('error'))}")
        lines.append("")

    if job.assets:
        lines += ["## Photos and video kept", "",
                  "| # | Source | Title | Page | License | By | Risk | Your decision | Used in scene | File |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for i, a in enumerate(job.assets, 1):
            risk = a.vetting.risk if a.vetting else ""
            where = ", ".join(str(n) for n in sorted(set(used.get(a.id, []) + used.get(a.path, [])))) or ""
            lines.append(f"| {i} | {cell(a.source)} | {cell(a.title)} | {cell(a.page_url or a.source_url)} | "
                         f"{cell(a.license) or 'none found'} | {cell(a.author)} | {risk} | {cell(a.status)} | {where} | `{cell(a.rel_path)}` |")
        lines.append("")
    if job.references:
        lines += ["## Text kept", "", "| Source | Title | Page | License | File |", "|---|---|---|---|---|"]
        for r in job.references:
            lines.append(f"| {cell(r.source)} | {cell(r.title)} | {cell(r.url)} | {cell(r.license)} | `{cell(r.rel_path)}` |")
        lines.append("")

    skipped = [(n.get("source"), n.get("query"), sk) for n in notes for sk in (n.get("skipped") or [])]
    if skipped:
        lines += ["## Found but not kept", "",
                  "| Source | Search | Link | Why not |", "|---|---|---|---|"]
        for src, q, sk in skipped:
            lines.append(f"| {cell(src)} | {cell(q)} | {cell(sk.get('url'))} | {cell(sk.get('reason'))} |")
        lines.append("")
    if not (job.assets or job.references or notes):
        lines.append("Nothing has been gathered yet.")
    p = project_dir / "SOURCES.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def write_credits(job: Job, project_dir: Path) -> Path:
    """CREDITS.md: attribution for every approved asset and every text reference used.
    Also writes DESCRIPTION_CREDITS.txt, a paste-ready block for the video description."""
    lines = [f"# Credits: {job.subject}", "",
             "Publish these credits with the video wherever the license or platform terms require it.", ""]
    block = description_block(job)
    (project_dir / "DESCRIPTION_CREDITS.txt").write_text((block + "\n") if block else "", encoding="utf-8")
    if block:
        lines += ["## Paste into your video description", "", "```", block, "```", ""]
    approved = job.approved_assets
    if approved:
        lines += ["## Images and video (full detail)", ""]
        for a in approved:
            lines.append(f"- {a.attribution or a.title or a.source_url}  \n  license: {a.license or 'unknown'} · file: `{a.rel_path}`")
        lines.append("")
    if job.references:
        lines += ["## Text sources", ""]
        for r in job.references:
            lines.append(f"- \"{r.title}\", {r.url}. {r.license or 'license unknown'} {r.license_url}".rstrip())
        lines.append("")
    p = project_dir / "CREDITS.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
