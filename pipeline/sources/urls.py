"""Your own list of URLs: pull the videos (or images) from links you paste, like RankReel.

Each entry can be a plain URL or {"url": ..., "note": ..., "position": ...}. `position` is a hint for
where it goes in the video: a scene number (1 = first scene), or words like "intro" / "end". Everything
found is kept, and the URL, page title, uploader, duration and the platform's license label are saved
in `sources/urls/manifest.json` and `sources/urls/info/`. Vetting flags platform videos as high risk:
the uploader may not own the rights, and the platform's terms may forbid downloading. Approving one
needs a written note from you.

Direct links to .mp4/.jpg/... files are downloaded as they are. Everything else goes through yt-dlp,
which supports YouTube, TikTok, X, Vimeo, Instagram, news sites and many more.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from ..core.models import Asset
from .base import SourceContext, SourceResult, SourceUnavailable, safe_name

Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]
DIRECT_EXT = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
              "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
CC_LABEL = re.compile(r"creative commons", re.I)
# Substrings yt-dlp's own error text uses when a link simply isn't a video/audio page at all (a plain
# webpage, an article, a login/paywall wall, ...) rather than a download that failed for some other reason
# (network error, deleted video, geo-block). Used only to add a friendlier hint alongside yt-dlp's own
# message -- never to replace it, since the heuristic can be wrong and the original error is the ground truth.
NOT_MEDIA_HINTS = ("unsupported url", "unable to extract", "no video formats found",
                    "requested format is not available", "no media found", "unable to download webpage")


async def run_subprocess(args: list[str], timeout: float = 900) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"timed out after {timeout:.0f}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def normalize(entry: Any, index: int) -> dict[str, Any]:
    if isinstance(entry, str):
        entry = {"url": entry}
    d = dict(entry)
    d["url"] = str(d.get("url", "")).strip()
    d["note"] = str(d.get("note", "") or "").strip()
    d["position"] = str(d.get("position", "") or "").strip()
    d["order"] = index + 1
    return d


def parse_url_lines(text: str) -> list[dict[str, str]]:
    """One URL per line; optional `| note | position`. Blank lines and # comments are ignored."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        out.append({"url": parts[0], "note": parts[1] if len(parts) > 1 else "",
                    "position": parts[2] if len(parts) > 2 else ""})
    return out


