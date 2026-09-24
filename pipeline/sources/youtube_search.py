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


# Substrings yt-dlp's own error text uses for YouTube's anti-bot sign-in wall (increasingly common on
# ytsearch results since it can trip per-video, not just per-account) -- used only to add a friendlier
# hint alongside yt-dlp's own message, never to replace it. There's no fix on our end for this one: it
# would need real account cookies (--cookies-from-browser), which this app deliberately doesn't set up
# on its own (see the standing rule against introducing new paid/credentialed integrations without
# approval -- and cookies here means using someone's real YouTube login, not a service fee, but the same
# "don't wire this up silently" logic applies).
YOUTUBE_SIGNIN_HINTS = ("sign in to confirm", "confirm you're not a bot", "confirm you are not a bot")


async def search_youtube(query: str, count: int, runner: Runner = run_subprocess) -> list[dict[str, Any]]:
    """Up to `count` candidates for `query`, newest-search-result-first (yt-dlp's own ranking, not ours).
    Raises SourceUnavailable if yt-dlp isn't installed, RuntimeError for any other yt-dlp failure.

    `--ignore-errors` is on: YouTube's anti-bot sign-in wall (see YOUTUBE_SIGNIN_HINTS) can trip on one
    specific video in the result list without affecting the others, and without it yt-dlp aborts the
    *entire* search the moment the first entry in the ytsearch pseudo-playlist fails, turning one blocked
    video into zero results. So a nonzero exit code here doesn't necessarily mean the search failed --
    it's only treated as an error when nothing at all could be parsed out of stdout.
    """
    args = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--ignore-errors", "--skip-download", "--dump-json",
            "--playlist-end", str(count), f"ytsearch{count}:{query}"]
    code, out, err = await runner(args)
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
    if code != 0 and not out_list:
        if "No module named yt_dlp" in err:
            raise SourceUnavailable("yt-dlp is not installed (pip install yt-dlp)")
        tail = err.strip().splitlines()[-1][:200] if err.strip() else "no output"
        if any(h in tail.lower() for h in YOUTUBE_SIGNIN_HINTS):
            raise RuntimeError(f"YouTube search failed: every result it tried to read hit YouTube's own "
                                f"sign-in/bot check ({tail}) -- this isn't something the app can fix by "
                                f"retrying the same search; try a different or more specific query, or "
                                f"add the video by pasting its link into \"Add links\" instead")
        raise RuntimeError(f"YouTube search failed: {tail}")
    return out_list
