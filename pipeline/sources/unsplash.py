"""Unsplash photos (free access key in UNSPLASH_ACCESS_KEY). Stills only.

Unsplash License: free for commercial use, credit appreciated. CAUTION: Unsplash's API guidelines
ask apps to hot-link images and to ping a "download" endpoint when a photo is used. Here the photo is
downloaded into the project (needed for rendering) and the ping is sent for each photo we keep a
candidate for. Whether that fully meets their API terms for this kind of tool is unchecked; read
docs/KNOWN_LIMITATIONS.md before relying on this source. Demo keys allow 50 requests an hour.
"""
from __future__ import annotations

import os

from .base import Candidate, CredentialsMissing, HttpSource, SourceContext

SEARCH = "https://api.unsplash.com/search/photos"
LICENSE = "Unsplash License"
LICENSE_URL = "https://unsplash.com/license"


class UnsplashSource(HttpSource):
    name = "unsplash"
    label = "Unsplash (free photos)"

    def __init__(self, per_query: int = 3, api_key: str = ""):
        super().__init__(per_query)
        self._key = api_key

    def key(self) -> str:
        return self._key or os.getenv("UNSPLASH_ACCESS_KEY", "")

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        if not self.key():
            raise CredentialsMissing("UNSPLASH_ACCESS_KEY is not set")
        ctx.http.headers["Authorization"] = f"Client-ID {self.key()}"
        data = await ctx.http.get_json(SEARCH, params={
            "query": query, "per_page": self.per_query, "orientation": "portrait", "page": ctx.page},
            purpose=f"search Unsplash for '{query}'")
        out: list[Candidate] = []
        for p in data.get("results", []):
            url = (p.get("urls") or {}).get("regular")
            if not url:
                continue
            user = p.get("user") or {}
            who = user.get("name", "unknown")
            page = (p.get("links") or {}).get("html", "")
            title = p.get("alt_description") or p.get("description") or f"unsplash {p.get('id')}"
            ping = (p.get("links") or {}).get("download_location")
            if ping:
                try:
                    await ctx.http.get_json(ping, purpose=f"tell Unsplash photo {p.get('id')} is being downloaded")
                except Exception:
                    pass
            out.append(Candidate(
                url=url, kind="image", mime="image/jpeg", title=title, description=title, page_url=page,
                author=who, license=LICENSE, license_url=LICENSE_URL,
                attribution=f"Photo by {who} on Unsplash ({page})",
                width=p.get("width"), height=p.get("height"), meta={"unsplash_id": p.get("id")}))
        return out
