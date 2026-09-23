# Adding another scraping source

Every source gets its own folder in each project (`sources/<name>/`) with a request log, a manifest and the files.
You only write the search; downloading, hashing, dedupe, logging, vetting, the review gate and the decision log are shared.

## 1. If your scraper already produces files

Drop them in `library/scraped/`. Adding `folder` to a job's sources is no longer enough by itself (#10): the
whole folder used to get imported into ANY job that listed `folder`, which meant an unrelated job could
silently inherit another case's material. Now you also pick which specific files this job uses -- browse
what's there with `GET /jobs/{id}/folder-files` (or the review page's "Add your own footage" panel at Gate 2)
and queue your picks with `POST /jobs/{id}/assets/reject {"folder_files": ["sub/clip.mp4", ...]}` (each entry
can also be `{"path": ..., "note": ...}`); `folder` is added to the job's sources automatically. See
`pipeline/sources/folder.py` and docs/REVIEW_UI.md. Every file picked this way is stamped `owner_submitted`
and `import_method="local_folder"` -- picking it for a job is the explicit "this is my own material" action.

Optionally add a sidecar `<file>.json` next to each file:

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
Raise `CredentialsMissing("MYSITE_KEY is not set")` when a key is missing (not the bare `SourceUnavailable`) so
it's reported to the person as a fixable "add your key" problem rather than lumped in with rate limits or
outages. `ctx.http.get_json()`/`ctx.http.download()` already raise the right specific error for you for HTTP-level
failures (a bad status, a network error, a response that isn't valid JSON) -- you only need to raise one yourself
for something your own `search()` detects, like a missing key before the request is even made. See
`docs/SOURCE_ERRORS.md` for the full list and what each one means. Respect each site's terms and rate limits.

## Sources that ship with the pipeline

| Name for `--sources` | What | Key in `.env` | Notes |
|---|---|---|---|
| `wikipedia` | Article text for the script | none | Text only |
| `commons` | Wikimedia Commons photos | none | Many CC BY-SA (medium flag) |
| `pexels` | Photos and video | `PEXELS_API_KEY` | Free key |
| `pixabay` | Photos and video | `PIXABAY_API_KEY` | Free key; key is hidden in request logs |
| `unsplash` | Photos | `UNSPLASH_ACCESS_KEY` | Demo key: 50 requests an hour. API terms caveat in KNOWN_LIMITATIONS |
| `nasa` | Images and video | none | Generally public domain, with exceptions (NASA_NOTE flag) |
| `archive` | Internet Archive films and photos | none | Keeps items either way now; unlicensed ones come through with license unknown and get flagged for review (set `archive_require_license = true` to only keep explicitly-licensed items) |
| `loc` | Library of Congress photos | none | Public domain only when the record's rights text says so |
| `smithsonian` | Smithsonian Open Access images | `SMITHSONIAN_API_KEY` | CC0 items only get a license |
| `chronicling_america` | Library of Congress historic U.S. newspaper pages | none | Real period newspaper pages for an actual event; public domain only when the record's rights text says so |
| `openverse` | CC-licensed image search aggregating Flickr, Commons, museums and more | none | Everything indexed is CC/PD already; no license filtering needed |
| `dpla` | Digital Public Library of America (US museums/archives/libraries) | `DPLA_API_KEY` | Rights vary by contributing institution |
| `flickr` | Flickr photos, incl. Flickr Commons | `FLICKR_API_KEY` | Images only; `flickr_licenses` in `config/pipeline.toml` controls which licenses are searched |
| `europeana` | European museums/archives/libraries aggregator | `EUROPEANA_API_KEY` | Best for cases with a European angle |
| `urls` | Your own list of links (yt-dlp) | none | See `docs/URL_LIST.md` |
| `folder` | Files in `library/scraped/`, explicitly picked per job (#10) | none | Optional sidecar `.json` |
