"""Internet Archive (archive.org): films and photos pulled from search, license or not.

Most items have no `licenseurl` in their metadata even when they're genuinely public domain --
unlike Library of Congress, which writes a plain-English rights note on almost everything, Archive
only populates `licenseurl` for explicit modern CC declarations. So, like `loc.py`, this source
keeps items either way: one with no `licenseurl` is still fetched and included, just with
`license=""`, and the vetting step's LIC_UNKNOWN rule (high severity) flags it for a human to check
before use -- vetting is the real gate, not this source silently dropping things search-side.
Set `require_license = true` (`archive_require_license` in pipeline.toml) to go back to the old,
stricter behavior of dropping anything without a `licenseurl` before it's even fetched. Files over
`max_mb` are skipped to keep downloads reasonable.
"""
from __future__ import annotations

import re

from .base import Candidate, HttpSource, SourceContext, cc_name, strip_html

SEARCH = "https://archive.org/advancedsearch.php"
META = "https://archive.org/metadata/{id}"
DOWNLOAD = "https://archive.org/download/{id}/{name}"
EXT_MIME = {"mp4": "video/mp4", "webm": "video/webm", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


def _first(v) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def pick_file(files: list[dict], mediatype: str, max_bytes: int) -> dict | None:
    """Largest usable file under the size limit (a good derivative, not a huge original)."""
    allowed = ("mp4", "webm") if mediatype == "movies" else ("jpg", "jpeg", "png")
    ok = []
    for f in files:
        name = f.get("name", "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        try:
            size = int(f.get("size") or 0)
        except ValueError:
            size = 0
        if ext in allowed and 0 < size <= max_bytes and f.get("source") != "metadata":
            ok.append((size, f))
    return max(ok, key=lambda x: x[0])[1] if ok else None


class InternetArchiveSource(HttpSource):
    name = "archive"
    label = "Internet Archive"

    def __init__(self, per_query: int = 3, videos_per_query: int | None = None, require_license: bool = False, max_mb: int = 60):
        super().__init__(per_query, videos_per_query)
        self.require_license = require_license
        self.max_bytes = max_mb * 1024 * 1024

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        clean = re.sub(r'[^\w\s-]', " ", query)
        data = await ctx.http.get_json(SEARCH, params={
            "q": f"({clean}) AND mediatype:(movies OR image)",
            "fl[]": ["identifier", "title", "creator", "licenseurl", "mediatype"],
            "rows": self.per_query * 6, "page": ctx.page, "output": "json"},
            purpose=f"search Internet Archive for '{query}'")
        out: list[Candidate] = []
        for doc in data.get("response", {}).get("docs", []):
            lic_url = _first(doc.get("licenseurl"))
            if self.require_license and not lic_url:
                continue
            ident, mt = doc.get("identifier"), _first(doc.get("mediatype"))
            if not ident or mt not in ("movies", "image"):
                continue
            try:
                meta = await ctx.http.get_json(META.format(id=ident), purpose=f"list files for archive.org item {ident}")
            except Exception:
                continue
            f = pick_file(meta.get("files", []), mt, self.max_bytes)
            if not f:
                continue
            m = meta.get("metadata", {})
            title = strip_html(_first(m.get("title")) or _first(doc.get("title")) or ident)
            who = strip_html(_first(m.get("creator")) or _first(doc.get("creator"))) or "unknown"
            lic_url = _first(m.get("licenseurl")) or lic_url
            lic = cc_name(lic_url) or ("" if not lic_url else lic_url)
            page = f"https://archive.org/details/{ident}"
            ext = f["name"].rsplit(".", 1)[-1].lower()
            desc = strip_html(_first(m.get("description")))[:400]
            if not lic_url:
                desc = (desc + " " if desc else "") + "No license info in the Archive.org record -- check the item page before use."
            out.append(Candidate(
                url=DOWNLOAD.format(id=ident, name=f["name"].replace(" ", "%20")),
                kind="video" if mt == "movies" else "image", mime=EXT_MIME.get(ext, ""), title=title,
                description=desc, page_url=page, author=who,
                license=lic, license_url=lic_url,
                attribution=f'"{title}" by {who}, {lic or "license unknown"} ({lic_url}), via Internet Archive: {page}'.replace("()", "").replace("  ", " "),
                meta={"archive_id": ident, "mediatype": mt, "file": f["name"]}))
        return out
