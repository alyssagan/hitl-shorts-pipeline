"""NASA Image and Video Library (no key needed).

NASA content is generally not copyrighted in the US, with exceptions the vetting step flags
for you: third-party material inside NASA media, identifiable people, and NASA's protected
logos. Credit line: NASA as the source. Never suggest NASA endorses your video.
"""
from __future__ import annotations

from .base import Candidate, HttpSource, SourceContext, strip_html

SEARCH = "https://images-api.nasa.gov/search"
ASSET = "https://images-api.nasa.gov/asset/{nasa_id}"
LICENSE = "Public domain (NASA; third-party material and logos excepted)"
LICENSE_URL = "https://www.nasa.gov/nasa-brand-center/images-and-media/"


def pick_file(hrefs: list[str], kind: str) -> str:
    """Best file for the renderer from an asset manifest: large jpg for images, a medium mp4 for video."""
    order = ("~large", "~medium", "~orig", "~small") if kind == "image" else ("~medium", "~large", "~small", "~mobile", "~orig")
    ext = (".jpg", ".jpeg") if kind == "image" else (".mp4",)
    for tag in order:
        for h in hrefs:
            low = h.lower().split("?")[0]
            if tag in low and low.endswith(ext):
                return h.replace(" ", "%20")
    return ""


class NasaSource(HttpSource):
    name = "nasa"
    label = "NASA Image and Video Library"

    def __init__(self, per_query: int = 4, videos_per_query: int | None = None, videos: bool = True):
        super().__init__(per_query, videos_per_query)
        self.videos = videos

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        media = "image,video" if self.videos and self.videos_per_query > 0 else "image"
        data = await ctx.http.get_json(SEARCH, params={"q": query, "media_type": media, "page": ctx.page},
                                       purpose=f"search NASA library for '{query}'")
        out: list[Candidate] = []
        for item in (data.get("collection", {}).get("items") or [])[: self.per_query * 2]:
            d = (item.get("data") or [{}])[0]
            nid, kind = d.get("nasa_id"), d.get("media_type", "image")
            if not nid or kind not in ("image", "video"):
                continue
            try:
                manifest = await ctx.http.get_json(ASSET.format(nasa_id=nid), purpose=f"list files for NASA item {nid}")
            except Exception:
                continue
            hrefs = [i.get("href", "") for i in (manifest.get("collection", {}).get("items") or [])]
            url = pick_file(hrefs, kind)
            if not url:
                continue
            title = d.get("title") or nid
            who = d.get("photographer") or d.get("secondary_creator") or d.get("center") or "NASA"
            page = f"https://images.nasa.gov/details/{nid}"
            out.append(Candidate(
                url=url, kind=kind, mime="video/mp4" if kind == "video" else "image/jpeg", title=title,
                description=strip_html(d.get("description", ""))[:400], page_url=page, author=who,
                license=LICENSE, license_url=LICENSE_URL,
                attribution=f"{title}: NASA ({who}), {page}",
                meta={"nasa_id": nid, "center": d.get("center", ""), "keywords": d.get("keywords", []),
                      "date_created": d.get("date_created", "")}))
        return out
