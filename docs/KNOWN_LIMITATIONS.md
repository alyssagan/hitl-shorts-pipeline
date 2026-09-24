# Known limitations

Things that work today but with a trade-off we chose on purpose. Each has a way to
improve it later. Add new ones at the bottom; move an entry to "Resolved" when fixed.

## Open

### 1. Script writing depends on a key and a free-tier model
- **What:** the script is now written by our own writer (`pipeline/stages/scenes/writer.py`,
  settings in `[script]` of `config/pipeline.toml`): about 260 words (~100 seconds) by default,
  reading up to 12000 characters of source text split across the articles. Reviewer notes
  from a rejected draft are added to the prompt.
- **If there is no `GEMINI_API_KEY`,** or `provider = "mpt"`, the scene stage falls back to
  MoneyPrinterTurbo's script step. That one is capped by MPT at 2000 characters of
  instructions (about 1000 of source text) and has no length control, so scripts are short.
- **Effect:** the model can still get a fact wrong or write below the target. The review gate
  shows the word count and estimated seconds, and a `warning` is saved in the decision log when
  the script is under 60% of the target.
- **Also:** Wikipedia's search can return a loosely related article first
  ("3 surprising facts about octopuses" returned "Kraken" before "Octopus"). The writer is told
  to use only the sources, so a wrong source can pull the script off topic.
- **Longer than ~100 seconds:** raise `target_words` and `grounding_chars`. Very long scripts
  need more source text than two Wikipedia articles provide (true-crime mode will).
- **Options to improve:** better article selection; a fact-check pass that compares each claim
  with the sources; summarizing long sources first.

### 2. Vetting reads metadata, not pictures
- **What:** the risk rules (`pipeline/vetting/rules.py`) look at license text, titles,
  descriptions and image size. They never look at the image itself, so they can't spot
  a recognizable face, a child, a logo or a watermark unless the text says so.
- **Effect:** a clean-looking image can still be risky. Your review at the asset gate is the
  real check; the flags are a head start.
- **Also:** the license on Commons is whatever the uploader entered. Mislabeled or wrongly
  claimed licenses exist, and the pipeline can't tell.
- **Options:** add an image-content check (an AI vision model, logged in the decision log
  with its reasons) as an extra flag, never as a filter.

### 3. Share-alike and other license questions are flagged, not answered
- **What:** `LIC_SA`, `LIC_ND`, `LIC_GFDL` etc. tell you a question exists. Whether a video
  counts as an adaptation is a legal question the pipeline can't answer, and this is not legal advice.
- **Effect:** for anything monetized, client work or company use, take real advice or use
  public-domain / CC0 / plain CC BY assets.

