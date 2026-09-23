"""Flickr: one of the largest tagged photo archives on the internet -- including Flickr Commons, where
libraries, archives and museums (the Library of Congress among them) publish historical photos tagged "no
known copyright restrictions." That's often exactly the kind of real period photo a true-crime case needs,
and it's the main reason to add Flickr alongside the pure-archive sources.

Needs a free API key: https://www.flickr.com/services/apps/create/apply (FLICKR_API_KEY). Images only (no
video -- Flickr's video files need a separate, unauthenticated-unfriendly download step this doesn't
implement). `license` restricts results server-side to the given Flickr license ids; the default keeps
everything except id 0 ("All Rights Reserved"), so plain public/tagged photos with no explicit license
don't come through -- Flickr requires an explicit license id to search by, there's no "unknown, keep it
anyway" option server-side the way Internet Archive/LOC have.
"""
from __future__ import annotations

import os

from .base import Candidate, CredentialsMissing, HttpSource, SourceContext

SEARCH = "https://www.flickr.com/services/rest/"
DEFAULT_LICENSES = "1,2,3,4,5,6,7,8,9,10"   # every Flickr license except 0 = All Rights Reserved
# https://www.flickr.com/services/api/flickr.photos.licenses.getInfo.html -- stable, documented ids.
LICENSES = {
    "1": ("CC BY-NC-SA 2.0", "https://creativecommons.org/licenses/by-nc-sa/2.0/"),
    "2": ("CC BY-NC 2.0", "https://creativecommons.org/licenses/by-nc/2.0/"),
    "3": ("CC BY-NC-ND 2.0", "https://creativecommons.org/licenses/by-nc-nd/2.0/"),
    "4": ("CC BY 2.0", "https://creativecommons.org/licenses/by/2.0/"),
    "5": ("CC BY-SA 2.0", "https://creativecommons.org/licenses/by-sa/2.0/"),
    "6": ("CC BY-ND 2.0", "https://creativecommons.org/licenses/by-nd/2.0/"),
    "7": ("No known copyright restrictions (Flickr Commons)", "https://www.flickr.com/commons/usage/"),
    "8": ("US Government Work (public domain, 17 U.S.C. § 105)", "https://www.usa.gov/government-works"),
    "9": ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "10": ("Public Domain Mark 1.0", "https://creativecommons.org/publicdomain/mark/1.0/"),
}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class FlickrSource(HttpSource):
    name = "flickr"
    label = "Flickr (incl. Flickr Commons)"

    def __init__(self, per_query: int = 4, api_key: str = "", licenses: str = DEFAULT_LICENSES):
        super().__init__(per_query)
        self._key = api_key
        self.licenses = licenses

    def key(self) -> str:
        return self._key or os.getenv("FLICKR_API_KEY", "")

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        if not self.key():
            raise CredentialsMissing("FLICKR_API_KEY is not set (free key: https://www.flickr.com/services/apps/create/apply)")
        data = await ctx.http.get_json(SEARCH, params={
            "method": "flickr.photos.search", "api_key": self.key(), "text": query, "media": "photos",
            "license": self.licenses, "content_type": 1, "per_page": max(self.per_query * 2, 6),
            "page": ctx.page, "extras": "url_l,url_o,owner_name,license,description,date_taken",
            "format": "json", "nojsoncallback": 1},
            purpose=f"search Flickr for '{query}'")
        photos = ((data.get("photos") or {}).get("photo")) or []
        out: list[Candidate] = []
        for p in photos:
            url = p.get("url_l") or p.get("url_o")
            if not url:
                continue
            lic_name, lic_url = LICENSES.get(str(p.get("license", "")), ("", ""))
            who = p.get("ownername") or "unknown"
            title = p.get("title") or f"flickr {p.get('id')}"
            page = f"https://www.flickr.com/photos/{p.get('owner', '')}/{p.get('id', '')}"
            desc_field = p.get("description")
            desc = (desc_field.get("_content", "") if isinstance(desc_field, dict) else "") or title
            out.append(Candidate(
                url=url, kind="image", mime="image/jpeg", title=title, description=desc[:400],
                page_url=page, author=who, license=lic_name, license_url=lic_url,
                attribution=f'"{title}" by {who}, {lic_name or "license unknown"}, via Flickr: {page}',
                width=_int(p.get("width_l") or p.get("width_o")), height=_int(p.get("height_l") or p.get("height_o")),
                meta={"flickr_id": p.get("id", ""), "license_id": p.get("license", ""), "date_taken": p.get("date_taken", "")}))
        return out
