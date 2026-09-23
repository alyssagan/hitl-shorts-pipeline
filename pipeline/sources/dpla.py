"""Digital Public Library of America (DPLA): aggregates digitized photos, documents, newspapers and more
from thousands of US libraries, archives and museums -- one search across collections that would otherwise
each need their own scraper. Good for period photographs and documents tied to a specific place or era.
Needs a free API key: https://pro.dp.la/developers (DPLA_API_KEY).

Rights vary by whichever institution contributed the item. Recorded as given: a recognizable Creative
Commons/public-domain rights URL becomes the license; anything else is left blank (so vetting flags it, per
the rest of this codebase's "keep and record what's given, never assume" approach) with the raw rights text
kept in the description so a human can judge it fast.
"""
from __future__ import annotations

import os

from .base import Candidate, CredentialsMissing, HttpSource, SourceContext, cc_name, strip_html

SEARCH = "https://api.dp.la/v2/items"


def _first(v) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


class DplaSource(HttpSource):
    name = "dpla"
    label = "Digital Public Library of America"

    def __init__(self, per_query: int = 4, api_key: str = ""):
        super().__init__(per_query)
        self._key = api_key

    def key(self) -> str:
        return self._key or os.getenv("DPLA_API_KEY", "")

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        if not self.key():
            raise CredentialsMissing("DPLA_API_KEY is not set (free key: https://pro.dp.la/developers)")
        rows = max(self.per_query * 2, 6)
        data = await ctx.http.get_json(SEARCH, params={
            "q": query, "api_key": self.key(), "page_size": rows, "page": ctx.page},
            purpose=f"search DPLA for '{query}'")
        out: list[Candidate] = []
        for doc in data.get("docs", []):
            url = doc.get("object")
            if not url:
                continue
            sr = doc.get("sourceResource") or {}
            title = strip_html(_first(sr.get("title"))) or "DPLA item"
            who = strip_html(_first(sr.get("creator"))) or ""
            page = doc.get("isShownAt", "")
            date = sr.get("date")
            display_date = (date.get("displayDate") if isinstance(date, dict) else _first(date)) or ""
            rights = _first(sr.get("rights")) or _first(doc.get("rights"))
            lic = cc_name(rights)
            lic_url = rights if lic else ""
            desc = strip_html(_first(sr.get("description")))[:400] or title
            if not lic and rights:
                desc += f" Rights (per DPLA record): {rights}"
            provider = ((doc.get("provider") or {}).get("name")) or doc.get("dataProvider") or "DPLA"
            out.append(Candidate(
                url=url, kind="image", mime="image/jpeg", title=title, description=desc,
                page_url=page, author=who or provider, license=lic, license_url=lic_url,
                attribution=f'"{title}", {who or provider}, via DPLA ({provider}): {page}',
                meta={"dpla_id": doc.get("id", ""), "provider": provider, "rights_text": rights, "date": display_date}))
        return out
