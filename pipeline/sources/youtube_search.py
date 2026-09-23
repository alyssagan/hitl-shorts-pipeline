"""On-demand YouTube search for Gate 2's "Find more" panel (requested directly, as the one real gap in
that panel's link-out list -- Internet Archive/Chronicling America/Commons are already automated sources,
and Google Images/FindAGrave have no API and no rights metadata worth automating).

Lists candidate videos via yt-dlp's own search syntax (`ytsearchN:query`) -- the same tool "Add links"
already uses to download a pasted URL, so this needs no paid API key, no Google Cloud project, no quota.
It is metadata only (`--skip-download`): nothing is downloaded or added to the job by this call. A reviewer
sees title/uploader/duration/thumbnail/description for each candidate and decides which (if any) are worth
pulling in. Picking one still goes through the normal "Add links" path (pipeline/api/app.py's
assets_add_url, which calls UrlListSource.fetch_one on the video's own URL) -- so it's still auto-flagged
high risk (PLATFORM_SOURCE, pipeline/vetting/rules.py) and needs a written note before approval, exactly
as if the reviewer had found and pasted the link by hand. This is deliberately NOT wired into the normal
per-round sourcing loop (`pipeline/stages/sourcing.py`): that loop downloads everything it finds and adds
it as a pending asset automatically, which is the wrong default for platform video -- bulk-downloading
search results without a person choosing each one first would multiply the exact rights exposure #9/#14
in docs/KNOWN_LIMITATIONS.md already flag for a single pasted link.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .base import SourceUnavailable
from .urls import Runner, run_subprocess


async def search_youtube(query: str, count: int, runner: Runner = run_subprocess) -> list[dict[str, Any]]:
    """Up to `count` candidates for `query`, newest-search-result-first (yt-dlp's own ranking, not ours).
    Raises SourceUnavailable if yt-dlp isn't installed, RuntimeError for any other yt-dlp failure."""
    args = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--skip-download", "--dump-json",
            "--playlist-end", str(count), f"ytsearch{count}:{query}"]
    code, out, err = await runner(args)
    if code != 0:
        if "No module named yt_dlp" in err:
            raise SourceUnavailable("yt-dlp is not installed (pip install yt-dlp)")
        tail = err.strip().splitlines()[-1][:200] if err.strip() else "no output"
        raise RuntimeError(f"YouTube search failed: {tail}")
    out_list: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            info = json.loads(line)
        except ValueError:
            continue
        vid = info.get("id", "")
        out_list.append({
            "id": vid,
            "title": info.get("title") or "(untitled)",
            "url": info.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else ""),
            "uploader": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail") or "",
            "upload_date": info.get("upload_date", ""),
            "description": (info.get("description") or "")[:300],
        })
    return out_list
