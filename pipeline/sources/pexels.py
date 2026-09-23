"""Pexels stock photos and videos (needs a free API key in PEXELS_API_KEY).

Pexels License: free for commercial use, no attribution required, but the API
terms ask for a visible link back to Pexels, and identifiable people may not be
shown in a bad light. Both are recorded/flagged rather than silently assumed.
"""
from __future__ import annotations

import os
import re

from .base import Candidate, CredentialsMissing, HttpSource, SourceContext

PHOTOS = "https://api.pexels.com/v1/search"
VIDEOS = "https://api.pexels.com/videos/search"
LICENSE = "Pexels License"
LICENSE_URL = "https://www.pexels.com/license/"


def _title_from_url(url: str) -> str:
    m = re.search(r"/(?:video|photo)/([^/]+?)-?\d*/?$", url or "")
    return (m.group(1).replace("-", " ") if m else "").strip()


class PexelsSource(HttpSource):
    name = "pexels"
    label = "Pexels (stock photos and video)"

    def __init__(self, per_query: int = 4, videos_per_query: int | None = None, videos: bool = True, api_key: str = ""):
        super().__init__(per_query, videos_per_query)
        self.videos = videos
        self._key = api_key

    def key(self) -> str:
        return self._key or os.getenv("PEXELS_API_KEY", "")

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        if not self.key():
            raise CredentialsMissing("PEXELS_API_KEY is not set")
        ctx.http.headers["Authorization"] = self.key()
        out: list[Candidate] = []
        photos = await ctx.http.get_json(PHOTOS, params={
            "query": query, "per_page": self.per_query, "orientation": "portrait", "page": ctx.page},
            purpose=f"search Pexels photos for '{query}'")
        for p in photos.get("photos", []):
            who = p.get("photographer", "unknown")
            out.append(Candidate(
                url=(p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("original", ""),
                kind="image", mime="image/jpeg", title=p.get("alt") or _title_from_url(p.get("url", "")) or f"pexels {p.get('id')}",
                description=p.get("alt", ""), page_url=p.get("url", ""), author=who,
                license=LICENSE, license_url=LICENSE_URL,
                attribution=f"Photo by {who} on Pexels ({p.get('url', '')})",
                width=p.get("width"), height=p.get("height"),
                meta={"pexels_id": p.get("id"), "photographer_url": p.get("photographer_url", ""),
                      "avg_color": p.get("avg_color", "")}))
        if self.videos and self.videos_per_query > 0:
            vids = await ctx.http.get_json(VIDEOS, params={
                "query": query, "per_page": max(1, self.videos_per_query + 1), "orientation": "portrait", "page": ctx.page},
                purpose=f"search Pexels videos for '{query}'")
            for v in vids.get("videos", []):
                files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
                if not files:
                    continue
                good = sorted((f for f in files if (f.get("width") or 0) >= 720), key=lambda f: f["width"])
                pick = good[0] if good else max(files, key=lambda f: f.get("width") or 0)
                who = (v.get("user") or {}).get("name", "unknown")
                out.append(Candidate(
                    url=pick["link"], kind="video", mime="video/mp4",
                    title=_title_from_url(v.get("url", "")) or f"pexels video {v.get('id')}",
                    description=_title_from_url(v.get("url", "")), page_url=v.get("url", ""), author=who,
                    license=LICENSE, license_url=LICENSE_URL,
                    attribution=f"Video by {who} on Pexels ({v.get('url', '')})",
                    width=pick.get("width"), height=pick.get("height"), duration=v.get("duration"),
                    meta={"pexels_id": v.get("id"), "user_url": (v.get("user") or {}).get("url", "")}))
        return out
