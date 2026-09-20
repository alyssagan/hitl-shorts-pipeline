"""Import files that your own scraper (or you) dropped into a folder.

Each file may have an optional sidecar `<file>.json` describing it:
    {"title": "...", "source_url": "...", "license": "...", "license_url": "...",
     "author": "...", "description": "..."}
Files without a sidecar are imported with no license, which the vetting step
flags as high risk (unknown license) so a human must decide.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from ..core.models import Asset
from .base import MIME_TO_EXT, SourceContext, SourceResult, safe_name

EXT_TO_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
               "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}


class FolderSource:
    name = "folder"
    label = "Your scraper's folder"

    def __init__(self, path: str = "library/scraped"):
        self.path = Path(path)

    async def fetch(self, queries: list[str], ctx: SourceContext) -> SourceResult:
        result = SourceResult()
        note = {"source": self.name, "query": "(all files)", "found": 0, "kept": 0, "skipped": []}
        if not self.path.exists():
            note["error"] = f"folder {self.path} does not exist"
            result.trace.append(note)
            return result
        files = sorted(p for p in self.path.rglob("*") if p.is_file() and p.suffix.lower() != ".json")
        note["found"] = len(files)
        seen = set(ctx.known_hashes)
        for p in files:
            mime = EXT_TO_MIME.get(p.suffix.lower().lstrip("."))
            if not mime:
                note["skipped"].append({"url": str(p), "reason": f"format '{p.suffix}' can't be used by the renderer"})
                continue
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            if sha in seen:
                note["skipped"].append({"url": str(p), "reason": "identical file already in this project"})
                continue
            seen.add(sha)
            meta = {}
            side = p.with_name(p.name + ".json")
            if not side.exists():
                side = p.with_suffix(".json")
            if side.exists():
                try:
                    meta = json.loads(side.read_text(encoding="utf-8"))
                except ValueError:
                    note["skipped"].append({"url": str(side), "reason": "sidecar is not valid JSON; imported without metadata"})
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
                mime=mime, sha256=sha, meta={"imported_from": str(p), "sidecar": bool(meta)}))
            note["kept"] += 1
        result.trace.append(note)
        return result
