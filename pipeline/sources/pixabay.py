"""Pixabay photos and videos (free API key in PIXABAY_API_KEY).

Pixabay Content License: free for commercial use, no attribution required. Not allowed:
selling unaltered copies, or implying that a person or brand endorses your video.
Pixabay's API terms ask that results are cached for 24 hours (files are stored in the project
folder) and that images are downloaded, not hot-linked (they are downloaded here).
The key travels in the query string, so it is hidden in the request log.
"""
from __future__ import annotations

import os

from .base import Candidate, CredentialsMissing, HttpSource, SourceContext

PHOTOS = "https://pixabay.com/api/"
VIDEOS = "https://pixabay.com/api/videos/"
LICENSE = "Pixabay Content License"
LICENSE_URL = "https://pixabay.com/service/license-summary/"


class PixabaySource(HttpSource):
    name = "pixabay"
    label = "Pixabay (free photos and video)"

    def __init__(self, per_query: int = 4, videos_per_query: int | None = None, videos: bool = True, api_key: str = ""):
        super().__init__(per_query, videos_per_query)
        self.videos = videos
        self._key = api_key

    def key(self) -> str:
        return self._key or os.getenv("PIXABAY_API_KEY", "")

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        if not self.key():
            raise CredentialsMissing("PIXABAY_API_KEY is not set")
        q = query[:100]
        out: list[Candidate] = []
        photos = await ctx.http.get_json(PHOTOS, params={
            "key": self.key(), "q": q, "image_type": "photo", "orientation": "vertical",
            "per_page": max(3, self.per_query), "safesearch": "true", "page": ctx.page},
            purpose=f"search Pixabay photos for '{query}'")
        for p in photos.get("hits", []):
            url = p.get("largeImageURL") or p.get("webformatURL")
            if not url:
                continue
            who = p.get("user", "unknown")
            title = (p.get("tags") or f"pixabay {p.get('id')}").strip()
            out.append(Candidate(
                url=url, kind="image", mime="image/jpeg", title=title, description=title,
                page_url=p.get("pageURL", ""), author=who, license=LICENSE, license_url=LICENSE_URL,
                attribution=f"Image by {who} from Pixabay ({p.get('pageURL', '')})",
                width=p.get("imageWidth"), height=p.get("imageHeight"),
                meta={"pixabay_id": p.get("id"), "tags": p.get("tags", "")}))
        if self.videos and self.videos_per_query > 0:
            vids = await ctx.http.get_json(VIDEOS, params={
                "key": self.key(), "q": q, "per_page": max(3, self.videos_per_query + 1), "safesearch": "true", "page": ctx.page},
                purpose=f"search Pixabay videos for '{query}'")
            for v in vids.get("hits", []):
                sizes = [s for s in (v.get("videos") or {}).values() if isinstance(s, dict) and s.get("url")]
                if not sizes:
                    continue
                good = sorted((s for s in sizes if (s.get("width") or 0) >= 720), key=lambda s: s["width"])
                pick = good[0] if good else max(sizes, key=lambda s: s.get("width") or 0)
                who = v.get("user", "unknown")
                title = (v.get("tags") or f"pixabay video {v.get('id')}").strip()
                out.append(Candidate(
                    url=pick["url"], kind="video", mime="video/mp4", title=title, description=title,
                    page_url=v.get("pageURL", ""), author=who, license=LICENSE, license_url=LICENSE_URL,
                    attribution=f"Video by {who} from Pixabay ({v.get('pageURL', '')})",
                    width=pick.get("width"), height=pick.get("height"), duration=v.get("duration"),
                    meta={"pixabay_id": v.get("id"), "tags": v.get("tags", "")}))
        return out
