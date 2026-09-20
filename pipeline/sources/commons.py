"""Wikimedia Commons: freely licensed images, with license metadata.

Licenses on Commons are mixed (public domain, CC BY, CC BY-SA, some with
restrictions). Everything found is kept and its license recorded; the vetting
step flags what needs a human look and explains why.
"""
from __future__ import annotations

from .base import Candidate, HttpSource, SourceContext, strip_html

API = "https://commons.wikimedia.org/w/api.php"
META_KEYS = ("LicenseShortName|LicenseUrl|Artist|Credit|Attribution|AttributionRequired|Copyrighted|"
             "UsageTerms|ImageDescription|ObjectName|Restrictions|DateTimeOriginal|Categories")


class CommonsSource(HttpSource):
    name = "commons"
    label = "Wikimedia Commons (images)"

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        data = await ctx.http.get_json(API, params={
            "action": "query", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": max(self.per_query * 3, 10),
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata|user",
            "iiurlwidth": 1280, "iiextmetadatafilter": META_KEYS, "format": "json"},
            purpose=f"search Wikimedia Commons for images matching '{query}'")
        pages = sorted((data.get("query", {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0))
        out: list[Candidate] = []
        for p in pages:
            infos = p.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            md = {k: (v or {}).get("value", "") for k, v in (info.get("extmetadata") or {}).items()}
            title = p.get("title", "").removeprefix("File:").rsplit(".", 1)[0].replace("_", " ")
            author = strip_html(md.get("Artist", "")) or info.get("user", "")
            lic = md.get("LicenseShortName", "")
            lic_url = md.get("LicenseUrl", "")
            page_url = info.get("descriptionurl", "")
            credit = f'"{title}" by {author or "unknown author"}'
            attribution = f"{credit}, {lic or 'license unknown'}" + (f" ({lic_url})" if lic_url else "") + \
                          f", via Wikimedia Commons: {page_url}"
            downloaded_thumb = bool(info.get("thumburl"))
            out.append(Candidate(
                url=info.get("thumburl") or info.get("url", ""), kind="image", mime=info.get("mime", ""),
                title=title, description=strip_html(md.get("ImageDescription", "")), page_url=page_url,
                author=author, license=lic, license_url=lic_url, attribution=attribution,
                width=info.get("thumbwidth") if downloaded_thumb else info.get("width"),
                height=info.get("thumbheight") if downloaded_thumb else info.get("height"),
                meta={"extmetadata": md, "original_url": info.get("url", ""),
                      "original_size": [info.get("width"), info.get("height")]},
            ))
        return out
