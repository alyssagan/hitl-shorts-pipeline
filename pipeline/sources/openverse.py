"""Openverse (openverse.org): a search engine over hundreds of millions of openly-licensed images,
aggregating Flickr, Wikimedia Commons, museums, libraries and more into one index. Unlike a general stock
site, everything Openverse indexes is already CC-licensed or public domain -- there's no "all rights
reserved" mixed in to filter out.

No API key is required for normal use. Openverse rate-limits unauthenticated requests more tightly than a
request from a registered app would; if this starts returning 429s often, Openverse supports registering a
free app for higher limits (https://api.openverse.org/v1/auth_tokens/register/) -- ask and it can be wired
in, but isn't needed to get started.
"""
from __future__ import annotations

from .base import Candidate, HttpSource, SourceContext, cc_name

SEARCH = "https://api.openverse.org/v1/images/"
FILETYPE_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


def _license_label(lic: str, version: str, url: str) -> str:
    """A readable license name. Prefers Openverse's own `license_url` (usually a creativecommons.org link,
    same as Commons/Archive) via cc_name(); falls back to building one from the short `license`/
    `license_version` codes Openverse always provides even when there's no recognizable URL."""
    name = cc_name(url)
    if name:
        return name
    lic = (lic or "").lower()
    if lic == "cc0":
        return f"CC0 {version}".strip()
    if lic == "pdm":
        return f"Public Domain Mark {version}".strip()
    return f"CC {lic.upper()} {version}".strip() if lic else ""


class OpenverseSource(HttpSource):
    name = "openverse"
    label = "Openverse (CC-licensed image search)"

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        data = await ctx.http.get_json(SEARCH, params={
            "q": query, "page": ctx.page, "page_size": max(self.per_query, 3), "mature": "false"},
            purpose=f"search Openverse for '{query}'")
        out: list[Candidate] = []
        for item in data.get("results", []):
            url = item.get("url")
            if not url:
                continue
            lic, ver, lic_url = item.get("license", ""), item.get("license_version", ""), item.get("license_url", "")
            who = item.get("creator") or "unknown"
            title = item.get("title") or f"openverse {item.get('id', '')}"
            page = item.get("foreign_landing_url", "")
            label = _license_label(lic, ver, lic_url)
            origin = item.get("source") or item.get("provider") or "Openverse"
            tags = [t.get("name") for t in (item.get("tags") or []) if isinstance(t, dict) and t.get("name")]
            out.append(Candidate(
                url=url, kind="image", mime=FILETYPE_MIME.get((item.get("filetype") or "").lower(), "image/jpeg"),
                title=title, description=title, page_url=page, author=who,
                license=label, license_url=lic_url,
                attribution=f'"{title}" by {who}, {label or "license unknown"}, via {origin} (found on Openverse): {page}',
                width=item.get("width"), height=item.get("height"),
                meta={"openverse_id": item.get("id", ""), "provider": item.get("provider", ""),
                      "source": item.get("source", ""), "tags": tags}))
        return out
