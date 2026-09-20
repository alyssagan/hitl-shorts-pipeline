"""Smithsonian Open Access (free key in SMITHSONIAN_API_KEY from api.data.gov).

Only media the record marks as CC0 gets a license; anything else is left without one, which
vetting flags as high risk. NOTE: the response layout used here follows Smithsonian's public
docs from memory and has not been checked against a live response yet
(see docs/KNOWN_LIMITATIONS.md).
"""
from __future__ import annotations

import os

from .base import Candidate, HttpSource, SourceContext, SourceUnavailable, strip_html

SEARCH = "https://api.si.edu/openaccess/api/v1.0/search"


class SmithsonianSource(HttpSource):
    name = "smithsonian"
    label = "Smithsonian Open Access"

    def __init__(self, per_query: int = 3, api_key: str = ""):
        super().__init__(per_query)
        self._key = api_key

    def key(self) -> str:
        return self._key or os.getenv("SMITHSONIAN_API_KEY", "")

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        if not self.key():
            raise SourceUnavailable("SMITHSONIAN_API_KEY is not set")
        data = await ctx.http.get_json(SEARCH, params={
            "api_key": self.key(), "q": f'{query} AND online_media_type:"Images"', "rows": self.per_query * 3},
            purpose=f"search Smithsonian Open Access for '{query}'")
        out: list[Candidate] = []
        for row in data.get("response", {}).get("rows", []):
            c = row.get("content", {})
            dnr = c.get("descriptiveNonRepeating", {})
            title = (dnr.get("title") or {}).get("content") or row.get("title") or "Smithsonian item"
            unit = dnr.get("data_source", "Smithsonian Institution")
            page = dnr.get("record_link", "")
            for m in (dnr.get("online_media") or {}).get("media", []):
                if m.get("type") != "Images" or not m.get("content"):
                    continue
                cc0 = (m.get("usage") or {}).get("access") == "CC0"
                out.append(Candidate(
                    url=m["content"], kind="image", mime="image/jpeg", title=strip_html(title),
                    page_url=page, author=unit, license="CC0 1.0" if cc0 else "",
                    license_url="https://creativecommons.org/publicdomain/zero/1.0/" if cc0 else "",
                    attribution=f'"{strip_html(title)}", {unit}, Smithsonian Open Access: {page}',
                    meta={"smithsonian_id": row.get("id", ""), "access": (m.get("usage") or {}).get("access", "")}))
                break
        return out
