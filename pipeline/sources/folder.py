"""Import files that your own scraper (or you) dropped into a folder -- but only the ones you explicitly
pick for THIS job (#10). Every job that lists `folder` used to get the WHOLE folder's contents with no
per-job filtering at all: start an unrelated job with `folder` in its sources and it would silently pull in
whatever an earlier case's scraper left there. `fetch()` below now imports nothing unless the job's
`providers.options["folder_files"]` says which relative paths to use -- set via
`Orchestrator.reject_assets(..., folder_files=[...])` / `POST /jobs/{id}/assets/reject` (mirrors how a pasted
URL list is queued), after browsing what's available with `list_available()` / `GET /jobs/{id}/folder-files`.

Each file may have an optional sidecar `<file>.json` describing it:
    {"title": "...", "source_url": "...", "license": "...", "license_url": "...",
     "author": "...", "description": "..."}
Files without a sidecar are imported with no license, which the vetting step
flags as high risk (unknown license) so a human must decide.

Picking a file this way IS the explicit "this is my own material" action (#10): every asset imported here is
stamped `import_method="local_folder"` and `owner_submitted=True`, with your optional selection note carried
as `owner_note` -- never inferred, only ever set because you chose this specific file for this specific job.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from ..core.models import Asset
from .base import MIME_TO_EXT, SourceContext, SourceResult, safe_name

EXT_TO_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
               "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}


def normalize_selection(entry: Any) -> dict[str, str]:
    """A `folder_files` entry can be a plain relative-path string or {"path": ..., "note": ...} -- same
    shape as pipeline/sources/urls.py's URL-list entries, for the same reason (a human's reason for
    including something is worth keeping next to it)."""
    if isinstance(entry, str):
        entry = {"path": entry}
    d = dict(entry)
    d["path"] = str(d.get("path", "")).strip().lstrip("/")
    d["note"] = str(d.get("note", "") or "").strip()
    return d


def sidecar_meta(p: Path) -> dict[str, Any]:
    side = p.with_name(p.name + ".json")
    if not side.exists():
        side = p.with_suffix(".json")
    if not side.exists():
        return {}
    try:
        return json.loads(side.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def list_available(root: Path) -> list[dict[str, Any]]:
    """Every importable file currently sitting in `root` (recursively) -- relative path, size, kind and any
    sidecar title -- for a human to choose from when picking files for a specific job (#10). Read-only: never
    touches a job, never imports anything by itself. Used by `GET /jobs/{id}/folder-files`."""
    if not root.exists():
        return []
    out = []
    for p in sorted(f for f in root.rglob("*") if f.is_file() and f.suffix.lower() != ".json"):
        mime = EXT_TO_MIME.get(p.suffix.lower().lstrip("."))
        meta = sidecar_meta(p)
        out.append({"path": str(p.relative_to(root)), "size": p.stat().st_size,
                    "kind": "video" if mime and mime.startswith("video") else ("image" if mime else None),
                    "usable": mime is not None, "title": meta.get("title", ""), "has_sidecar": bool(meta)})
    return out


class FolderSource:
    name = "folder"
    label = "Your scraper's folder"

    def __init__(self, path: str = "library/scraped"):
        self.path = Path(path)

    async def fetch(self, queries: list[str], ctx: SourceContext) -> SourceResult:
        result = SourceResult()
        note = {"source": self.name, "query": "(selected files)", "found": 0, "kept": 0, "skipped": [], "kind": "media"}
        root = self.path.resolve()
        if not self.path.exists():
            note["error"] = f"folder {self.path} does not exist"
            result.trace.append(note)
            return result
        selection = [e for e in (normalize_selection(raw) for raw in (ctx.settings.get("job_options") or {}).get("folder_files", []))
                     if e["path"]]
        if not selection:
            note["error"] = (f"no files were selected for this job from {self.path} -- the whole folder is no "
                              "longer imported automatically (#10). Pick specific files for this job first "
                              "(GET the available ones, then reject_assets(..., folder_files=[...])).")
            result.trace.append(note)
            return result
        note["found"] = len(selection)
        seen = set(ctx.known_hashes)
        for e in selection:
            p = (self.path / e["path"]).resolve()
            try:
                p.relative_to(root)
            except ValueError:
                note["skipped"].append({"url": e["path"], "reason": "outside the configured folder, refused"})
                continue
            if not p.is_file():
                note["skipped"].append({"url": e["path"], "reason": "file not found in the folder"})
                continue
            mime = EXT_TO_MIME.get(p.suffix.lower().lstrip("."))
            if not mime:
                note["skipped"].append({"url": str(p), "reason": f"format '{p.suffix}' can't be used by the renderer"})
                continue
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            if sha in seen:
                note["skipped"].append({"url": str(p), "reason": "identical file already in this project"})
                continue
            seen.add(sha)
            meta = sidecar_meta(p)
            ext = MIME_TO_EXT[mime]
            dest = Path(ctx.dir) / "files" / f"{len(result.assets) + len(ctx.known_urls) + 1:03d}-{safe_name(p.stem)}.{ext}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            result.assets.append(Asset(
                source=self.name, kind="video" if mime.startswith("video") else "image",
                path=str(dest), rel_path=os.path.relpath(dest, ctx.project_dir),
                source_url=meta.get("source_url") or f"file://{p}", page_url=meta.get("source_url", ""),
                title=meta.get("title") or p.stem, description=meta.get("description", ""),
                query="(imported)", author=meta.get("author", ""), license=meta.get("license", ""),
                license_url=meta.get("license_url", ""), attribution=meta.get("attribution", ""),
                mime=mime, sha256=sha, meta={"imported_from": str(p), "sidecar": bool(meta), "selected_path": e["path"]},
                import_method="local_folder", owner_submitted=True, owner_note=e["note"]))
            note["kept"] += 1
        result.trace.append(note)
        return result