### 4. Credits: description text only, and license matching is simple
- **What:** `DESCRIPTION_CREDITS.txt` is written for you to paste. Nothing is posted for you,
  and nothing is burned into the video (MPT can't overlay text per image).
- **Also:** deciding whether an asset needs a credit line is a substring match on the license
  text (`pipeline/stages/sourcing.py`, `description_block`). An unusual license string could
  be missed or included wrongly. Check the block before publishing.
- **Options:** a post-render ffmpeg step for on-screen credits; a proper license table.

### 5. Mostly still images from the free sources
- **What:** Wikipedia and Commons return mostly photos and diagrams, a few videos.
- **Effect:** the video will be photos with motion added, not stock footage. Pexels (free key)
  is already supported as an optional source and would add video clips.
- **Unverified:** how good MPT's image-to-clip motion looks. We'll know after the first render.

### 6. Free tiers and unofficial services can change or throttle
- **Gemini free tier** has rate and daily limits, and model names get retired. The model in
  `config/mpt-config.toml` (`gemini-2.0-flash`) may need updating.
- **Edge TTS** (the free voice) is an unofficial service that needs internet and can change
  without notice.
- **Wikipedia / Commons** expect polite use. Heavy repeated searches may need throttling.

### 7. Everything is single-user and local
- **What:** no login on the pipeline or MPT API. Both bind to `127.0.0.1` only. The reviewer
  name is just text you type, so the decision log records who *said* they decided.
- **Also:** the decision log's hash chain shows edits to a line, but someone with file access
  could rebuild the whole chain. It is a record for you, not a legal audit trail.
- **Options:** if anyone else uses it, add real user accounts and sign log entries.

### 8. MoneyPrinterTurbo is built from our own Docker recipe
- **What:** upstream's Dockerfile pins Debian "bullseye", which is end-of-life and no longer
  downloads. `docker-compose.yml` builds MPT from `python:3.11-slim` with an inline recipe.
- **Effect:** if a newer MPT version adds system packages (for example ImageMagick for
  subtitles), our recipe won't install them until we add them. The submodule is pinned, so
  this only matters when upgrading MPT.
- **Options:** move the recipe into a real `docker/mpt.Dockerfile`, and re-check it whenever
  the MPT submodule is updated.

### 9. First real render worked; quality is still unjudged
The first full run (octopus video, about 4 minutes to render) completed end to end. Confirmed:
gates -> Gemini script -> Edge voice -> MPT render with approved images -> credits file.
Getting there needed these fixes (all in git history): script prompt over 2000 characters,
retired Gemini model, MPT error text shown as a script, empty voice name, and a read-only
project mount.
Still to judge by watching and comparing runs (record in OPTIONS_TO_TRY.md):
- do the images match what the voice is saying (clip choice is by shared words only);
- subtitle look, voice quality, pacing and total length;
- scene reordering in the review gate (not exercised yet);
- per-scene audio previews (`generate_scene_audio`, off by default);
- Windows and the Docker paths there (#10).

### 10. Windows is untested
- **What:** designed to run on Windows through Docker, but only developed against a Mac.
- **Watch for:** path separators, line endings, and Docker bind-mount paths.

### 11. True-crime mode is not built yet
- **What:** agreed but deliberately postponed until the POC works. Needs its own review
  states (eligibility, images, final), its guardrails carried over, one shared MPT copy, and
  much longer source material (which will hit limitation #1 hard).

### 12. MoneyPrinterTurbo writes into the project folders
- **What:** to use a still image, MPT converts it to a short video and saves it next to the
  original (for example `001-Octopus.jpg.mp4` in `sources/commons/files/`). So the project
  folder is mounted into MPT as writable, not read-only.
- **Effect:** MPT could in principle change files there, and those extra `.mp4` files sit
  beside the originals. The recorded SHA-256 in each manifest is of the original download, so
  you can still check the originals. The extras are safe to delete.
- **Options:** point MPT at a scratch copy of approved assets instead, so the project folder
  can stay read-only.

### 13. New sources were built from documentation, not live responses
- **What:** Pixabay, NASA, Internet Archive, Library of Congress, Unsplash and the URL list follow the
  services' public API docs and are tested with hand-written mock replies. Smithsonian's layout
  (`response.rows[].content.descriptiveNonRepeating`) is from memory, because its docs page could not be read.
- **As of 2026-09-23**, five more sources were added the same way: Openverse's and DPLA's response fields
  were confirmed against their real published docs before writing the code, so those two are lower-risk.
  Chronicling America's and Europeana's field names (`image_url`, `edmPreview`/`edmIsShownAt`/`rights`) could
  not be checked against a live response (both APIs 403'd every fetch attempt from here) -- they're modeled
  on the closest proven pattern in this codebase (loc.py for Chronicling America, since both hit the same
  www.loc.gov backend) or on public docs (Europeana), but if either comes back empty across several
  different searches, that's the first thing worth checking, not the keywords. Flickr's API is old,
  extremely well-documented and stable, so it's treated as low-risk despite the same lack of a live test.
- **Effect:** the first live search on each may reveal a difference. `sources/<name>/requests.jsonl` shows
  exactly what was asked and what status came back; a source that fails is skipped without stopping the others.
- **As of the "Find more" panel's on-demand search** for Internet Archive, Chronicling America and Wikimedia
  Commons (Gate 2, `POST /jobs/{id}/source-search`), this same risk applies there too, not just to the normal
  per-round search -- it's the identical `search()` method either way (`pipeline/sources/base.py`'s
  `HttpSource.search`), just called directly instead of from `pipeline/stages/sourcing.py`'s batch loop. A
  parse failure surfaces as a 422 in the review page rather than a silently-empty result.
- **Also:** Library of Congress and, as of 2026-09-23, Internet Archive both leave items without a clear rights
  statement unlicensed rather than dropping them, so vetting can flag them high risk instead of you never seeing
  them (see ROADMAP.md); set `archive_require_license = true` in `config/pipeline.toml` to go back to Archive
  only surfacing explicitly-licensed items.
- **Not built:** AI image generation (paid or heavy), by choice for now.

### 14. Terms of use for some sources are only partly met
- **Unsplash** asks apps to hot-link photos; we download them (needed for rendering) and send the download
  ping. Whether that meets their API terms for a tool like this is unchecked.
- **Pixabay** asks that results are cached for 24 hours and images are downloaded, not hot-linked. Both hold here.
- **Platform videos** (URL list): downloading may break the platform's terms even where the uploader allows reuse.
  The pipeline flags it and records your decision; it cannot make it safe.
- **NASA:** media is generally not copyrighted, but third-party material, identifiable people and logos are
  excepted. The `NASA_NOTE` flag reminds you; it can't see the image.

### 15. URL list depends on yt-dlp working from Docker
- **What:** sites change and sometimes block downloads (login walls, bot checks, region locks). A failed link is
  reported in the source's trace and the others continue.
- **Also:** the pipeline image now includes ffmpeg and yt-dlp, so it is larger and takes longer to build.
  Rebuild (`docker compose up --build`) to get a newer yt-dlp when links start failing.
- **As of the "Find more" panel's YouTube search** (Gate 2, `pipeline/sources/youtube_search.py`), this
  dependency now also covers search, not just download: it uses yt-dlp's own `ytsearchN:` syntax rather than
  YouTube's official Data API, so there's no key/quota to manage, but also no guarantee it keeps working --
  it's reading the same search results page a browser would, and if YouTube changes that page or starts
  rate-limiting/blocking the requests, search fails the same way a broken download link does (a clear error
  in the review page, logged to `sources/youtube_search/requests.jsonl`, nothing silently wrong). It's also
  metadata-only by design -- nothing downloads until a specific result is picked -- specifically so a batch
  search doesn't multiply the platform-download risk of a single pasted link into ten at once.

## Resolved

_(nothing yet)_

## 16. Relevance scoring reads metadata, not the picture (updated: hybrid TF-IDF + LLM scoring)
As of the hybrid relevance scorer (`docs/SCORING.md`), most assets are scored by a deterministic local TF-IDF match, with an
LLM's meaning-aware judgement reserved for the borderline ones -- which fixes the worst cases of the original word-matching
limitation (a generic caption with the right words scoring high; a correct photo with a different wording scoring low) for
those borderline cases specifically. But every scorer here -- TF-IDF, the LLM, and the plain keyword-match last resort -- only
ever sees the asset's title/description/tags/page URL, never the image or video itself. An untitled photo (common on stock and
Flickr-style sources) can't be confirmed relevant by any of them, and a wrongly-captioned photo of the wrong place could still
score well. That is why off-topic items stay numbered and approvable rather than being removed. A later option is a real
vision check of the thumbnail (see OPTIONS_TO_TRY). Also: stock sites (Pexels, Pixabay, Unsplash) suit generic b-roll, not
named historical events, whatever they score.

## 17. LLM relevance scoring, where it's still used, adds a dependency and (small) cost
The TF-IDF baseline (`docs/SCORING.md`) needs nothing -- no key, no network, no rate limit -- and scores every pending asset
every round. The LLM is now only consulted for the (usually much smaller) set of borderline assets near the threshold. With
`[relevance] enabled = true` (the default) and a borderline asset to score, that call needs a working Gemini free-tier key and
network access; without a key it silently falls back to the TF-IDF score (`scoring_method`/`method_version` on each asset say
which scorer actually ran), so a run never fails for this reason, but the borderline calls it would have refined stay
unrefined. The LLM
calls that do happen are exposed to the same free-tier rate limits and occasional "busy" 429/503s as keyword and script
generation (auto-retried; see docs/LOGGING.md), bounded by `max_llm_per_round` even in a worst case.

## 18. Manual crop needs known dimensions, and costs render time
Gate 3's crop tool (docs/REVIEW_UI.md) needs an asset's width/height to compute the crop window; almost every sourced/
uploaded asset has this, but if one somehow doesn't, the crop panel says so and MoneyPrinterTurbo's own automatic
center-crop is used instead -- never a guess. A crop is also only ever applied at render time (`pipeline/stages/render/
crop.py`, one `ffmpeg` process per cropped scene, still images and video clips alike), not live in the browser, so it adds
a little to render time and disk use (one cropped copy per cropped scene, written into the project's `cropped/` folder) --
proportional to how many scenes actually have a crop set, not the whole job. If that one ffmpeg call fails for any reason,
the render falls back to the uncropped clip and logs a warning rather than failing the whole render over one scene's
framing. There's also no per-clip preview of the *rendered* result (MoneyPrinterTurbo's own zoom/pan effect on top of the
crop) before you commit to "Approve and render" -- the crop preview shows framing, not motion.
