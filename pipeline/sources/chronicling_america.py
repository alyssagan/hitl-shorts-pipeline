"""Library of Congress Chronicling America: digitized historic U.S. newspaper pages, part of the National
Digital Newspaper Program. No key needed. This is often the single best source for period-accurate material
tied to an actual event -- real front pages, articles and photo engravings from the era a case happened,
not a generic reenactment or stock photo.

Uses the same www.loc.gov JSON search API as loc.py, scoped to this collection, so the request/response
shape is modeled on that already-proven pattern -- but it hasn't been separately confirmed against a live
response for the chronicling-america collection specifically (see docs/KNOWN_LIMITATIONS.md). If this comes
back with zero results across several different queries, that mismatch -- not the query wording -- is the
first thing to check.

Nearly every page in this collection is either pre-1929 (automatically public domain) or was specifically
rights-cleared as a condition of the newspaper's inclusion in the program, but that isn't asserted here
unless a record's own rights_advisory field actually says so (same caution as loc.py) -- when it's silent,
the description says so explicitly instead of guessing, and vetting still flags the license as unknown.
"""
from __future__ import annotations

import re

from .base import Candidate, HttpSource, SourceContext, strip_html

SEARCH = "https://www.loc.gov/collections/chronicling-america/"
PD_HINT = re.compile(r"no known (?:copyright )?restrictions|public domain|no known copyright|not renewed", re.I)


def _flat(v) -> str:
    if isinstance(v, list):
        return " ".join(_flat(x) for x in v)
    if isinstance(v, dict):
        return " ".join(_flat(x) for x in v.values())
    return str(v or "")


def best_image(urls: list[str]) -> str:
    """Last usable .jpg in the list (loc.gov lists derivatives small to large, same as loc.py)."""
    jpgs = [u for u in urls if u.split("#")[0].lower().endswith((".jpg", ".jpeg"))]
    return jpgs[-1].split("#")[0] if jpgs else ""


class ChroniclingAmericaSource(HttpSource):
    name = "chronicling_america"
    label = "Chronicling America (historic U.S. newspapers)"

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        data = await ctx.http.get_json(SEARCH, params={
            "q": query, "fo": "json", "c": self.per_query * 3, "sp": ctx.page},
            purpose=f"search Chronicling America newspapers for '{query}'")
        out: list[Candidate] = []
        for r in data.get("results", []):
            url = best_image(r.get("image_url") or [])
            if not url:
                continue
            rights = strip_html(_flat(r.get("rights_advisory")) or _flat(r.get("rights")))
            pd = bool(PD_HINT.search(rights))
            title = strip_html(_flat(r.get("title"))) or "Chronicling America newspaper page"
            page = r.get("url") or r.get("id") or ""
            date = _flat(r.get("date"))
            desc = title + (f", {date}" if date else "")
            if rights:
                desc += f". Rights advisory: {rights}"
            elif not pd:
                desc += (" Chronicling America pages are drawn from out-of-copyright or rights-cleared "
                         "newspapers as a condition of the National Digital Newspaper Program, but this "
                         "record has no rights_advisory field to confirm that -- check the item page.")
            out.append(Candidate(
                url=url, kind="image", mime="image/jpeg", title=title, description=desc,
                page_url=page, author="Library of Congress, Chronicling America",
                license="Public domain (per Chronicling America rights advisory)" if pd else "",
                license_url="https://www.loc.gov/collections/chronicling-america/about-this-collection/rights-and-access/",
                attribution=f'"{title}"{f", {date}" if date else ""}, Chronicling America, Library of Congress: {page}',
                meta={"rights": rights, "date": date}))
        return out
