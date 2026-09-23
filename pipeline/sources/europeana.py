"""Europeana: aggregates digitized photos, documents and more from thousands of European museums, libraries
and archives -- the European counterpart to DPLA. Mostly useful when a case has a European angle; for a
US-only case it's unlikely to add much over DPLA/Chronicling America/LOC.

Needs a free API key: https://pro.europeana.eu/get-api (EUROPEANA_API_KEY). Response field names are
modeled on Europeana's documented Search API (edmPreview/edmIsShownAt/dataProvider/rights) and haven't been
separately checked against a live response (see docs/KNOWN_LIMITATIONS.md) -- same caveat as
chronicling_america.py, for the same reason (no network access to verify from where this was written).

Rights vary by contributing institution: a recognizable Creative Commons/public-domain rights URL becomes
the license; anything else is left blank (vetting flags it) with the raw rights URI kept in the description.
"""
from __future__ import annotations

import os

from .base import Candidate, CredentialsMissing, HttpSource, SourceContext, cc_name, strip_html

SEARCH = "https://api.europeana.eu/record/v2/search.json"


def _first(v) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


class EuropeanaSource(HttpSource):
    name = "europeana"
    label = "Europeana"

    def __init__(self, per_query: int = 4, api_key: str = ""):
        super().__init__(per_query)
        self._key = api_key

    def key(self) -> str:
        return self._key or os.getenv("EUROPEANA_API_KEY", "")

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        if not self.key():
            raise CredentialsMissing("EUROPEANA_API_KEY is not set (free key: https://pro.europeana.eu/get-api)")
        rows = max(self.per_query * 2, 6)
        data = await ctx.http.get_json(SEARCH, params={
            "wskey": self.key(), "query": query, "qf": "TYPE:IMAGE", "media": "true",
            "rows": rows, "start": (ctx.page - 1) * rows + 1},
            purpose=f"search Europeana for '{query}'")
        out: list[Candidate] = []
        for item in data.get("items", []):
            url = _first(item.get("edmPreview")) or _first(item.get("edmIsShownBy"))
            if not url:
                continue
            title = strip_html(_first(item.get("title"))) or "Europeana item"
            page = _first(item.get("edmIsShownAt")) or _first(item.get("guid"))
            provider = _first(item.get("dataProvider")) or _first(item.get("provider")) or "Europeana"
            rights = _first(item.get("rights"))
            lic = cc_name(rights)
            lic_url = rights if lic else ""
            desc = title
            if not lic and rights:
                desc += f". Rights (per Europeana record): {rights}"
            out.append(Candidate(
                url=url, kind="image", mime="image/jpeg", title=title, description=desc,
                page_url=page, author=provider, license=lic, license_url=lic_url,
                attribution=f'"{title}", {provider}, via Europeana: {page}',
                meta={"europeana_id": item.get("id", ""), "provider": provider, "rights_text": rights,
                      "year": _first(item.get("year"))}))
        return out