class UrlListSource:
    name = "urls"
    label = "Your list of URLs (yt-dlp)"

    def __init__(self, runner: Runner | None = None, max_mb: int = 200, max_height: int = 1080):
        self.runner = runner or run_subprocess
        self.max_mb = max_mb
        self.max_height = max_height

    async def fetch_one(self, url: str, note: str, n: int, ctx: SourceContext, position: str = "") -> Asset:
        """Extract a single URL (direct file or yt-dlp) into an Asset, outside the normal batch `fetch()` loop.
        Used by Gate 3's "drag a link onto a scene" endpoint (pipeline/core/orchestrator.add_scene_asset via
        pipeline/api/app.py's scene_from_url) and Gate 2's "Add links" endpoint (add_reviewable_asset via
        assets_add_url), each of which adds one asset to a job outside a normal sourcing round. `n` numbers the
        downloaded file so it doesn't collide with files this source has already written for this project.
        `position` is the same scene-number/"intro"/"end" hint the batch URL list supports (see module docstring)."""
        e = normalize({"url": url, "note": note, "position": position}, 0)
        return await (self._direct(url, n, e, ctx) if self._is_direct(url) else self._ytdlp(url, n, e, ctx))

    async def fetch(self, queries: list[str], ctx: SourceContext) -> SourceResult:
        entries = [normalize(e, i) for i, e in enumerate((ctx.settings.get("job_options") or {}).get("urls", []))]
        entries = [e for e in entries if e["url"]]
        if not entries:
            raise SourceUnavailable("no URLs were provided for this job (add them with --urls FILE)")
        result = SourceResult()
        seen_urls, seen_hashes = set(ctx.known_urls), set(ctx.known_hashes)
        note: dict[str, Any] = {"source": self.name, "query": "(your URL list)", "found": len(entries), "kept": 0, "skipped": [], "kind": "media"}
        for e in entries:
            url = e["url"]
            if urlparse(url).scheme not in ("http", "https"):
                note["skipped"].append({"url": url, "reason": "not an http(s) link"})
                continue
            if url in seen_urls:
                note["skipped"].append({"url": url, "reason": "already in this project"})
                continue
            n = len(ctx.known_urls) + len(result.assets) + 1
            try:
                asset = await self._direct(url, n, e, ctx) if self._is_direct(url) else await self._ytdlp(url, n, e, ctx)
            except SourceUnavailable:
                raise
            except Exception as exc:
                note["skipped"].append({"url": url, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            if asset.sha256 in seen_hashes:
                Path(asset.path).unlink(missing_ok=True)
                note["skipped"].append({"url": url, "reason": "identical file already in this project"})
                continue
            seen_urls.add(url)
            seen_hashes.add(asset.sha256)
            result.assets.append(asset)
            note["kept"] += 1
        result.trace.append(note)
        return result

    @staticmethod
    def _is_direct(url: str) -> bool:
        return urlparse(url).path.rsplit(".", 1)[-1].lower() in DIRECT_EXT

    def _meta(self, e: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"requested_url": e["url"], "url_list_note": e["note"], "position_hint": e["position"],
                "list_order": e["order"], **extra}

    async def _direct(self, url: str, n: int, e: dict[str, Any], ctx: SourceContext) -> Asset:
        ext = urlparse(url).path.rsplit(".", 1)[-1].lower()
        name = safe_name(urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        dest = ctx.dir / "files" / f"{n:03d}-{name}.{ext}"
        _size, sha = await ctx.http.download(url, dest, purpose=f"download file from your URL list: {url}")
        host = urlparse(url).netloc
        mime = DIRECT_EXT[ext]
        return Asset(
            source=self.name, kind="video" if mime.startswith("video") else "image", path=str(dest),
            rel_path=os.path.relpath(dest, ctx.project_dir), source_url=url, page_url=url, title=name.replace("-", " "),
            description=e["note"], query="(url list)", author=host, license="", license_url="",
            attribution=f"{url}", mime=mime, sha256=sha, meta=self._meta(e, host=host),
            import_method="manual_url")

    async def _ytdlp(self, url: str, n: int, e: dict[str, Any], ctx: SourceContext) -> Asset:
        files = ctx.dir / "files"
        info_dir = ctx.dir / "info"
        files.mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{n:03d}-{safe_name(urlparse(url).netloc + '-' + urlparse(url).path.strip('/').replace('/', '-'), 40)}"
        fmt = (f"bv*[height<={self.max_height}][ext=mp4]+ba[ext=m4a]/b[height<={self.max_height}][ext=mp4]/"
               f"bv*[height<={self.max_height}]+ba/b")
        args = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "--restrict-filenames",
                "-f", fmt, "--merge-output-format", "mp4", "--max-filesize", f"{self.max_mb}M",
                "--write-info-json", "-o", str(files / f"{stem}.%(ext)s"), url]
        code, out, err = await self.runner(args)
        ctx.http._log(method="yt-dlp", url=url, status=code, purpose=f"download video from your URL list: {url}",
                      stderr_tail=err.strip()[-300:])
        if code != 0 and "No module named yt_dlp" in err:
            raise SourceUnavailable("yt-dlp is not installed (pip install yt-dlp)")
        media = sorted(p for p in files.glob(f"{stem}.*") if p.suffix.lower() in (".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png"))
        if code != 0 or not media:
            tail = err.strip().splitlines()[-1][:200] if err.strip() else "no output"
            if any(h in tail.lower() for h in NOT_MEDIA_HINTS):
                raise RuntimeError(f"this doesn't look like a direct video/photo link -- yt-dlp couldn't find "
                                    f"any downloadable media on that page ({tail})")
            raise RuntimeError(f"yt-dlp could not download this link ({tail})")
        path = media[0]
        info: dict[str, Any] = {}
        info_file = files / f"{stem}.info.json"
        if info_file.exists():
            try:
                info = json.loads(info_file.read_text(encoding="utf-8"))
            except ValueError:
                pass
            info_file.replace(info_dir / info_file.name)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        title = info.get("title") or path.stem
        who = info.get("uploader") or info.get("channel") or urlparse(url).netloc
        page = info.get("webpage_url") or url
        label = info.get("license") or ""
        lic = "CC BY (label set by the uploader on the platform)" if CC_LABEL.search(label) and "attribution" in label.lower() else ""
        mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}.get(path.suffix.lower(), "image/jpeg")
        return Asset(
            source=self.name, kind="video" if mime.startswith("video") else "image", path=str(path),
            rel_path=os.path.relpath(path, ctx.project_dir), source_url=url, page_url=page, title=title,
            description=(e["note"] or (info.get("description") or "")[:300]), query="(url list)", author=who,
            license=lic, license_url="", attribution=f'"{title}" by {who}: {page}',
            width=info.get("width"), height=info.get("height"), duration=info.get("duration"), mime=mime, sha256=sha,
            meta=self._meta(e, uploader=who, upload_date=info.get("upload_date", ""), extractor=info.get("extractor_key", ""),
                            platform_license_label=label, info_file=f"info/{info_file.name}"),
            import_method="manual_url")
