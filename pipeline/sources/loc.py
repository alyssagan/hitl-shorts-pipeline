"""Library of Congress photos, prints and drawings (no key needed).

Rights vary item by item. An item is labelled public domain only when its own rights text says
so (for example "No known restrictions on publication"); otherwise the license stays empty, which
vetting flags as high risk so a human must read the rights advisory (kept in the description).
"""
from __future__ import annotations

import re

from .base import Candidate, HttpSource, SourceContext, strip_html

SEARCH = "https://www.loc.gov/photos/"
PD_HINT = re.compile(r"no known (?:copyright )?restrictions|public domain|no known copyright|not renewed", re.I)


def _flat(v) -> str:
    if isinstance(v, list):
        return " ".join(_flat(x) for x in v)
    if isinstance(v, dict):
        return " ".join(_flat(x) for x in v.values())
    return str(v or "")


def best_image(urls: list[str]) -> str:
    """Last usable .jpg in the list (LoC lists sizes small to large)."""
    jpgs = [u for u in urls if u.split("#")[0].lower().endswith((".jpg", ".jpeg"))]
    return jpgs[-1].split("#")[0] if jpgs else ""


class LibraryOfCongressSource(HttpSource):
    name = "loc"
    label = "Library of Congress"

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        data = await ctx.http.get_json(SEARCH, params={
            "q": query, "fo": "json", "c": self.per_query * 3, "fa": "online-format:image", "sp": ctx.page},
            purpose=f"search Library of Congress for '{query}'")
        out: list[Candidate] = []
        for r in data.get("results", []):
            url = best_image(r.get("image_url") or [])
            if not url:
                continue
            rights = strip_html(_flat(r.get("rights_advisory")) or _flat(r.get("rights")))
            pd = bool(PD_HINT.search(rights))
            title = strip_html(_flat(r.get("title"))) or "Library of Congress item"
            page = r.get("url") or r.get("id") or ""
            who = strip_html(_flat(r.get("contributor"))) or "Library of Congress"
            out.append(Candidate(
                url=url, kind="image", mime="image/jpeg", title=title,
                description=(f"Rights advisory: {rights}" if rights else "No rights advisory in the record."),
                page_url=page, author=who,
                license="Public domain (per Library of Congress rights advisory)" if pd else "",
                license_url="https://www.loc.gov/legal/",
                attribution=f'"{title}", {who}, Library of Congress: {page}',
                meta={"rights": rights, "date": _flat(r.get("date"))}))
        return out
