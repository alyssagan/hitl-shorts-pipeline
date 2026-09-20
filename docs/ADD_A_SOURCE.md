# Adding another scraping source

Every source gets its own folder in each project (`sources/<name>/`) with a request log, a manifest and the files.
You only write the search; downloading, hashing, dedupe, logging, vetting, the review gate and the decision log are shared.

## 1. If your scraper already produces files

Drop them in `library/scraped/` and add `folder` to the job's sources. Optionally add a sidecar `<file>.json`
next to each file:

```json
{"title": "...", "source_url": "https://...", "license": "CC BY 4.0", "license_url": "...", "author": "...", "description": "..."}
```

No sidecar means no license, which vetting flags as **high risk**, so a human must decide.

## 2. A new web API

```python
# pipeline/sources/mysite.py
from .base import Candidate, HttpSource, SourceContext

class MySiteSource(HttpSource):
    name = "mysite"
    label = "My Site"

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        data = await ctx.http.get_json("https://api.example.com/search", params={"q": query},
                                       purpose=f"search My Site for '{query}'")   # logged for you
        return [Candidate(url=i["file"], mime="image/jpeg", title=i["title"], page_url=i["page"],
                          author=i["by"], license=i["license"], width=i["w"], height=i["h"]) for i in data["items"]]
```

Register it in `pipeline/stages/registry.py` (`reg.register_source("mysite", lambda: MySiteSource())`), then request it
with `"providers": {"sources": ["mysite"]}` or `--sources mysite`.

Fill in `license`, `author`, `page_url` as accurately as the site allows. The vetting rules read them.
Raise `SourceUnavailable("MYSITE_KEY is not set")` when a key is missing; it is logged and the other sources still run.
Respect each site's terms and rate limits.

## Sources that ship with the pipeline

| Name for `--sources` | What | Key in `.env` | Notes |
|---|---|---|---|
| `wikipedia` | Article text for the script | none | Text only |
| `commons` | Wikimedia Commons photos | none | Many CC BY-SA (medium flag) |
| `pexels` | Photos and video | `PEXELS_API_KEY` | Free key |
| `pixabay` | Photos and video | `PIXABAY_API_KEY` | Free key; key is hidden in request logs |
| `unsplash` | Photos | `UNSPLASH_ACCESS_KEY` | Demo key: 50 requests an hour. API terms caveat in KNOWN_LIMITATIONS |
| `nasa` | Images and video | none | Generally public domain, with exceptions (NASA_NOTE flag) |
| `archive` | Internet Archive films and photos | none | Keeps only items with a license or public-domain mark |
| `loc` | Library of Congress photos | none | Public domain only when the record's rights text says so |
| `smithsonian` | Smithsonian Open Access images | `SMITHSONIAN_API_KEY` | CC0 items only get a license |
| `urls` | Your own list of links (yt-dlp) | none | See `docs/URL_LIST.md` |
| `folder` | Files in `library/scraped/` | none | Optional sidecar `.json` |
