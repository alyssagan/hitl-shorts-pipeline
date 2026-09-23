"""Wikipedia article text, used to ground the script in real facts.

Not an image source: it produces `TextRef`s (saved under sources/wikipedia/files/).
Text is CC BY-SA 4.0; the license and URL are recorded on each reference.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..core.models import TextRef
from .base import SourceContext, SourceResult, _now, safe_name

API = "https://en.wikipedia.org/w/api.php"
DEFAULT_MAX_CHARS = 30000   # overridable per job via config/pipeline.toml [sources] wikipedia_max_chars


class WikipediaSource:
    name = "wikipedia"
    label = "Wikipedia (article text)"

    def __init__(self, max_articles: int = 2, max_chars: int = DEFAULT_MAX_CHARS):
        self.max_articles = max_articles
        self.max_chars = max_chars

    async def fetch(self, queries: list[str], ctx: SourceContext) -> SourceResult:
        result = SourceResult()
        # Every article URL already kept for this project, this round or an earlier one (ctx.known_urls
        # is rebuilt each round from job.references -- see sourcing.py) -- never re-fetch/re-save one.
        seen_urls = set(ctx.known_urls)
        for q in queries:
            if len(result.references) >= self.max_articles:
                break
            # "kind": "text" disambiguates this source's found/kept counts (articles) from every other
            # source's (photos/video) in shared reporting -- see base.py's HttpSource.fetch() for the
            # "media" counterpart, and docs/SOURCE_ERRORS.md / SOURCES.md's "What was searched" table.
            note = {"source": self.name, "query": q, "found": 0, "kept": 0, "skipped": [], "kind": "text"}
            try:
                found = await ctx.http.get_json(API, params={
                    "action": "query", "list": "search", "srsearch": q, "srlimit": 1, "format": "json"},
                    purpose=f"find the best-matching Wikipedia article for '{q}'")
                hits = found.get("query", {}).get("search", [])
                note["found"] = len(hits)
                if not hits:
                    result.trace.append(note)
                    continue
                title = hits[0]["title"]
                page = await ctx.http.get_json(API, params={
                    "action": "query", "prop": "extracts|info", "explaintext": 1, "exsectionformat": "plain",
                    "titles": title, "redirects": 1, "inprop": "url", "format": "json"},
                    purpose=f"read the text of '{title}' (used to ground the script)")
                pages = page.get("query", {}).get("pages", {})
                p = next(iter(pages.values()), {})
                text = (p.get("extract") or "").strip()
                url = p.get("fullurl") or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
                if not text:
                    note["skipped"].append({"url": url, "reason": "article has no text"})
                elif url in seen_urls:
                    note["skipped"].append({"url": url, "reason": "already in this project"})
                else:
                    seen_urls.add(url)
                    text = text[:self.max_chars]
                    # Numbered like every other source's saved files (base.py, folder.py) so two article
                    # titles that sanitize to the same string (truncated at safe_name's 60-char cap, or
                    # differing only in stripped punctuation) can't silently overwrite each other on disk.
                    n = len(ctx.known_urls) + len(result.references) + 1
                    dest = Path(ctx.dir) / "files" / f"{n:03d}-{safe_name(title)}.txt"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(
                        f"{title}\nSource: {url}\nLicense: CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)\n"
                        f"Retrieved: {_now()}\n\n{text}\n", encoding="utf-8")
                    result.references.append(TextRef(
                        source=self.name, title=title, url=url, path=str(dest),
                        rel_path=os.path.relpath(dest, ctx.project_dir),
                        license="CC BY-SA 4.0", license_url="https://creativecommons.org/licenses/by-sa/4.0/",
                        query=q, chars=len(text)))
                    note["kept"] = 1
            except Exception as exc:
                note["error"] = f"{type(exc).__name__}: {exc}"
            result.trace.append(note)
        return result
