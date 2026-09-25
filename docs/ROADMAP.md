# Roadmap

What we agreed to build next, in order, with the choices made. Limits that shape each item
are in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md); things to experiment with are in
[OPTIONS_TO_TRY.md](OPTIONS_TO_TRY.md).

## Decisions

| Question | Choice |
|---|---|
| Review UI | A local web app served by the pipeline (localhost, opens in the browser, works on Mac and Windows) |
| URL list | Any link, using a downloader (yt-dlp); every clip keeps its URL, title and uploader; unknown or platform licenses are flagged high-risk and need a written note to approve |
| Script length | 90 seconds or more (default target 260 words) |
| Editor after render | Reorder, swap, delete and edit text, then re-render (about 4 minutes each time, because MoneyPrinterTurbo renders the whole video at once) |
| Videos | Stay local. Never committed to git (`.gitignore`) |

## Order of work

1. **Longer, editable script.** Own script writer, word count and estimated seconds at the
   review gate, edit a scene's text (terminal now, web page in step 3). *Built.*
2. **More material: a URL list.** A file or page where you paste URLs. Each is downloaded into
   the project (`sources/urls/`), with URL, title, uploader, duration and license kept in the
   manifest and the decision log, and a suggested position in the video. Also more free stock
   sources (Pexels and Pixabay with a free key, Internet Archive, NASA).
3. **Review web app.** Every photo and clip on one page with its source, license, flags and an
   approved / rejected badge, and a "still to review" count before moving on. Script editor on
   the same page.
4. **After-render editor.** Timeline of scenes: drag to reorder, replace or remove a clip, edit
   the text, then re-render. Not a live-preview video editor (see decisions).
5. **True-crime mode.** After the above; its own review states and guardrails.

## What each step depends on

- Steps 3 and 4 share one web app and one set of API routes, so step 3 is built to hold both.
- Step 2 needs `yt-dlp` and `ffmpeg` in the pipeline's Docker image, and internet access from it.
- Downloading videos from platforms can break their terms of service and copyright. The
  pipeline flags it and records the decision; the risk stays with you (Limitation #3).

> Update: the asset review web page (thumbnails, scores, Use/Reject, search again) is built, and so is the scene/script page
> (live full-script view, editable per-scene narration and clip, reorder, approve/rewrite). See docs/REVIEW_UI.md.

## Done: script-writing styles -- 4 retention-mechanics scriptwriter personas (2026-09-25)
Requested directly: 4 system prompts (History/Conspiracy/True Crime, STEM, DTC/Pet/Food Marketing,
Math/Stats/CS), each with its own retention-mechanics instructions and word-count target, for the Gate 3
scriptwriter. First increment toward a "script first" pipeline the same conversation also asked about --
this piece only adds the scriptwriter voices; it does not reorder keywords-before-script (see
docs/SCRIPT_STYLES.md's "What this does NOT do" for why that's a separate, bigger decision, and what the
natural next increment looks like).

- New optional `Job.script_style` (`pipeline/core/models.py`): one of 4 values, `None` by default -- a job
  with no script_style is byte-for-byte unaffected. Independent of `Job.niche` (different code paths,
  different purpose, no niche equivalent for `math_cs`) -- see docs/SCRIPT_STYLES.md "How this relates to
  niches".
- `pipeline/stages/scenes/script_styles.py` (new): the 4 personas, verbatim as requested.
- `pipeline/stages/scenes/writer.py`'s `ScriptWriter`: a styled call sends the persona as a `system` message
  and the task (topic + word target + reviewer feedback) as the `user` message, skips the default
  source-grounding entirely, and checks the word count against the style's own range instead of the
  config's single `target_words`. Same retry/fallback transport (`post_chat()`) as every other path.
- `--script-style` on `scripts/poc.py`; `POST /jobs {"script_style": ...}`; review page shows a "script
  style: ..." chip in the job header.
- Deliberately NOT grounded in sourced text (unlike the default prompt) -- see docs/SCRIPT_STYLES.md
  "Accuracy trade-off" for what that means for `true_crime_mystery` specifically.

## Done: per-stage Gemini key override for script generation (2026-09-25)
Requested directly, following on from the script-styles work above and a question about splitting Gemini
quota (rate limits are scoped per Google Cloud project, not per key -- `[keywords]`/`[relevance]`/`[script]`
all defaulting to the same `GEMINI_API_KEY` means all three share one project's budget). `[keywords]` and
`[relevance]` already had their own override env var (`KEYWORD_LLM_API_KEY`/`RELEVANCE_LLM_API_KEY`,
falling back to that section's `api_key_env`, falling back to `GEMINI_API_KEY`); `[script]` was the odd one
out -- it only ever read `script_cfg["api_key_env"]` directly, with no per-stage override layer.

- `pipeline/stages/registry.py`'s `script_writer()`: added `SCRIPT_LLM_API_KEY` as the same
  override-then-fall-back-to-`api_key_env` pattern the other two stages use. No other behavior change --
  every job that hasn't set this stays on exactly the key it resolved to before.
- `docs/LOGGING.md` "Splitting quota across stages" (new): explains the per-project quota scoping, all three
  override vars in one place, and which stage is actually worth splitting off first (script is the heaviest,
  most bursty caller while iterating on `script_style` prompts -- every Gate 3 rejection is another call).
- `.env.example`/`config/pipeline.toml` `[script]`: documented, matching the existing `[keywords]` comment.
- `tests/test_registry_llm_keys.py` (new, 9 tests): pins the override precedence for all three stages
  (env override > section `api_key_env` > `GEMINI_API_KEY` default > no key -> stage disabled), and that
  setting one stage's override doesn't leak into the other two.

## Done: content niches -- niche-aware keyword phrasing + Stage 3 evaluation schema (2026-09-25)
Requested directly, as a full architecture spec ("MASTER NICHE PROMPT STRATEGIES" for 5 niches -- True
Crime, Conspiracy, Science & Astronomy, Pet Product, Food/Bakery -- plus an "Evaluation Engine Output
Schema" for Stage 3). Layered onto the existing 3-gate pipeline rather than a rearchitecture into the
spec's own 5-stage numbering -- see docs/NICHES.md's "What this does NOT do" for the reasoning and what a
follow-up would need to cover if that reordering is actually wanted.

- New optional `Job.niche` (`pipeline/core/models.py`): one of the 5 values, `None` by default -- a job
  with no niche is byte-for-byte unaffected by any of this.
- `pipeline/niches.py`: the per-niche source rewards/penalties (reusing the existing `ARCHIVE_SOURCES`/
  `STOCK_SOURCES` groups, no new source adapters) and the keyword-prompt guidance strings.
- `pipeline/stages/keywords/llm.py`: appends one niche-specific guidance line to the keyword-writing
  prompt when a job has a niche set.
- `pipeline/vetting/niche.py` (new): computes the requested schema per asset every vetting round --
  `niche_evaluated`/`relevance_score` (1-10, rescaled from the existing 0..1 relevance)/`aesthetic_fit`
  (`Excellent`/`Acceptable`/`Jarring`, deterministic by source adapter)/`risk_assessment` (quotes the
  existing risk summary)/`reasoning`/`action` (`Approved`/`Flagged for Review`/`Rejected`, a SUGGESTION
  only -- never applied to `Asset.status`; Gate 2 still needs an explicit human decision on every asset).
  Deterministic and explainable, the same philosophy as `rules.py`'s risk rules -- no new paid LLM call.
- New `GET /jobs/{id}/niche-evaluation` report endpoint; `--niche` on `scripts/poc.py`; the review page
  shows an "aesthetic: ..." badge and the evaluation's reasoning in the existing "Why this score and risk"
  panel, plus a niche chip in the job header.
- Explicitly NOT implemented: Instagram/TikTok as searchable sources for Pet/Food (no adapter exists for
  either platform's API; would need new paid/authenticated access -- not added without approval). A
  specific Instagram/TikTok URL still works through the existing "Add links" yt-dlp downloader.

## Done: stock photo pull budget + deferred relevance scoring ("stop go limits") (2026-09-25)
Requested directly: "let's pull stock photos first and score relevance later... we should have stop go
limits depending how much we've pulled already and be able to continue if we realize after vetting there
isn't more." Before this, every sourcing round auto-triggered relevance scoring immediately after
(`Orchestrator.run_pending`) with no way to separate the two, and there was no cap on how many stock photos
(pexels/pixabay/unsplash/nasa) a job could pull in total -- only a per-keyword cap (`per_query`).

**Stock photo budget** (`providers.options["stock_limit"]`, `pipeline/stages/sourcing.py`): caps STOCK_SOURCES
assets cumulatively across every round for the job, not per-round. `SourcingStage.run` skips a stock source
entirely once the running total already meets the cap, and truncates (doesn't reject outright) a single
fetch that would overshoot it mid-round -- either way a trace note carries both `skipped_source` (for the
log/manifest, consistent with every other skip reason) and `warning` (so the review page's existing
"Warning: ..." banner surfaces it with no new frontend plumbing). 0/unset stays unlimited, same as every job
before this option existed. Raising the number and clicking Next batch/Search again again is the "continue"
half -- nothing already pulled is lost or re-fetched.

**Deferred relevance scoring** (`providers.options["defer_relevance"]`): `_apply_vetting` (orchestrator.py)
passes empty topic_terms to `vet_all` when set, which suppresses `relevance()` (and the `RELEVANCE_LOW` flag
it can add) without touching any of the OTHER risk rules -- those don't depend on topic_terms at all, so
PLATFORM_SOURCE/LIC_*/etc. still fire exactly as before. The (possibly costly) TF-IDF/LLM scoring call is
skipped entirely at the two `run_pending` dispatch sites rather than just discarded, so nothing is spent on
it until asked for. A null-relevance asset is never hidden by the review page's min-score slider (`below()`
only hides a *scored* item under the threshold), so a deferred round's pending assets simply show up
unfiltered, with a **"not yet scored"** badge in place of a score.

**`Orchestrator.score_relevance()` / "Score relevance now"** (new `POST /jobs/{id}/assets/score-relevance`):
the only thing that ever scores a deferred round's assets, since nothing else re-triggers it automatically.
Always scores for real regardless of `defer_relevance` (`force_score=True` on `_apply_vetting`) -- the option
only ever silences the automatic post-sourcing trigger, never this explicit one. Works at any job state with
pending assets, same "not gated to one gate" precedent as `label_asset`/`set_asset_identity`.

Both options are exposed from the existing "Get more" panel (`pipeline/api/review_page.py`) right below
Next batch/Search again, following `max_queries`/`per_query`'s own pattern: they persist in
`job.providers.options` until changed again, sync from the job's current value on every page load, and a
blank stock-limit field never resets an existing one.

- 22 new tests: `tests/test_stock_budget.py` (7, the cumulative-cap/truncation mechanics via a fake
  `SourcingStage` adapter), `tests/test_defer_relevance.py` (4) and `tests/test_score_relevance_now.py` (5,
  full Orchestrator + mock-transport integration), 5 in `tests/test_api_sources.py` (the HTTP plumbing), and
  6 in `tests/test_review_page_stock_budget.py` (the frontend badge/why-text logic and a `STOCK_SOURCES`
  frontend/backend cross-check, same pattern as `SEARCHABLE_SOURCES`). Full suite: 579 passing (up from 551),
  zero regressions.

## Done: YouTube search no longer fails outright when one result hits YouTube's sign-in/bot wall (2026-09-24)
Real bug, reported verbatim: `YouTube search failed: YouTube search failed: [youtube] yAM3U7OrEaY: Sign in
to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication.` Two separate
problems in that one message.

First, yt-dlp's `ytsearchN:` was run without `--ignore-errors`, so the moment ONE video in the batch hit
YouTube's anti-bot sign-in wall (increasingly common, and per-video, not per-account or per-search), yt-dlp
aborted the *entire* run and the search returned nothing, even though other results in the same batch had
already been read successfully. `search_youtube` (`pipeline/sources/youtube_search.py`) now passes
`--ignore-errors` and parses whatever made it to stdout regardless of yt-dlp's overall exit code -- a
nonzero exit only becomes an error when nothing at all could be parsed out of it. When every result in a
batch does hit the wall, the error message now says so plainly and points at what to do instead (a
different/more specific query, or pasting the video's own link into "Add links") rather than yt-dlp's raw
`--cookies-from-browser` text, which isn't something this app does anything with. There's no fix here for
the block itself -- it would mean wiring in a real YouTube account's cookies, which is exactly the kind of
new credentialed integration the standing rule says not to add without asking first, and the check is
adversarial on YouTube's side regardless.

Second, the doubled "YouTube search failed: YouTube search failed: ..." in the report was its own separate
bug: `search_youtube`'s `RuntimeError` already carries a complete, user-facing "YouTube search failed: ..."
message, and `youtube_search_view` (`pipeline/api/app.py`) was wrapping it in a second one of its own.
Fixed to not re-wrap a message that already says so.

- 7 new tests: 5 in `tests/test_youtube_search.py` (the `--ignore-errors` flag is passed, partial results
  survive a nonzero exit, an all-blocked batch gets the clearer message), 2 in `tests/test_api_sources.py`
  (the double-wrap regression, plus the existing single-wrap case still passes). Full suite: 551 passing (up
  from 547), zero regressions.

## Done: "Set aside" panel for Irrelevant/Duplicate, and "no repeats" on re-running a search (2026-09-24)
Two more pieces of the same feedback thread as the "Find more" work above, both requested directly in one
message: "the ones that are irrelevant should get out of the view but should be saved in a tab where it
can be re-reviewed if needed" and "if i add items from these searches it should save, and then if i want
to do another iteration it should keep what was saved and move on to the next batch no repeats."

**Set aside.** Marking a card Irrelevant or Duplicate was leaving it sitting in the main grid forever --
both labels set `decisions[a.id]="reject"` (`setLabel()`), but nothing then moved the card anywhere, so
decided-and-rejected items just accumulated as clutter with nothing left to do about them. `notUsed(a)`
(`pipeline/api/review_page.py`) is true for anything locally decided `"reject"`, falling back to a saved
`status==="rejected"` when there's no local decision yet -- a local decision always wins over the
last-saved server status, so clicking Use on something saved as rejected from an earlier session brings it
back immediately rather than waiting on a save round-trip. `setAsideAssets()` filters the job to exactly
those, `visible()` now excludes them from the main grid, and `manualUrlAssets()` excludes them too so a
pending manual/search-pick item that gets marked Irrelevant moves straight to Set Aside rather than
lingering in "Added by hand". `setAsidePanel()` renders them in a new collapsed-by-default panel (mirroring
`manualLinksPanel()`'s layout, reusing the same `card()` renderer so Use/Duplicate/Irrelevant work
identically there), wired into `assetReviewBody()` right after "Added by hand".

**No repeats.** Checked before writing anything: whether an added search-pick item *persists* was already
true (`Orchestrator.reject_assets` only queues the next round's config, it never touches `job.assets`, and
`sourcing.py`'s per-source dedup already includes search-picked assets since `keep_one()` stamps
`source=self.name`) -- no fix needed there. What was actually missing was dedup on the two *on-demand*
search endpoints themselves: re-running the same "Find more" search in a later iteration could show a
result already added or already seen, since neither endpoint filtered against `job.assets` before. Fixed
in `pipeline/api/app.py`: `source_search_view` now drops any candidate whose URL is already in
`ctx.known_urls` (computed from all of `job.assets`, not source-scoped like the normal per-round dedup)
before truncating to the requested count; `youtube_search_view` can't over-fetch on demand the way the
archive-style adapters can, so it now asks `search_youtube()` for `count * 2` results and filters+truncates
after, so a full page of `count` new results still comes back even when some of what YouTube returns has
already been added.

- 6 new tests: 1 backend (`tests/test_api_sources.py`, dedup on the YouTube search endpoint; the two
  existing count-clamp tests there were also updated for the doubled overfetch), 5 frontend
  (`tests/test_review_page_manual_links.py`, covering the local-reject-wins-immediately case, the
  saved-status fallback, the local-approve-overrides-saved-reject precedence case, a pending
  manual/search-pick item moving straight to Set Aside, and Set Aside being independent of the kind/source
  filters). Full suite: 547 passing (up from 540), zero regressions.

## Done: fix "Find more" search-adds vanishing silently below the relevance threshold (2026-09-24)
Real bug, caught immediately: Aly tried "Add this one" on an Internet Archive/Commons search result and
reported "no it just jerks" -- the add was actually succeeding (that's the page reloading), but the new
asset immediately disappeared, because `assets_add_candidate` left it with the model's default
`import_method="search"`, and the review page's relevance-threshold filter (`visible()`,
`pipeline/api/review_page.py`) hides anything scoring under it by default with no exception for that value.
A hand-picked search result's title/description often won't text-match the approved keywords well, so it
commonly scores low -- exactly the same failure mode the existing "Added by link" panel was built to solve
for pasted URLs, just not extended to this new path when it shipped a day earlier.

Fix: a new `ImportMethod` value, `"search_pick"` (`pipeline/core/models.py`), stamped onto the asset in
`assets_add_candidate` (`pipeline/api/app.py`) right after `keep_one()` returns it (which leaves the
default, since it has no way to know the caller is a human pick rather than a normal batch search).
`manualUrlAssets()`'s filter (`pipeline/api/review_page.py`) now keeps `search_pick` visible alongside
`manual_url`, and the panel it feeds was renamed **"Added by hand"** and its copy broadened to cover both.
A YouTube pick already got this for free -- adding one reuses `/assets/add-url`, which already stamps
`manual_url` -- so only the three new sources needed the fix. Also added a line to the search-results
message pointing at where an add actually shows up ("Added by hand" panel, not necessarily the results list
itself), and a small `IMPORT_METHOD_LABELS` map so a card's own "Entered this job via" line reads in plain
English instead of a raw enum value.

- 5 new tests: 1 backend (`tests/test_source_search.py`, asserts the stamp), 4 frontend
  (`tests/test_review_page_manual_links.py`, mirroring every existing `manual_url` visibility test for
  `search_pick`, plus one proving the two can coexist). Full suite: 540 passing (up from 535), zero
  regressions.

## Done: inline search for Internet Archive/Chronicling America/Commons, and a "Find more" quick paste-back box (2026-09-23)
Requested directly, after Aly noticed most of "Find more" was "just links" and asked "why can't it search
call at the same time?" and "i need a way to streamline this." Internet Archive, Chronicling America and
Wikimedia Commons get the same "search right here, nothing downloads until you pick a result" treatment
YouTube already had -- unlike YouTube, these three already have a free no-key API AND already run as
automated sources elsewhere in the pipeline (`pipeline/sources/groups.py`'s `ARCHIVE_SOURCES`), so this
just exposes each source's own `search()` (`pipeline/sources/base.py`'s `HttpSource.search`) on demand
instead of building anything new. Two new backend endpoints (`pipeline/api/app.py`): `POST
/jobs/{id}/source-search` (list candidates, metadata only) and `POST /jobs/{id}/assets/add-candidate`
(download one and add it pending) -- the latter reconstructs the exact `Candidate` the search already
returned rather than re-deriving license/author/attribution from a bare URL the way `/assets/add-url`'s
generic yt-dlp/direct download has to. A new `HttpSource.keep_one()` (`pipeline/sources/base.py`) factors
the single-candidate-download step out of `fetch()`'s batch loop without changing `fetch()` itself; a new
`Registry.source(name)` (`pipeline/stages/registry.py`) gets one adapter by name outside a normal sourcing
round. Google Images and FindAGrave stay plain link-outs (no free API at all); TikTok and Facebook stay
plain link-outs too (no free API, and the platform-download risk that already gets anything from them
auto-flagged high risk). For those four, a one-line paste-back box now sits right in the "Find more" panel
(`findQuickAdd()`), calling the same `/assets/add-url` "Add links" already uses, so finding something on
one of those sites doesn't mean scrolling away to paste its link in.

- Backend: 2 new endpoints, `INLINE_SEARCH_SOURCES` (matches the frontend's `SEARCHABLE_SOURCES` name for
  name), `HttpSource.keep_one()`, `Registry.source()`. 13 new tests (`tests/test_source_search.py`),
  offline via `httpx.MockTransport`, calling the real `search()`/`keep_one()` through the endpoints rather
  than mocking the adapter away.
- Frontend: `SEARCHABLE_SOURCES`, `srcState()`/`searchSource()`/`addSourceCandidate()`/`sourceResultCard()`
  (generalized per-source state, parallel to the existing YouTube-only `yt*` variables/functions, which
  were left untouched), `inlineSearchBlock()` (shared render shape for all four "search here" blocks,
  YouTube included), and `findQuickAdd()`. 6 new tests (`tests/test_review_page_source_search.py`),
  extracting the real JS and asserting the frontend/backend source lists actually match. Full suite: 535
  passing (up from 516), zero regressions.

## Done: TikTok and Facebook link-outs in "Find more"; Instagram explicitly excluded (2026-09-23)
Immediate follow-up: "what about facebook, instagram, tiktok?" -- same honest split as the rest of the
panel rather than adding all three for symmetry. TikTok and Facebook got a genuine free query-string
search URL added to `FIND_SITES` (`pipeline/api/review_page.py`), same discovery-link-only pattern as
Google Images/YouTube -- Facebook's carries an explicit note that it almost always needs you already
logged in to that browser to show anything. **Automated pull/search for any of the three was ruled out**,
not deferred: yt-dlp's search shortcut is YouTube-specific, it has no equivalent for these, and all three
have some of the strongest anti-scraping/login-wall protection of any platform -- building automated
search against them would mean working around that at volume, a materially bigger step into ToS exposure
than one deliberately pasted link. **Instagram got no button at all**, not even a link-out: unlike the
other seven, it has no plain query-string search URL to link to -- its search is entirely logged-in and
JS-driven -- so a button would only ever land on Instagram's login page, not a shortcut to anything.

- 1 new test (`tests/test_review_page_find_more.py`, now 10): `expected_hosts` extended with
  `www.tiktok.com`/`www.facebook.com`, plus a dedicated guard, `test_instagram_is_deliberately_not_offered`,
  so Instagram's absence stays a documented decision rather than something that could silently drift back
  in unnoticed. Full suite: 514 passing.

## Done: YouTube auto-search in "Find more", and risk reasons open by default (2026-09-23)
Two follow-ups requested directly right after the "Find more" panel shipped. First: "is there a way to
automate searching from here and pulling it and placing it a different section to review?" -- answered
honestly per site rather than building blind: Internet Archive/Chronicling America/Commons are already
automated sources (`docs/SEARCH_PLANNING.md`), Google Images/FindAGrave have no API and no rights metadata
worth automating, and YouTube was the one real, buildable gap -- yt-dlp already does the searching the
"Add links" download uses, `ytsearchN:query` needs no paid key. Second: "reasons why high risk etc any
information to make decisions" -- that information already existed (every card's "Why this score and
risk" section: rule id, severity, message, evidence, plus category/identity/rights badges) but was
collapsed by default, which is very likely why a high-risk asset read as broken/rejected earlier
(`89b0faa`) instead of merely undecided.

- **`pipeline/sources/youtube_search.py`** (new): `search_youtube(query, count, runner)` runs
  `yt-dlp --skip-download --dump-json ytsearchN:query` and parses one candidate per JSON line (id, title,
  url, uploader, duration, thumbnail, upload_date, description) -- metadata only, nothing downloaded here.
  Deliberately NOT registered as a sourcing-round source (`pipeline/stages/registry.py`): that loop
  downloads everything it finds automatically, which is the wrong default for platform video -- would
  multiply the rights exposure #9/#14 already flag for one pasted link across a whole batch.
- **`pipeline/api/app.py`**: `POST /jobs/{id}/youtube-search` (Gate 2 state only, same guard as
  `/assets/add-url`) -- `{query, count?}` (count clamped 1-10, default 5) -> the candidate list. Every
  call, success or failure, is logged to `sources/youtube_search/requests.jsonl` via the same `LoggedHttp`
  every other source uses. Picking a result doesn't call anything new -- the frontend posts the video's own
  `url` to the existing `/assets/add-url`, so it goes through the identical download/pending/high-risk/note
  path as a hand-pasted link.
- **`pipeline/api/review_page.py`**: `findMorePanel()` gained a "Search YouTube here" button (`searchYoutube()`)
  using whatever's already in the query box, a result-count picker, and `ytResultCard()` per candidate with
  an "Add this one" button (`addYoutubeCandidate()`) that posts to `/assets/add-url` with a note recording
  which search found it, then drops that candidate from the results list and reloads the job so it shows up
  in "Added by link" like any other pending manual asset. Separately: `card()`'s "Why this score and risk"
  `<details>` is now `open` by default, with a new `whyOpen` (asset id -> bool) tracking any reviewer
  collapse across re-renders -- same pattern `checklistOpen`/`cropOpen` already established for exactly this
  reason (a hardcoded `open:true` would silently snap back open on the next re-render even after a reviewer
  closed it).
- 16 new tests: `tests/test_youtube_search.py` (8, fake-runner unit tests for `search_youtube()`, same style
  as `UrlListTests` in `test_more_sources.py`) and `YoutubeSearchEndpointTests` in `tests/test_api_sources.py`
  (8, HTTP-level, mocking `search_youtube` the same way `AssetsAddUrlEndpointTests` already mocks
  `UrlListSource` -- deliberately NOT subclassing that class, to avoid silently re-running its own five
  tests under a new name). Caught one real bug along the way: `count = max(1, min(int(d.get("count") or 5),
  10))` treated an explicit `count: 0` the same as "not provided" (`0 or 5` -> `5` in JS-adjacent Python
  truthiness) instead of clamping it up to 1 -- fixed to check for `None`/missing explicitly. Full suite:
  513 passing.

## Done: "Find more" panel, Gate 2 (2026-09-23)
Prompted directly by a real coverage gap during testing: Aly asked why a well-documented, decades-old
case wasn't turning up photos, newspapers, or footage. Traced honestly rather than promised a fix --
every automated source in this pipeline is a free, openly-licensed archive API (Wikipedia/Commons,
Internet Archive, LOC/Chronicling America, Smithsonian, DPLA, Europeana, Openverse, Flickr, plus generic
stock sites, `docs/SEARCH_PLANNING.md`). That's a structural boundary, not a bug: the specific press
photos, platform videos, and public records that would actually cover a case like this mostly live
outside that pool entirely, in places with no bulk search API a source adapter could call (a state court
archive, a specific department's mugshot page, a documentary on YouTube). The honest fix isn't a new
automated source -- it's making the manual search a person already has to do faster, and making sure
whatever they find lands back in the review flow the normal way.

- **`pipeline/api/review_page.py`**: new Gate 2 panel, `findMorePanel()`, between the visual checklist
  and the "Added by link" panel. A text box defaults to the job's subject; `findMoreSuggestions()` offers
  every approved keyword's `term`/`entity`/`aliases` plus every visual-checklist item's `label` as
  one-click chips (deduped case-insensitively), so a reviewer isn't retyping case details by hand. Six
  buttons (`FIND_SITES`) each `window.open()` a real, properly-encoded search URL on a free site --
  Internet Archive, Chronicling America (the Library of Congress's own public search UI for that
  collection, not the JSON API the automated source calls, so it's not limited to what that adapter
  already tried), Wikimedia Commons, YouTube, Google Images, and FindAGrave -- in a new tab. This makes
  zero network calls of its own and adds nothing to the job; it only opens a page for a human to look at
  and judge. Google Images and YouTube's buttons carry an explicit note in the UI that they're discovery
  tools, not rights sources -- whatever turns up there still needs its license checked, and a documentary
  clip goes back in through "Add links" (already auto-flagged high risk, #9) rather than a screen
  recording.
- 9 new tests (`tests/test_review_page_find_more.py`), same approach as the other review-page test
  files: pull `FIND_SITES`/`findMoreSuggestions()`/`findMoreQuery()` out of `review_page.PAGE` and run
  them for real in Node, including a check that every site's URL is correctly percent-encoded (a raw
  space or `&` in a query would silently break or truncate the search) and points at the expected host.
  Full suite: 497 passing.

## Done: "Added by link" panel, Gate 2 (2026-09-23)
Prompted directly by a real bug report during testing: Aly added an Instagram link through "Add links,"
got a success message, then couldn't find it anywhere on the page. Traced it rather than guessing --
`add_reviewable_asset()` always lands a pasted link as `pending` (never auto-approved, #9), and two
independent, unrelated filters were both working against it at once: any social/video platform link is
auto-flagged **high risk** by vetting (`PLATFORM_SOURCE`, `pipeline/vetting/rules.py`) regardless of
content, which sorts it to the very end under the default risk-first sort, and its caption/title text
often doesn't match the case's approved keywords, so it also frequently scores under the relevance
threshold and gets hidden by the default score filter -- so it was really in the grid the whole time,
just sorted last and hidden by score simultaneously. Not a bug, but a genuine discoverability gap worth
closing rather than just explaining away.

- **`pipeline/api/review_page.py`**: `manualUrlAssets()` picks out every asset with `import_method ===
  "manual_url"` (already set by `pipeline/sources/urls.py::fetch_one()`, unrelated to this change) that's
  still `pending`; `visible()` now excludes exactly those from the main filtered/sorted grid, and a new
  `manualLinksPanel()` renders them in their own always-visible section above the grid, reusing the same
  `card()` component (so Use/Duplicate/Irrelevant, the high-risk note requirement, everything works
  identically) -- filters and sort order simply don't apply there. Once a decision is made, the asset's
  `status` moves off `pending` and it naturally rejoins the normal grid on the next refresh, subject to
  the normal filters like anything else.
- Manually added files were already downloading into their own `sources/urls/` folder on disk, separate
  from every other source -- nothing needed there; the gap was purely in the review page's visibility.
- 7 new tests (`tests/test_review_page_manual_links.py`), same approach as `test_review_page_sort.py`:
  pull the actual `visible()`/`manualUrlAssets()` JS out of `review_page.PAGE` and run it for real in
  Node, rather than a reimplementation that could silently drift from what ships. Full suite: 488 passing.

## Done: manual crop, Gate 3 (2026-09-23)
Prompted directly ("do we have a UI for drag and drop and cropping of videos" -> "i definitely want a cropping
feature"). Investigated MoneyPrinterTurbo first rather than assuming: it always auto-center-crops a clip to the
render's aspect ratio itself (`vendor/MoneyPrinterTurbo` `app/services/video.py::_fit_clip_to_canvas`), but that
crop is always centered -- `MaterialInfo` (`app/models/schema.py`) has no field for a custom crop box, so there
was no way to hand MPT a framing choice even if the pipeline wanted to. A follow-up `AskUserQuestion` scoped the
build: photos AND video clips (not just stills), the control lives on each Gate 3 scene card (not a separate
Gate 2 step), and it's a reposition/zoom interaction constrained to the target aspect ratio (not a freeform
any-aspect rectangle) -- mirroring what MPT's own auto-crop already does, just letting a human move the window.

- **`pipeline/stages/render/crop.py`** (new): `crop_box()` computes the same (x, y, w, h) ffmpeg window MPT's own
  cover-crop would use at `zoom=1.0`/centered, but positioned by a human's `center_x`/`center_y` (0..1 fractions
  of the source frame) and tightened by `zoom` (>=1.0); always clamped inside the source frame. `apply_scene_crop()`
  bakes it into an actual cropped file with one `ffmpeg -vf crop=...` call (images and video alike -- ffmpeg treats
  a single image as a one-frame stream, so the same command handles both; video keeps its audio via `-c:a copy`),
  via the same injectable-runner pattern as `pipeline/sources/urls.py::run_subprocess` so tests never invoke real
  ffmpeg. Falls back to the unmodified clip (letting MPT's own auto-crop handle it) whenever there's nothing to
  act on: no crop set, no asset, unknown width/height, or a crop window that already covers the whole frame.
- **`Scene.crop`** (`pipeline/core/models.py`, a new `SceneCrop`): `center_x`/`center_y`/`zoom` plus who/when.
  `None` means "MoneyPrinterTurbo's own automatic center-crop", same as before this feature existed -- nothing
  changes for a job that never touches it.
- **`Orchestrator.set_scene_crop()`/`remove_scene_crop()`**: range-validated (0..1 centers, zoom>=1.0), requires
  the scene to already have a clip, only allowed during `SCENES_REVIEW` (same gating as `edit_scenes()`). A crop
  is framed for one specific clip, so `edit_scenes()`, `add_scene_asset()` and `approve_pending_scene_asset()` all
  clear `scene.crop` whenever they actually change `scene.clip_path` -- a stale crop silently misapplied to a
  swapped-in clip was the one correctness trap worth guarding against here.
- **`MptRenderStage`**: resolves each scene's clip through `apply_scene_crop()` before building `video_materials`,
  writing cropped copies to the project's `cropped/` folder (already mounted into MoneyPrinterTurbo, so no new
  path mapping needed). An ffmpeg failure is logged and falls back to the uncropped clip -- ffmpeg trouble on one
  scene's framing never fails the whole render.
- **New routes**: `PATCH /jobs/{id}/scenes/{scene_id}/crop`, `POST .../crop/remove`, `GET /render-settings`
  (`{aspect}` -- the frontend's crop tool needs the deployment's configured aspect ratio to draw a correctly
  proportioned crop box and run the identical `crop_box()` math client-side, so the preview matches the render
  exactly).
- **Gate 3 UI (`pipeline/api/review_page.py`)**: a collapsed "Crop: automatic" / "Crop: adjusted" line under each
  scene's clip picker. Open it to drag a live preview (native `mousedown`/`mousemove`/`mouseup`, restyling the
  preview's `img`/`video` element directly during the drag rather than calling the full `render()` on every
  pixel of movement, so it stays smooth despite this page's rebuild-the-whole-DOM-per-render architecture) and a
  zoom slider, then **Save crop** or **Reset to auto-crop**. Disabled with an explanatory note if the clip's
  width/height aren't known, or if there's an unsaved clip-picker change pending (crop that, not this).
- 40 new tests: `tests/test_scene_crop.py` (`crop_box()` math, `apply_scene_crop()` fallbacks/ffmpeg invocation
  via a fake runner, `MptRenderStage` actually using a cropped path, orchestrator validation/gating/clearing) and
  `SceneCropEndpointTests` in `tests/test_api_sources.py` (the routes over real HTTP). Embedded JS syntax-checked
  with `node --check` (no npm registry access in this environment for a real JS test framework). Full suite: 481
  passing.
- **Not done**: no live preview of MoneyPrinterTurbo's own pan/zoom effect on top of the crop (the tool shows
  framing, not motion); no per-job aspect-ratio override (the render aspect is one deployment-wide config value,
  same as before this feature).

## Done: five more public sources -- Openverse, DPLA, Flickr, Europeana, Chronicling America (2026-09-23)
Added after "not getting good results" / wanting real case-related photos beyond what Wikipedia+Commons alone turn up.
`--sources` now also accepts `openverse` and `chronicling_america` (no key), and `dpla`/`flickr`/`europeana` (free keys,
see `.env.example`). All five are treated as archive-style sources by the keyword prompt (docs/RUNNING.md "More
sources for real case photos"). Flickr Commons and Openverse in particular are aimed at real historical/archival
photos, not generic stock. Also flagged clearly (and in the new RUNNING.md section) that none of these -- old or
new -- will surface actual mugshots/crime-scene/victim photos, which are almost always copyrighted news/police
material, not in any free public archive.

Also clarified: "add a video/photo manually by URL" already existed at Gate 2 asset review (the "Add links" box,
`docs/REVIEW_UI.md`) before this change -- nothing new was needed there, just pointed out since it wasn't obvious.


## Done: LLM keyword prompt rewritten for photo/video search, not SEO (2026-09-23)
Prompted directly, after a real run: Aly's "The Dyatlov Pass Incident" job pulled 74 assets and only ONE
cleared the asset review's default 50% relevance threshold. Traced it to the actual keywords approved --
`--keywords llm`'s prompt asked the model to act as "an SEO analyst for short-form vertical video," so it
returned phrases like "dyatlov pass explained," "... TikTok," "... 2024 update" -- good for making a finished
video findable on YouTube, useless for finding EXISTING photos: no stock photo or Wikipedia image is ever
captioned that way, so the relevance scorer's word-overlap check came back 0% for 73 of 74 assets. Not a bug
in the scorer or the vetting -- the prompt was asking the model to solve the wrong problem.

- `pipeline/stages/keywords/llm.py`'s `PROMPT` is rewritten around "these are SEARCH QUERIES for photo/video
  libraries, not SEO," with explicit do's (2-6 words, name a place/object/scene/activity/weather/era a camera
  could show) and don'ts (never "explained"/"documentary"/"TikTok"/"short video"/"2024 update"/"facts"/"top
  10" -- those describe a video ABOUT the topic, not a photo OF something).
- New `_source_guidance()` tailors the prompt to `job.providers.sources`: historical-archive sources
  (Wikipedia, Commons, Internet Archive, LOC, Smithsonian) get told to include era/place/document specifics;
  modern generic-stock sources (Pexels, Pixabay, Unsplash, NASA) get told the OPPOSITE -- favor generic,
  timeless visual subjects, since a stock site has nothing event-specific; a job with no web sources at all
  gets guidance aimed at matching local `library/clips/` filenames instead. Mixed source lists get both
  pieces of applicable guidance.
- `search_volume`/`difficulty` stay in the output schema (nothing downstream changes -- `Keyword` model,
  `poc.py`'s `vol~` display, the decision log are all untouched), but the prompt now says explicitly they're
  "context only, never a reason to phrase `term` differently," so that framing can't leak back into how
  phrases get worded.
- `last_trace` (and so the `proposed_keywords` decision-log entry, and the new `keywords_proposed.json` from
  the entry above) now also records `sources_this_round`, so which library-tailored guidance actually applied
  to a given round is part of the permanent record, not just implied by the prompt text.
- Couldn't validate against a live model call from this sandbox (its network egress blocks direct API calls
  the same way it blocks PyPI -- confirmed while trying); validated instead by rendering the actual prompt
  for Aly's real job (archive+stock mix, stock-only, archive-only, and no-sources cases) and reading it end to
  end, plus 7 new tests (`tests/test_stages.py`) covering every `_source_guidance()` branch, that the old "SEO
  analyst" framing is gone, and that `PROMPT.format()` never raises for any real source combination.
- **Not done**: no change to `scripts/make_keywords.py`'s own prompt -- it was already written this way
  (true-crime-archive-specific, never had the SEO framing) and wasn't the source of the problem. Full suite:
  280 passing, no regressions.

## Done: keyword files, always -- proposed/approved always saved, manual edits get a real file, subjects get a reusable library (2026-09-23)
Prompted directly, after asking where keyword generation happens: Aly wanted a file created every time,
including for `--keywords manual`. Scoped with three `AskUserQuestion` rounds into: both a proposed-before-Gate-1
and an approved-after-Gate-1 file (not just one or the other); `manual` folded into the same file-based flow
instead of staying a separate typed-into-the-terminal path; and a per-subject reusable library on top, so a
later job on the same subject can be offered what was approved before instead of redoing it.

- **Every job, every provider**: `Orchestrator._apply_keywords()`/`review_keywords()` now write
  `keywords_proposed.json` and `keywords_approved.json` into the job's own project folder unconditionally --
  `llm`, `manual`, a reused library set, or an edited draft all go through the exact same two writes, since
  they all pass through `_apply_keywords`/`review_keywords` either way. A second round after a Gate-1
  rejection overwrites `keywords_proposed.json` with the current round only; the full history of every round
  already lives in `decisions.jsonl` (append-only, never touched by this).
- **`--keywords manual` with no `--keywords-file`** no longer silently guesses from the subject the instant
  the job starts. `scripts/poc.py`'s `edit_keywords_draft()` writes an empty, editable file
  (`library/keywords/_drafts/<slug>-<timestamp>.txt`), prints its path and waits for Enter; leaving it blank
  falls back to the old subject-derived guess, so nothing regresses for anyone who liked the old behavior.
- **Reusable per-subject library**: once a job's keywords clear Gate 1, `review_keywords()` also copies them to
  `<library keywords_dir>/<subject slug>/<job id>.json` (new `config/pipeline.toml` key, `[library]
  keywords_dir`, default `library/keywords`, blank turns it off). `scripts/poc.py`'s `choose_library_set()`
  offers whatever's saved for the current subject before generating anything new; picking one only *reads* that
  file (never modifies or overwrites it -- every job's copy is named by its own job id) and still goes through
  Gate 1 like anything else. An explicit `--keywords-file FILE` always wins over both the reuse prompt and the
  draft-file flow, unchanged from before.
- **Test-safety by design, not by convention**: `keywords_library_dir` defaults to `None` (feature off) unless
  `settings["library"]["keywords_dir"]` is actually present, rather than a hardcoded `Path("library/keywords")`
  CWD-relative fallback -- every existing unit test builds its own `Orchestrator` with a bare `settings={}`
  and would otherwise have started writing real files into this repo's own `library/keywords/` on every test
  run. Production gets the key automatically from `config/pipeline.toml`; the per-job
  `keywords_proposed.json`/`keywords_approved.json` files are unaffected either way, since they live inside the
  job's own already-sandboxed project folder.
- 7 new orchestrator tests (`tests/test_keywords_files.py`): both files' contents, a second round overwriting
  cleanly, the library copy off by default vs. on when configured, and two jobs on the same subject getting
  separate files rather than one overwriting the other. Plus a scripted smoke test of every new `poc.py`
  function (`slug`, `library_sets_for`, `choose_library_set`, `edit_keywords_draft`, `resolve_keywords_source`)
  with `input()` mocked, since poc.py itself has no automated test file (it's a thin HTTP client, same as
  before). Full suite: 273 passing.

## Done: drag-and-drop footage + link extraction, the two RankReel features Aly asked for by name (2026-09-23)
Prompted directly: comparing this pipeline against three different "RankReel" products turned up two real gaps
worth closing -- RankReel's drag-and-drop editing, and pulling video straight off a URL the way RankReels.ai
and the existing `pipeline/sources/urls.py` (yt-dlp) already do. Two follow-up `AskUserQuestion` calls scoped
exactly what to build (both a job-creation-time and a mid-job path for links; all three drag targets: an
approved asset, a local file, and a URL).

- **URL input, job creation**: `scripts/poc.py --urls -` reads pasted/piped multiline URL text (same
  `url | note | position` format the `--urls FILE` flag always used) instead of requiring a saved file first.
- **URL input, mid-job (Gate 2)**: a new "Add links" box in the review page's "Get more" panel, backed by
  `Orchestrator.reject_assets(extra_urls_text=...)` -- merges newly pasted links into
  `job.providers.options["urls"]` (deduped by URL, existing notes kept) and adds the `"urls"` source to
  `job.providers.sources` if the job wasn't already pulling from it, then re-runs sourcing exactly like
  "Search again"/"Next batch" already do. A job that started with only keyword-based sources can now have a
  link pasted into it later without being recreated.
- **Drag-and-drop, Gate 3 (`pipeline/api/review_page.py`)**: the existing scene-reorder drag handle is now one of
  three things a drop on a scene card can mean -- (1) drag a thumbnail from a new strip of approved assets onto
  a scene (visual version of the existing Clip dropdown, which is still there too), (2) drag a file straight
  from your computer onto a scene card (uploads it), or (3) drop a video/photo URL onto a scene card (runs it
  through yt-dlp/direct-download, same as the `urls` source). `onSceneDrop()` disambiguates by what's actually
  in the drop event.
- **New backend for (2)/(3)**: `pipeline/sources/upload.py` (`asset_from_upload` -- no sidecar is possible for a
  one-off drop, so like `folder.py`'s un-sidecared files it's imported with no license, which vetting flags high
  risk until a note says it's fine), `UrlListSource.fetch_one()` (a single-URL version of the batch `fetch()`),
  and two new orchestrator methods: `add_scene_asset()` (vets the new asset with the same `vet_asset()` every
  sourced asset gets; low/medium risk or a note given up front -> approved and assigned to the scene immediately;
  high risk with no note -> still added to `job.assets` as `pending` and left unassigned, **never silently
  discarded**, since the file is already downloaded/uploaded by that point) and `approve_pending_scene_asset()`
  (finishes approving + assigns once a note is given). New routes: `POST /jobs/{id}/scenes/{scene_id}/upload`
  (multipart), `/from-url`, `/approve-pending`.
- A pending-but-pending Gate 3 asset shows in a new "Dragged in, needs a decision" panel with a note field and
  an Approve & use button; the frontend remembers which scene it was dropped on (`pendingIntent`, client-side --
  nothing to add server-side, since Gate 2's existing guard already guarantees no asset can be `pending` once a
  job reaches scene review through the normal flow, so any `pending` asset visible at Gate 3 can only be one of
  these drag-and-drop adds).
- 10 new orchestrator tests (`tests/test_scene_assets.py`): url-merge + dedup, low/high risk add, unknown scene
  id, wrong state, and the pending -> approve handoff. Embedded JS syntax-checked with `node --check` (same
  constraint as the drag-reorder work above: no npm registry access in this environment to run a real JS test
  framework). Full suite: 266 passing.
- **Not done**: the third RankReel-family gap found during the comparison (RankReels.ai's talking-head "digital
  twin" avatar narration) and the RankMyReel-style post-render viral-potential scoring -- the latter is the
  same "performance feedback loop" already marked PROPOSED elsewhere in this roadmap; neither was asked for here.

## Done: clip matching quality + "photos keep looping" fix (2026-09-23)
Prompted by "I feel like the photos don't fit with the script" and "I don't like that the photos just keep
running in a circle" -- investigated `pipeline/stages/scenes/clips.py` (how a scene's narration gets matched
to an approved asset) and `pipeline/stages/render/mpt.py` (what actually gets sent to MoneyPrinterTurbo) to
find the real cause of both rather than guessing:
- **Stopword pollution in matching**: `AssetClipSource`'s word-overlap matcher counted ANY shared word,
  including "the"/"and"/"with"/"for" etc., so two totally unrelated pieces of text could look like a "match"
  on nothing but grammar. Now filters through the same `STOPWORDS` list the relevance scorer already uses
  (`pipeline/vetting/rules.py`), so a match has to share an actual subject word.
- **The real source of the looping**: when a job has fewer approved assets than scenes, the exhausted scenes
  got `clip_path = None` -- and `MptRenderStage.run()` silently DROPS any scene with no clip from what it
  sends to MoneyPrinterTurbo (`clips = [s.clip_path for s in scenes if s.clip_path]`), so the video ends up
  with fewer clips than narration segments. MoneyPrinterTurbo then has to stretch or loop whatever clips it
  DOES have to cover the full spoken length -- an unpredictable repeat with no connection to what's being
  said at that point. Fixed by making `AssetClipSource` reuse its best-matching approved asset again, on
  purpose, once the pool runs out, instead of returning `None` -- every scene now gets a clip, and any repeat
  is the closest match available and is labeled `REUSED` in that scene's "clip chosen because" reason
  (visible in the terminal and the review page), rather than an invisible MoneyPrinterTurbo-side workaround.
  `LocalFolderClipSource` (your own `library/clips/` footage, not sourced photos) intentionally keeps its
  existing "never reuse, leave it empty" behavior -- unlike an approved-photo pool, that gap is fixed by just
  adding more files to a folder you control directly, so silently reusing there felt like it would hide the
  actual signal ("you need more clips") rather than help.
- **Script-writer prompt**: `pipeline/stages/scenes/writer.py`'s `PROMPT` (used whenever a script-writer LLM
  key is configured, which this project's setup already has) now explicitly tells the model that one
  paragraph becomes one scene and gets matched to a real photo afterward, so it should anchor every paragraph
  to one concrete, picturable subject from the source text (a specific place, person, document, date or
  moment) instead of abstract or purely reflective lines nothing can actually depict. Left MoneyPrinterTurbo's
  own fallback script prompt (`pipeline/stages/scenes/mpt.py`, only used when no script-writer key is set)
  alone -- it's under a hard 2000-character limit MoneyPrinterTurbo enforces, already tight, and not the path
  this project's config actually uses.
- 3 new tests (`tests/test_more_sources.py::ClipMatchingTests`) cover the stopword fix, the reuse-instead-of-
  None fallback, and the true "zero approved assets" case still correctly returning no clip. Full suite: 237
  passing.
- Not done, and probably the more complete fix: actually generating an AI image/clip for a scene that has no
  good match, instead of reusing an unrelated one -- raised in the same feedback ("we should also have an
  option to ask them to render AI"). Needs a provider decision (a free-tier image API, e.g. Pollinations.ai
  keyless, or Gemini's own image generation if the account's tier includes it) and a decision on when it
  should trigger (every gap automatically, or a per-scene opt-in). Not started; see backlog.


## Done: LLM calls send a real User-Agent, fixing a Groq fallback 403 (2026-09-23)
Prompted by the Groq backup provider failing with `403 error code: 1010` the first time it actually got
exercised (Gemini's free tier was 429-busy on `make_keywords.py`). Error 1010 is Cloudflare's "banned based on
your client's signature" response -- and neither the pipeline's LLM calls (`pipeline/stages/llm_http.py`, via
httpx) nor `scripts/make_keywords.py` (via `urllib`) ever set a `User-Agent` header, so both were sending
Python's bare default (`python-httpx/...` / `Python-urllib/...`) -- a well-known bot signature some providers'
bot-protection blocks outright, unrelated to the API key or rate limit. The pipeline already does this correctly
for every source adapter (`pipeline/sources/base.py::DEFAULT_USER_AGENT`, e.g. Wikipedia asks for exactly this)
-- it just never got applied to LLM calls. Fixed both call paths:
- `llm_http.py::post_chat()` now sets a real User-Agent on every request it makes, primary and fallback alike,
  reusing the same `DEFAULT_USER_AGENT` the source adapters already use -- covers every LLM call site in the
  pipeline (keywords, relevance, script) with no per-call-site change needed. Never overrides a caller-supplied
  User-Agent if one's already set.
- `scripts/make_keywords.py` gets its own copy of the same value (it's deliberately standard-library-only, no
  pipeline package import) applied the same way.
- 2 new tests in `tests/test_llm_fallback.py` (both primary and backup requests carry a non-bot-looking
  User-Agent; a caller-supplied one isn't clobbered) and 1 in `tests/test_make_keywords.py`. Full suite: 240
  passing.
- Doesn't guarantee Groq will always answer -- a 429 from Gemini can still happen, and this only fixes a request
  that was being rejected before it even reached Groq's rate limiter. If Groq still 429s after this, that's a
  real "backup is also busy" case, not this bug.

**Follow-up, same day**: fixing the User-Agent let the very next request through to Groq for real, which surfaced
a second, unrelated problem -- `404 model_not_found` for `llama-3.3-70b-versatile`. Checked rather than guessed:
Groq moved that model (and `llama-3.1-8b-instant`) to enterprise-only access on 2026-08-16, so a free/developer
key gets a 404, not a graceful "busy, try later." `config/pipeline.toml`'s `[llm_fallback].model` (and the
matching hardcoded defaults in `pipeline/stages/registry.py::build_llm_fallback()` and
`scripts/make_keywords.py`, which only apply if the config value is ever missing) now point at
`openai/gpt-oss-120b` -- Groq's own recommended free-tier replacement for that capability tier. `openai/gpt-oss-20b`
is the faster/cheaper option if quality can flex. `docs/LOGGING.md`'s example log line updated to match; no test
hardcoded the old model name. Full suite still 240 passing after the swap.

**Second follow-up, same day**: prompted directly -- "are these sort of errors being logged?" -- checked rather
than assumed, and the honest answer for `scripts/make_keywords.py` was no. Once a job/project exists, every LLM
retry and failure is already durably logged (`logs/pipeline.log`, and a `stage_failed` decision-log entry for
anything that kills a stage). But `make_keywords.py` runs *before* any job exists, so it had nowhere to write to
-- both errors above (the Cloudflare block, then the Groq 404) existed only in a terminal's scrollback, gone the
moment the window closed.
- **New `library/keywords/make_keywords.log`**: an append-only log in the same line format
  `pipeline/core/joblog.py` already uses (timestamp, level, message, `key=val` details), so it reads like the
  rest of the project's logs. Every retry, every fallback attempt, and the final outcome -- success or
  failure, with the real status code and response-body detail, not just "it failed" -- is written here now,
  mirroring (not replacing) the existing terminal output. Gitignored (`.gitignore`): it's a local operational
  log, not something to commit, same treatment as `projects/*`.
- **Found and fixed a real bug while wiring this up**: when BOTH Gemini and the Groq fallback failed, the final
  error message reported Gemini's stale first error (e.g. "Still busy (429)"), not Groq's actual last failure
  (e.g. a 404 for an unknown model) -- `last_code`/`last_detail` were never updated after the fallback's own
  attempt failed. Now whichever provider failed LAST is what both the terminal message and the log's ERROR line
  report, with a regression test reproducing the exact scenario that was hit for real (Gemini 429 -> Groq 404).
- Also now logged: no `GEMINI_API_KEY` found, the model's reply not parsing as valid JSON, and a reply with zero
  usable keywords after cleaning -- previously terminal-only exits with no persistent trace either.
- 4 new tests in `tests/test_make_keywords.py` (the stale-error-message bug fix; a failed call writes an ERROR
  line with real detail; a successful call writes an INFO line; existing tests isolate `mk.LOG_PATH` to a temp
  file via a new `setUp`, so the test suite itself never writes junk into the real log). Full suite: 243 passing.

**Third follow-up, same day**: asked directly -- "how do I know when it'll restart" -- after a 429. Checked
rather than guessed: Groq sends a real `Retry-After` header on every 429 it returns (its docs confirm it), but
Gemini's OpenAI-compatible endpoint specifically (`v1beta/openai/...`, what this pipeline actually calls) does
**not** reliably send either a `Retry-After` header or a `retryDelay` body field -- confirmed against real
reported response bodies, not assumed. So this had to be built to use whatever a provider actually gives, and
say plainly when nothing was given, rather than inventing a countdown either way.
- **`pipeline/stages/llm_http.py::_server_retry_hint()`** (new) and its duplicate in `scripts/make_keywords.py`
  (same standard-library-only reasoning as `USER_AGENT`): reads a `Retry-After` header first, then falls back
  to a Google-style `retryDelay` nested in `error.details[]`. When either is present, THAT real number now
  drives the actual wait (capped at `MAX_SERVER_WAIT` = 120s so a huge ask can't hang a run), and the retry log
  line says so (`"server asked to wait 9s"`) instead of silently using the fixed backoff schedule. When neither
  is present -- the common case for Gemini -- the log says so explicitly (`"no wait-time hint from the
  server"`) and falls back to the existing fixed schedule, never fabricating a number.
- `post_chat()` gained an injectable `sleep` parameter (default `asyncio.sleep`), the same pattern
  `make_keywords.py`'s `call_llm()` already used, so tests can assert on the real wait value without a test
  actually blocking for it.
- The final give-up message (both scripts) now explains what a 429 with no hint could mean rather than leaving
  it a mystery: Gemini's per-minute limit is a rolling window (try again shortly), its per-day limit resets at
  midnight Pacific Time, and [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) shows
  which one actually applies to a given key -- something this pipeline has no way to see from the outside.
- 5 new tests in `tests/test_llm_fallback.py` (header-driven wait, body-field-driven wait, honest "no hint"
  reporting, capping an absurd value while still logging the real number, the backup provider's own hint on
  final failure) and 5 more in `tests/test_make_keywords.py` (the same shape, plus the final-message wording).
  One existing test in `tests/test_script_writer.py` updated for the new (more informative) log message
  wording. See docs/LOGGING.md "How do I know when it'll restart?" for the full read. Full suite: 252 passing.


## Done: Internet Archive no longer silently drops unlicensed items (2026-09-23)
Prompted by a real job (Corazon Amurao / "The Ninth Nurse") coming back from asset search with zero usable
assets. Investigated rather than assumed: the "0 found" number logged for Internet Archive on that job wasn't
Archive's actual search-hit count -- `note["found"]` (`pipeline/sources/base.py::HttpSource.fetch()`) counts
what a source's `search()` returns, and `InternetArchiveSource.search()` was `continue`-ing past (dropping)
every result that lacked a `licenseurl` field *before* fetching that item's real metadata, title, or rights
info -- so a topic like this, whose actual mid-20th-century crime-case photography mostly predates Archive's
modern `licenseurl` convention, could search-match plenty of real items and still report zero.

This is a real logic bug, not a keyword-length or query-matching problem (checked and ruled out separately --
Commons/Archive/LOC all do relevance-ranked or literal-substring search on the query as given, none of them
require an exact phrase match, so a longer keyword phrase isn't the mechanism here). The actual cause: Archive
only populates `licenseurl` for items with an explicit modern CC declaration -- unlike Library of Congress,
which writes a plain-English rights note (`rights_advisory`) on almost every record. `loc.py` already handles
its own "rights unclear" case correctly: it never drops the item, it just ships it with `license=""` and lets
`pipeline/vetting/rules.py::r_license()`'s `LIC_UNKNOWN` rule (severity high) flag it for a human to check.
Internet Archive's `require_license = true` default was bypassing that same, already-built safety net entirely
by never letting the item reach vetting (or the human reviewer) in the first place.

- **`pipeline/sources/internet_archive.py`**: `require_license` now defaults to `False`. An item with no
  `licenseurl` is still fetched (title, creator, description, the actual file) and included as a `Candidate`
  with `license=""`, exactly mirroring `loc.py`'s pattern -- vetting's existing `LIC_UNKNOWN` flag is the real
  gate now, not this source's search-side filter. Its description also gets a short note appended ("No license
  info in the Archive.org record -- check the item page before use.") so a human reviewing it in the assets
  page has the context without opening the source log. `require_license` stays a constructor parameter (and
  `archive_require_license` in `config/pipeline.toml`, also now defaulting to `false`) for anyone who wants the
  old, stricter "only explicitly-licensed items" behavior back.
- **`pipeline/stages/registry.py`**: the `archive_require_license` config default flipped from `True` to
  `False` to match, with a comment explaining why.
- **Docs**: `docs/ADD_A_SOURCE.md`'s source table and `docs/KNOWN_LIMITATIONS.md` (#13) updated to describe the
  new default and how to opt back into the old behavior.
- **Tests**: `tests/test_more_sources.py::ArchiveTests` split into two cases -- default behavior now asserts
  BOTH the licensed and unlicensed item come through (unlicensed one at `license == ""`, flagged `LIC_UNKNOWN`,
  risk `high`), and a second test confirms `require_license=True` still reproduces the old, stricter behavior
  for anyone relying on it. Caught and fixed an unrelated bug in the test fixture itself while doing this: both
  mock file downloads returned identical byte content, so the pipeline's own hash-based duplicate-file dedup
  (`HttpSource.fetch()`, correct, real behavior) was silently eating the second item in the old single-item
  test -- fixed by giving each mocked download distinct content. Full suite: 253 passing.
- **Not fixed by this change, still true**: the Corazon Amurao job may still come back thin on assets --
  Wikipedia never contributes images (text-only by design), `pexels`/`pixabay` weren't in that job's
  `--sources` list, and Commons hit its own rate limit mid-run on that attempt. This fix addresses one
  confirmed over-filtering bug in one source; it doesn't guarantee any particular topic has enough historical
  photography sitting in the free public-domain corpora these sources draw from.


## Done: retention styling + platform post copy, from MoneyPrinterTurbo's own unused features (2026-09-23)
Prompted directly -- the real goal isn't just "a video," it's "a viral reel that makes money": addicting, a
strong storyline, and nobody swipes away. That reframed what was missing, and a full architecture pass (see
the "Shorts Pipeline Architecture" artifact from this session) turned up several gaps -- caption styling, cut
pacing, background music, a cover-frame choice, and no title/caption/hashtag generation at all.

Before building anything new, checked what MoneyPrinterTurbo (vendored, already running) actually supports,
rather than assuming a gap meant new code was needed. It turned out most of this was **already built into
MoneyPrinterTurbo and simply never requested** by this pipeline's render call:
- `vendor/MoneyPrinterTurbo/app/models/schema.py::VideoParams` (the real `/api/v1/videos` request schema, not
  just a WebUI preference) already accepts `subtitle_display_mode` ("sentence" vs "word_by_word"),
  `subtitle_animation` ("none" vs "pop_spring"), font/stroke/position/background styling, `bgm_type` +
  `bgm_volume` (a built-in background-music library, or point it at your own file), and
  `video_transition_mode`. `pipeline/stages/render/mpt.py` sent none of these -- every render used
  MoneyPrinterTurbo's bare defaults: static "sentence" captions, no animation, no music.
- Still images already get automatic Ken-Burns-style motion (`render_image_zoom_video()`, a ~3%/second zoom)
  with **no configuration at all** -- the "photos with motion added" behavior `docs/KNOWN_LIMITATIONS.md` #5
  already described as unverified turns out to come for free; nothing needed building there.
- `POST /api/v1/social-metadata` (`app/services/llm.py::generate_social_metadata`) already runs its own LLM
  prompt -- "Role: Short-Video Social Media Copywriter" -- to produce a platform-sized title (a hook), a
  caption ending in a call to action, and the right hashtag count for `tiktok` / `youtube_shorts` /
  `instagram_reels` / `facebook_reels`. This pipeline's `MptClient` never called it, so nothing generated the
  CTA a content calendar's "CTA" column implies should exist.

What changed:
- **`pipeline/stages/mpt_client.py`**: new `social_metadata()` method wrapping the endpoint above.
- **`pipeline/stages/render/mpt.py`**: `MptRenderStage` now forwards every retention-styling field above when
  configured (each defaults to "" / 0 / `None` at the class level -- meaning "don't override MoneyPrinterTurbo's
  own default" -- so nothing changes unless `config/pipeline.toml` sets it), and, after a successful render,
  best-effort-generates social copy for every configured platform (a failure on one platform is logged and
  skipped, never fails the render -- it's a bonus on a finished video, not something the video depends on).
  Writes a paste-ready `SOCIAL_POST.md` (title/caption/hashtags per platform) into the project folder.
- **`pipeline/stages/base.py`**: `RenderStage.run()` now returns a new `RenderResult` (`output_path` +
  `social_metadata`) instead of a bare path string, so the generated copy actually reaches the job record --
  not just a side-effect file. `pipeline/core/orchestrator.py::_apply_render` unpacks it onto
  `job.output_path` / `job.social_metadata` (a new field on `Job`, so it's visible over the API for free via
  `job.model_dump()`), and the `render_completed` decision-log entry now also lists which platforms got copy.
- **`config/pipeline.toml` `[mpt]`**: every new field lives here with its own comment explaining what it does
  and why. Concrete defaults chosen: `clip_seconds` 5 -> 3 (MoneyPrinterTurbo's own WebUI default, and shorter
  clips mean more cuts, which matters for short-form retention); `subtitle_display_mode = "word_by_word"` +
  `subtitle_animation = "pop_spring"` (a punchier, more native-feeling caption reveal than a full sentence
  appearing at once); `bgm_type = "random"` at a low `bgm_volume = 0.12` (music under the narration, not over
  it); `social_platforms = ["instagram_reels", "tiktok", "youtube_shorts"]` on by default. Left alone on
  purpose: font/color/stroke and `video_transition_mode` -- those are closer to brand/taste than a settled
  short-form convention, and nobody here has watched a rendered example with one yet to judge it.
- **Tests**: 4 new tests in `tests/test_stages.py::RenderTests` -- unset fields never override MoneyPrinterTurbo's
  default; set fields are forwarded exactly; social metadata is generated per platform and written to
  `SOCIAL_POST.md`; one platform's call failing doesn't stop the others or the render. Full suite: 256 passing.
- **Not done here, still open**: the actual hook/narrative-structure rewrite of `ScriptWriter`'s prompt (cold
  open on the most shocking beat instead of chronological order) and the performance-feedback loop (pulling
  real watch-time/retention data back in) -- both discussed and prioritized in the same conversation, neither
  started yet. A cover-frame *choice* (vs. whatever frame the render happens to open on) also isn't addressed;
  MoneyPrinterTurbo doesn't appear to expose one via this API.


## Done: drag-and-drop scene reorder + private per-scene notes (2026-09-23)
Follow-up to the clip-matching/looping fix above. That same feedback message also raised two more things --
"we haven't added the UI to move things around option" and "Notes: what would be a good way to put it.." -- and
asked to add an AI-render option. Asked Aly to clarify all three with `AskUserQuestion`; her reply only answered
the first, clearly ("drag drop"). Rather than re-prompting over something this small, built the one clear answer
and made a best-guess, easily-changed call on the ambiguous one, while explicitly leaving the bigger, costed
decision (AI rendering) alone:
- **Drag-and-drop reordering**: each scene card in the Gate 3 editor (`pipeline/api/review_page.py`) now has a
  `☰` drag handle alongside the existing ↑/↓ buttons -- either works, and they stay in sync (same `sceneOrder`
  state, same save path). Uses the plain HTML5 drag-and-drop API, no library.
- **Private per-scene notes**: a new text box on every scene card, under the narration -- "Your note (private --
  never spoken, never sent to the renderer)". Best guess at what "Notes" meant: a place to jot why you picked
  something, what to double check, or what to come back to, that stays out of the actual video. New `Scene.note`
  field (`pipeline/core/models.py`), threaded through `Orchestrator.edit_scenes()`'s existing allowed-fields list
  (no new API route), saved the same way and at the same time as narration edits. **This is a guess, not a
  confirmed spec** -- if what Aly actually wanted was one note for the whole project rather than one per scene,
  or something else entirely, this is easy to change; flagged back to her directly rather than assumed settled.
- **Not done**: the AI-render option itself. That's a real feature needing her decision on scope and provider
  (automatic fallback vs. per-scene choice, which free-tier image API, cost/label handling) -- see backlog item E
  above, unchanged from the previous entry, still waiting on her input rather than guessed at.
- Extended the existing end-to-end orchestrator test (`tests/test_orchestrator.py::test_full_human_in_the_loop_flow`)
  to cover a scene reorder + narration edit + note edit together, rather than adding a separate test, since it's
  exercising the same `edit_scenes()` call the UI already made for reordering. No JS test framework is available
  in this environment (no npm registry access to install one); syntax-checked the embedded script with
  `node --check` and hand-validated the reorder/diff logic with a standalone Node script mirroring the pure
  functions, the same approach used for the scene/script editor below. Full suite: 237 passing.


## Done: scene/script review web page (2026-09-23)
Prompted by "we should also be able to see the script and update it if necessary" -- Gate 3 previously only worked from the
terminal (`scripts/poc.py`'s `scene_gate()`); the review page just showed a banner and did nothing once a job reached scene
review (docs/REVIEW_UI.md said so outright: "Not built yet"). This was also the last item of step 3 in the order-of-work
above ("Script editor on the same page").
- **`/review/JOB_ID` now handles Gate 3**, switching automatically when the job gets there: a live full-script view (current
  order + narration, word count, estimated seconds), one editable card per scene (narration textarea, ↑/↓ reorder, and
  a clip-swap dropdown over the approved assets when the job sources its own footage), Save changes / Approve and render /
  Ask for a rewrite. See docs/REVIEW_UI.md "Gate 3: script and scenes" for the full rundown.
- **Fixed a real staleness bug found while building this**: `job.script` is written once, when the writer stage finishes, and
  was never updated again -- so both the terminal's old `SCRIPT:` display and any script preview one might have added on this
  page would have shown the ORIGINAL draft forever, even after editing a scene's narration. Neither now trusts that field for
  display; both recompute the current script live from `job.scenes[*].narration` instead (`scripts/poc.py`'s `scene_gate()`
  updated the same way, so the terminal and the browser agree). `job.script` itself is left as-is (still useful as a record of
  the first draft) -- nothing reads it besides that one now-corrected display.
- No new API routes: both actions reuse `PATCH /jobs/<id>/scenes` and `POST /jobs/<id>/scenes/approve` / `/scenes/reject`,
  already covered by `tests/test_orchestrator.py`'s `edit_scenes` tests. The new per-scene edit-diffing and reorder logic
  (which scenes actually changed, in what order) was checked separately against a standalone set of cases (order swap,
  live-edit reflected in the script preview, no-op past the ends of the list, diff-only-what-changed) before shipping, since
  it's plain JS embedded in `pipeline/api/review_page.py` and outside the Python test suite's reach.


## Done: semantic (LLM) relevance scoring (2026-09-21)
Asset relevance defaulted to a free-tier LLM judging each asset's title/description/tags against the topic *in meaning*, batched
(~10 calls per 240 assets), with automatic fallback to keyword word-matching per asset when the LLM was off, unkeyed, or a batch
failed. Superseded the same day by the hybrid scorer below, which keeps the LLM's meaning-aware judgement but stops sending it
every asset.

## Done: hybrid TF-IDF + LLM-for-borderline relevance scoring (2026-09-21)
Prompted by hitting the free-tier LLM's rate limit and wanting more consistent scores: relevance now runs a deterministic, local
TF-IDF match (`pipeline/vetting/tfidf_relevance.py`, no network, no rate limit, same score every time) on **every** pending asset,
every round. The LLM is only asked for a second opinion on assets whose TF-IDF score is "borderline" -- within `borderline_band`
(default 0.15) of the approval threshold -- capped at `max_llm_per_round` (default 40) calls per round, closest-to-threshold first.
A 240-asset review that once made ~10 LLM calls might now make one or none. See docs/SCORING.md. Still text-only (no image
analysis) -- see KNOWN_LIMITATIONS #16/#17. Next natural step, if wanted: score each approved asset against the actual scene
narration once the script exists (a second pass at clip-matching time), for true per-scene story relevance rather than topic
relevance. Also raised but not started: switching the LLM tier itself to Groq (more generous free-tier limits, same
OpenAI-compatible config swap) for the cases that still do call an LLM -- worth less now that far fewer calls happen at all.

## Done: stage timing / provenance (2026-09-22)
Prompted by a job failing silently from Aly's point of view (no notification -- see backlog item A below -- and the
only record of "how long did it run before it failed" was an ephemeral activity-log line). Every stage's start,
finish and duration -- and a failed stage's duration too -- is now written into the tamper-evident decision log
(`stage_started`/`stage_finished`/`stage_failed`, docs/LOGGING.md "Timing / provenance"), not just logged as text.
`Orchestrator.timing_summary()` / `GET /jobs/<id>/timing` rolls this up at any point in a job's life: total wall
time, time per stage (summed across repeated rounds, e.g. "search again"), and time spent waiting on a human at
each gate -- written to the decision log one final time as a `job_summary` entry when the job finishes or fails.
`scripts/poc.py` prints a short version automatically. This is the "Time" and part of the "Per-project summary"
bullets from the backlog item below; tokens/cost tracking and the Slack/notification pieces are still not started.

## Done: token / $ cost provenance (2026-09-22)
Prompted by "are we keeping track of how many tokens, how many api calls, how much cost" -- the answer at the
time was no: every LLM call (keywords, script, relevance) discarded the `usage` block in Gemini's reply, and only
non-LLM source API calls were logged (`sources/<source>/requests.jsonl`, unchanged). Now every LLM call, wherever
it's made, is recorded (`pipeline/core/usage.py`, wired into the one shared `post_chat()` every call already goes
through) as its own `llm_call` entry in the same tamper-evident decision log timing uses -- tokens in/out and an
estimated $ cost from a price table in `config/pipeline.toml` (`$0` for the free-tier models this pipeline ships
with, a real estimate the moment you price a paid one). `Orchestrator.usage_summary()` / `GET /jobs/<id>/usage`
rolls this up by model, with the model's free-tier request quota alongside it for reference; written to the
decision log one final time as a `usage_summary` entry when the job finishes or fails, same pattern as
`job_summary` for timing. `scripts/poc.py` prints a short version automatically. See docs/LOGGING.md "Tokens /
cost". `scripts/make_keywords.py` runs before any job exists, so it has no decision log to write into -- it
prints its one call's tokens to the terminal instead. This finishes the "Tokens" and "Cost" bullets from the
backlog item below; "Is it done? What's next?" (a `/status` page) and the Slack piece (item A) are still open.

## Done: LLM fallback provider + keywords-file provenance (2026-09-22)
Prompted by "if gemini doesn't work, can we use grok" and "is [the keywords script] logged -- which api, which
model, tokens" -- checked first rather than assumed: no fallback existed (each provider only retried itself),
and neither `GROQ_API_KEY` nor an xAI/Grok key was in `.env` (xAI's Grok API is billed per token with no free
tier; Groq, a different company, has a genuine free developer tier and was already an idea in this file). Added
both, scoped to free-tier-only per standing project rules:
- **Backup provider**: `[llm_fallback]` in `config/pipeline.toml`, defaulting to Groq. Every LLM call in the
  pipeline (keywords, relevance, script) is retried once against it after the primary is still failing after
  its own retries (`pipeline/stages/llm_http.py::post_chat()`). Off automatically until `GROQ_API_KEY` is set.
  See docs/LOGGING.md "LLM fallback provider" for exactly what gets logged (which provider actually answered,
  never silently attributed to the wrong one).
- **Keywords-file provenance**: `scripts/make_keywords.py` (which runs before any job exists) now writes a
  `.meta.json` sidecar next to every keywords file -- provider, model, tokens, when. `poc.py` reads it when you
  use `--keywords-file`, and `ManualKeywordStage` carries it into that job's `proposed_keywords` decision-log
  entry, so "which API, which model, tokens, where these came from" is answered from the job's own permanent
  record, not a terminal scrollback from weeks earlier. See docs/LOGGING.md "Where a keywords file came from".

## Done: relevance-scoring method + algorithm version tracking (2026-09-22)
Prompted by "are we logging how we scored certain images? which sort of method, and each time we change method we
should be keeping track... nothing should be deleted" -- checked first rather than assumed, and the answer was a
real gap: which tier (tfidf/llm-semantic/keyword-match) scored an asset lived only on the live asset and an
ephemeral activity-log count, never in the permanent `vetted_asset` decision-log entry; and no scoring algorithm
had a version identifier at all, so this session's two real TF-IDF rewrites (fixing a pooled-query dilution bug,
then a cosine-similarity length-penalty bug) left no trace of which formula produced a given score. Fixed, purely
additively -- nothing already written was touched, per the constraint:
- Each of the three scorers now has its own version identifier (`KEYWORD_MATCH_VERSION` in
  `pipeline/vetting/rules.py`, `VERSION` in `tfidf_relevance.py` and in `llm_relevance.py`), recorded as
  `Vetting.relevance_method` (e.g. `tfidf-v3`, not just `tfidf`) and now written into the permanent
  `vetted_asset` decision-log entry (`logic.relevance_method`) and the per-round `relevance_scoring` entry
  (`logic.methods_used_this_round` / `logic.formulas_by_method`, replacing a single static formula string that
  had already gone stale).
- **`docs/SCORING_CHANGELOG.md`** (new): an append-only ledger of every version any scorer has ever shipped --
  what it did, what changed from the version before it, and why. Reconstructed `tfidf-v1` and `tfidf-v2`
  (both superseded, predating version tracking) from git history/code review alongside the current `tfidf-v3`,
  so today's two undocumented rewrites are no longer invisible.
- `tests/test_relevance_versioning.py` guards the wiring itself: it fails immediately if a future version bump
  updates a `VERSION` constant without updating the matching default elsewhere or its changelog-adjacent formula
  lookup, rather than the two silently drifting apart the way `method`/`SCORING_FORMULA` did before this.
- See docs/LOGGING.md "Relevance-scoring method and version" and docs/SCORING.md "Tracking changes to the scoring
  algorithm" for the full read on where this is recorded and how to compare scoring versions across runs.

  **Correction (2026-09-22, see the next section below):** the `tfidf-v1`/`tfidf-v2` identifiers this entry
  describes turned out to be a mistake -- no real log entry from before version tracking existed can actually be
  attributed to either one specifically, so presenting them as addressable versions was inventing precision that
  didn't exist. They've been replaced with an honest "pre-tracking history" note, and the current TF-IDF code is
  now `tfidf-v1` (the first version ever actually tracked). `Vetting.relevance_method` was also split into two
  separate fields, `scoring_method` and `method_version`. See the next entry for the full fix.

## Done: relevance-scoring provenance, method versioning fixed, and an evaluation workflow (2026-09-22)
Follow-up to the entry above, prompted by two things: (1) "will what we have now be able to tell us how well a
model is doing?" -- answer at the time: not really, `RELEVANCE_LABELS.jsonl` didn't record which method/version
scored a labeled asset, and no report existed; and (2) a direct correction that the previous entry's
`tfidf-v1`/`tfidf-v2` were invented, unverifiable version identifiers, not real ones any log could confirm.
- **Schema**: `Vetting.relevance_method` (one combined string) replaced with separate `scoring_method` and
  `method_version` fields, plus `scoring_fallback_note`, `relevance_threshold`, `relevance_decision`,
  `contribution_note`, and `contributions` (every method that scored an asset that round, not just the winner).
  See docs/SCORING.md "Multiple contributions".
- **`pipeline/vetting/method_registry.py`** (new): single source of truth for what each method+version does
  (description, formula/prompt, parameters, model note, when/why it changed) -- feeds the decision log's
  formula lookup AND a new `GET /methods` endpoint the review page fetches so a score can be explained inline,
  with a link to the method's full definition.
- **`docs/SCORING_CHANGELOG.md` rewritten**: the current TF-IDF implementation is now `tfidf-v1`, the FIRST
  version this file's math was ever given a tracked identifier for. The two earlier, real rewrites are described
  as prose ("pre-tracking history"), explicitly not versioned -- no `tfidf-v0`/`-v2` invented, no historical
  score attributed to one of them.
- **Human labels extended**: the three labels are now `use` / `duplicate` / `irrelevant` (was a 2-button
  Use/Irrelevant), with an optional structured `reason`, free-text `note`, and `duplicate_of_asset_id`.
  `RELEVANCE_LABELS.jsonl` is now strictly APPEND-ONLY (one row per label event, never rewritten -- "current"
  means the latest row per asset by timestamp) and snapshots the method/version/threshold/decision AS THEY WERE
  at scoring time, never recalculated later. A new `label_asset()` / `POST /assets/label` lets you label an
  asset in ANY job state, not just during the assets-review gate -- required for sampling/reviewing older jobs.
- **`scripts/evaluate_relevance.py`** (new): reads `RELEVANCE_LABELS.jsonl` across projects, groups by
  scoring method+version (with a separate unversioned/missing-provenance bucket for older data), and reports Use
  yield, Irrelevant/Duplicate selection rate, Missed Use items, relevance agreement/false-positive/false-negative
  rates (only where the machine's prediction and the human label are actually comparable), and duplicate-flag
  precision/recall -- every percentage with its sample size and a sparse-data warning. See docs/EVALUATION.md.
- **`scripts/sample_for_review.py`** (new): picks random + optional borderline assets from BOTH the
  machine-selected and machine-rejected/hidden pools for you to go label, tagging how each item was picked
  (`SAMPLE_TAGS.jsonl`, append-only) so a report never mistakes a biased sample for a representative one. Also
  a holdout discipline (`mark-holdout` / `HOLDOUT.json` per project) so a stable set can be kept aside and never
  tuned against, for an honest before/after comparison of a scoring change. See docs/EVALUATION.md.
- 40 new tests (`tests/test_evaluation_*.py`) cover the report math, the sampling logic, and an end-to-end path
  through the real `Orchestrator.label_asset()` into both CLI scripts. Full suite: 231 passing.

## Ideas backlog (added 2026-09-20, not started)

### A. Notifications (Slack, readable on a phone)
Goal: know what the pipeline is doing without watching a terminal.
- **Errors and warnings first**: a message when a stage fails (job, stage, the error line, link to the log) and on WARN lines that need attention
  (source skipped, model busy after all retries, script well under target length).
- **Status**: job started, each stage finished, video done (with time taken and where the file is), what happens next.
- **Pending on you**: a message whenever the job reaches a human gate (keywords, assets, scenes) with the count waiting and a link to
  `/review/<id>`; a **reminder** if a gate has been waiting longer than N hours (default idea: 2h, then daily) so it shows up on the phone.
- **Daily digest** (optional): one message listing every job and its state, what's pending, what failed.
- How it fits: every state change and warning already goes through one place (`pipeline/core/joblog.py` and `orchestrator._commit`), so
  notifications can be a listener on those, not new logic in each stage. Needs a `[notify]` section in `config/pipeline.toml`, the webhook URL in
  `.env` (never in git), and a per-level filter (e.g. only WARN/ERROR + gates + done).
- Free options: a Slack incoming webhook (free; the Slack phone app gives push). Alternative with no account: ntfy.sh push. Reminders need a small
  background loop in the pipeline container that checks for jobs stuck at a gate.
- The review link only opens from the Mac unless the port is exposed safely (Tailscale or similar); decide before promising phone links that open.

### B. Usage, cost and time tracking
- **Tokens**: done, see "Done: token / $ cost provenance" above -- every LLM call is an `llm_call` decision-log entry (calls, tokens in and
  out, model, cost) via `pipeline/core/usage.py`, `GET /jobs/<id>/usage`.
- **Cost**: done, same feature -- a price table in `config/pipeline.toml` (`[usage.prices.<model>]`) so a paid model shows a real $ estimate;
  free-tier quota (`[usage.free_quota.<model>]`) shown alongside for reference, though not enforced.
- **Time**: done, see "Done: stage timing / provenance" above (`docs/LOGGING.md`, `GET /jobs/<id>/timing`).
- **Per-project summary**: timing and usage are both done (`job_summary` / `usage_summary` decision-log entries); still to add: sources
  pulled, assets found/approved, render length -- and surfacing it in the Slack "done" message once notifications (item A) exist.
- **Is it done? What's next? Anything pending?**: a `/status` page and Slack summary per job: state, what it is waiting for and who, what runs next.

### C. Several stories at the same time
- Where we are: the orchestrator already keeps a lock per job and runs each in its own async task, so two jobs can run at once; the review page lists
  all projects. The terminal script (`poc.py`) follows one story per terminal window.
- Limits to design around: MoneyPrinterTurbo renders in one container (renders will likely queue or slow each other; may need a render queue with
  N at a time); the free Gemini tier's rate limit is shared by all jobs (a shared limiter/queue so two stories don't trip 429s); disk and download
  bandwidth; the terminal prompts (a `/review` queue page showing every job waiting on you is the natural way to handle many).
- Steps: (1) a jobs dashboard (state, waiting-on, age) in the browser, (2) a start-a-story form in the browser so terminals aren't needed, (3) a global
  cap on concurrent renders and LLM calls, (4) test two jobs in parallel end to end.
- A batch mode is a natural extension of the keyword files: a list of subjects in, one job each, all waiting at Gate 1 in the dashboard.

### D. Searchable logs in Postgres, with a UI (added 2026-09-22, not started)
Goal: "show me every job where Groq was used as a fallback", "every HIGH-risk asset I approved last month",
"every run that took over 5 minutes" -- answerable by typing into a search box, across every project's history,
instead of grepping `decisions.jsonl` files one project folder at a time.

- **Files stay the source of truth.** `decisions.jsonl` is hash-chained specifically so nothing can be edited
  after the fact (`pipeline/core/decisions.py::verify()`) -- that property only means something if the file is
  still the thing anyone would trust in a dispute. Postgres is a **search index built from the files**, never
  the only copy of anything: it can be dropped and rebuilt from `projects/*/decisions.jsonl` (and the activity
  and request logs) at any time with no data loss, the same relationship a search engine has to the documents
  it indexes.
- **New docker-compose service**: `postgres:16-alpine` (or newer LTS at build time) with a named volume so data
  survives `docker compose down`/`up`. New `[postgres]` section in `config/pipeline.toml` (host/port/db/user)
  and `POSTGRES_PASSWORD` in `.env`. Not a hard dependency of the pipeline itself -- if Postgres is down,
  jobs still run and the files are still written; only search/the logs UI degrade, same "never a hard
  requirement" pattern the LLM/relevance features already follow.
- **Schema** (sketch, refine when building): one row per decision-log entry (`job_id`, `seq`, `at`, `stage`,
  `action`, `actor_type`, `actor_name`, `actor_model`, `decision`, `reason`, and the free-form `subject`/
  `logic`/`inputs`/`outputs` as `jsonb` columns so nothing has to be flattened ahead of time), indexed on
  `job_id`, `at`, `stage`, `action`, `actor_type`, plus a `tsvector` generated column over `reason` + the
  jsonb text for free-text search. Separate tables for activity-log lines and source request logs (both in
  scope per the answer to this question), each keyed by `job_id` the same way, so a search can join across
  "this job's decisions AND its activity log AND what it actually requested."
- **Ingestion**: best-effort dual-write -- right after `DecisionLog.record()` (and the activity/request log
  writers) append to their file, also upsert the same row into Postgres, wrapped so a DB hiccup can never fail
  a job (exactly how `core/usage.py` and `core/joblog.py` already treat their own writes as "must never break a
  run"). Plus a standalone `scripts/reindex_logs.py` that walks every `projects/*/` folder and rebuilds the
  whole index from scratch -- the recovery path after schema changes, after Postgres was down for a while, or
  just to sanity-check the index matches the files.
- **UI**: a new `/logs` page in the pipeline's existing web app (same self-contained-HTML pattern as
  `pipeline/api/review_page.py`), backed by a new `GET /logs/search` API route -- filters for job/project,
  stage, actor type, action, date range, risk, provider/model, free-text search over reason/logic, each result
  linking back to that job's `/review/<id>` or `/jobs/<id>/decisions?format=md`. Not a replacement for the
  per-project `DECISIONS.md`/asset-review page, which stay -- this is the "search across everything" view they
  don't offer.
- **Open questions to settle when building**: whether DEBUG-level activity-log lines are worth indexing at all
  (high volume, rarely searched -- maybe INFO and up only, same default the activity log already uses); how far
  back "everything" reaches (all projects on disk, presumably, since there's no retention/archival policy yet);
  whether the `/logs` page needs its own access control, given the pipeline API currently has none and is meant
  for local/trusted-network use only.
- Rough build order: (1) the Postgres service + schema + `reindex_logs.py` (get existing history searchable
  first, no live-write risk yet), (2) dual-write hooks so new jobs stay indexed as they run, (3) the
  `GET /logs/search` API, (4) the `/logs` UI page.

### E. AI-generated image/clip fallback (added 2026-09-23, not started)
Goal: "we should also have an option to ask them to render AI" -- a scene with no good matching approved
photo gets an AI-generated image instead of either nothing or a reused unrelated photo (see "Done: clip
matching quality" above for why that gap exists at all).
- **Where it plugs in**: `ClipSource` (`pipeline/stages/scenes/clips.py`) is already the extension point --
  its own docstring says so ("Implement `ClipSource` to add scrapers, Pexels/Pixabay, or AI video
  generation"). A new `AiImageClipSource` (or a wrapper that falls back to one) would slot in next to
  `AssetClipSource`/`LocalFolderClipSource` the same way every other provider does (`pipeline/stages/
  registry.py`), no orchestrator changes needed.
- **Open questions to settle before building** (asked Aly directly -- 2026-09-23):
  - Automatic fallback only (fires just for a scene with no good approved match) vs. a per-scene choice to
    use AI art even when a real photo is available.
  - Provider: needs a free-tier (or already-configured) image API -- Pollinations.ai (keyless, genuinely
    free) is the most likely fit given this project's "free-tier only" rule elsewhere (`llm_fallback`,
    `keywords`, `relevance` all default to free tiers); Gemini's own image generation is worth checking if
    the configured account's tier includes it, since a key is already set up.
  - Whether an AI-generated image needs its own risk/label handling (it's not a real photo, so "license",
    "author", DUPLICATE checks etc. in `pipeline/vetting/rules.py` don't cleanly apply) and whether it shows
    up differently in the review page / CREDITS.md (probably: no license line needed, but should say clearly
    "AI-generated" wherever it's shown, including in the rendered video's description if that matters legally
    or editorially).
  - Cost: image generation is usually metered even on a "free tier" (rate-limited, not unlimited) -- worth a
    usage/cost entry the same way LLM calls already get one (`pipeline/core/usage.py`), so it doesn't become
    an invisible cost the way tokens used to be before that was built.

### F. Story-aware shot planning: let an LLM decide what each scene needs (added 2026-09-23, not started)
Prompted directly -- "what would make a good story, good video, what different frames would be good with the
script, maybe have an LLM plan this and help find photos and videos" -- after the Internet Archive fix above
turned out to only patch one over-filtering bug, not the deeper reason a job can come back thin: **nothing in
this pipeline currently reasons about the script when deciding what to search for or which asset goes where.**
Checked in the actual code, not assumed:
- `scripts/make_keywords.py` runs *before the job even exists*, so the keyword list is generated from the raw
  topic string alone, with no view of what the eventual script will actually say scene by scene.
- Sourcing then searches those topic-level keywords and a human approves a pool of assets -- still before any
  script exists (`JobState` order is `KEYWORDS_RUNNING -> SOURCING_RUNNING -> VETTING_RUNNING -> SCENES_RUNNING`,
  `pipeline/core/orchestrator.py`).
- Once the script is finally written and split into scenes, each scene's `search_terms` is just
  `[keywords[i % len(keywords)]]` -- round-robin cycling through the same pre-script keyword list, blind to
  what that specific paragraph is actually about (`pipeline/stages/scenes/mpt.py` line ~97).
- The clip for each scene is then picked by `AssetClipSource.fetch()` (`pipeline/stages/scenes/clips.py`) via
  plain bag-of-words overlap between the scene's narration/search terms and each approved asset's title/
  description/query -- no semantics, no sense of "this paragraph is about the trial, that photo is a hospital
  exterior." Gate 3 (`edit_scenes()` in `orchestrator.py`) lets a human manually reassign a scene's clip, but
  only to an asset that was *already approved before the script existed* -- there's currently no way to go back
  to sourcing for one specific scene once the script is written.
- `ScriptWriter`'s own prompt (`pipeline/stages/scenes/writer.py`) already half-anticipates this gap -- it
  explicitly tells the model to keep every paragraph anchored to "one concrete, picturable subject" *because* a
  clip gets matched to it afterward by shared words -- but nothing downstream actually uses that structure; it
  still just gets bag-of-words matched against a pool that was never asked to cover it.

**Proposed shape** (a new pass, not a rewrite of what already works): after the script is written and split
into scenes (still inside `SCENES_RUNNING`, before scenes go to human review), add an LLM "shot list" step that
reads the finished narration and, per scene, produces (a) 2-4 specific search queries for what that paragraph
needs visually (e.g. "Corazon Amurao nursing school photo," "1966 Chicago townhouse dormitory exterior," "Cook
County courthouse 1966" -- not the flat topic-level keywords used today), and (b) a fallback query one level
more generic for the same scene ("1960s Chicago nursing student," "vintage American courthouse exterior") to
use only if the specific one comes up empty. This is the decision layer the other three ideas plug into rather
than being four separate point fixes:
- **Broader source coverage** and a **newspaper-archive source** become more search targets this planner can
  route a given scene's specific query to (case-specific -> newspaper/Openverse/Commons; generic B-roll ->
  Pexels/Pixabay).
- The **tiered fallback strategy** from the ideas discussion above (broader retry, generic era B-roll, text/
  quote cards) becomes what happens, per scene, when even the shot planner's generic query comes up empty --
  the planner is what decides a scene has exhausted real options and should fall through, rather than that
  being a blind, pipeline-wide retry.
- **AI-generated image fallback (item E)** becomes the shot planner's last resort for a specific scene, with
  its own prompt informed by what that scene's narration actually says, instead of a generic per-topic trigger.
- **Open questions to settle before building:**
  - **Stage ordering is the real design decision.** Today assets are approved by a human *before* the script
    exists (deliberate: nothing gets sourced or shown to a reviewer without going through vetting first). A
    shot planner needs the finished script, so either (a) it runs as a *second*, scene-targeted sourcing round
    after SCENES_RUNNING, adding a new mini review step for just the new candidates it pulls in (extends the
    existing "search again" mechanic already used at asset review, reusing `prior_searches` paging in
    `pipeline/sources/base.py::HttpSource.fetch()`), or (b) script writing moves earlier in the job so shot
    planning can inform the *first* and only sourcing round. (a) is the smaller, safer change and keeps every
    existing gate intact; (b) is a bigger reorder with real benefits (one sourcing pass, not two) but touches
    the orchestrator's state machine and every stage that currently assumes assets exist before scenes do.
  - Cost/latency: one more LLM call per job (or one per scene, if done per-scene rather than batched) on top of
    keywords, script, and relevance scoring -- worth batching all scenes into a single call the way relevance
    scoring already batches assets (`pipeline/vetting/relevance.py`, "LLM scored N of N in 1 batch(es)").
  - How much this should also replace vs. supplement `AssetClipSource`'s bag-of-words matching -- an LLM
    judging "which of these 15 approved assets best fits this paragraph" is a more direct fix for mismatched
    photos than better search alone, and doesn't require any stage-ordering change, so it may be worth building
    first/independently as a smaller step.
  - Whether the newspaper-archive question (does LOC's Chronicling America actually cover papers into the
    1960s for a given title, or is it mostly pre-1960s copyright-cleared material) gets answered before or
    after this, since it determines whether "route case-specific scenes to the newspaper archive" is even a
    real option for a case this recent.
- **Smallest useful first slice, if we want a quick win before the bigger stage-ordering decision**: LLM-based
  scene-to-asset matching only (replace/augment `AssetClipSource`'s word-overlap scoring with an LLM judging
  the existing approved pool against each scene's narration) -- no new sourcing round, no stage reorder, and it
  directly targets "the photos don't fit the script" without deciding the bigger architecture question yet.

## In progress: major overhaul for authentic case material vs. historical/stock/reconstruction (2026-09-23 --)

Working through Aly's 15-section requirements doc (goal: "find more relevant photos and footage, especially
authentic material connected to a specific true-crime case, while clearly distinguishing it from historical
context, generic stock, and reconstructions"). Repository assessment first (verified, not assumed, what the
prior code actually did), then staged implementation, without promising every case will have accessible,
reusable photos or footage. Stage 1 (data model + sourcing foundations) done so far:

- **Data model** (`pipeline/core/models.py`): `Asset` gained `category` (verified_case/unverified_case_candidate/
  historical_context/illustrative_stock/reconstruction, never auto-promoted to verified_case -- always a human
  decision), a full identity-evidence block (`identity_status`, `depicts`, `case_connection`, `media_date` vs.
  `event_date`, ...) kept fully independent of a separate rights-evidence block (`rights_status`, distinguishing
  public_domain/cc0/open_license/paid_license/unresolved -- never "no known restrictions" treated as a legal
  conclusion) and of `status` (your use/reject decision) -- identity, relevance, rights and selection stay 4
  independent axes, per the requirements doc. `Keyword`/`TextRef` gained a `QueryGroup` (research/case/
  historical/stock, docs/SEARCH_PLANNING.md) plus entity/aliases/dates/locations/essential for a structured
  search plan.
- **Source error taxonomy** (`pipeline/sources/base.py`, docs/SOURCE_ERRORS.md): `CredentialsMissing`/
  `RateLimited` (respects a provider's `Retry-After`)/`ProviderError`/`ResponseParseError` replace one
  undifferentiated `SourceUnavailable` bucket, each source adapter updated to raise the specific one, and every
  per-query/per-source trace note now carries an `outcome` (ok_kept/ok_zero_matches/ok_no_downloadable/
  parse_error/credentials_missing/rate_limited/network_error/skipped_other/unknown_error) -- a technical failure
  is never shown as "searched, found nothing."
- **Wikipedia reporting fixed** (`pipeline/sources/wikipedia.py`): saved-article character cap is now
  configurable (`wikipedia_max_chars`, was hardcoded), a filename-collision bug that could silently overwrite
  one article's saved file with another's is fixed, and every trace note/log line/SOURCES.md row is tagged
  `kind: "text"` vs. `"media"` so "kept: 1" can't be misread as a photo when it's an article (or vice versa).
- **Query-group routing** (`pipeline/sources/groups.py`, `pipeline/stages/sourcing.py`,
  `pipeline/stages/keywords/llm.py`, docs/SEARCH_PLANNING.md): the LLM keyword prompt now asks for a group per
  term with the exact required examples ("Corazon Amurao interview" / "Chicago residential streets 1960s" /
  "empty hospital hallway" -- explicitly as search examples, never a claim material exists), and sourcing routes
  each term only to the sources its group pools to (case/historical -> archives except Wikipedia; research ->
  Wikipedia only; stock -> generic stock sites), stamping each new asset's `category` from the group that found
  it. A manual-keyword job (no group diversity) keeps broadcasting every term to every source exactly as before
  -- routing only activates once the LLM's structured plan actually populates groups, so nothing regresses for
  existing jobs/workflows.
- **Case reference sheet & visual checklist** (`Job.case_reference`/`Job.visual_checklist`, docs/CASE_REFERENCE.md):
  a canonical name, atomic facts (person/alias/date/location/address/institution/event, each `confirmed`/
  `unresolved`/`conflicting` with its own source links) and a generated-but-always-human-moved visual checklist,
  entirely separate from anything sourcing/vetting/scoring writes -- the one place in a job that only ever
  contains what a human actually entered or confirmed. Editable at any job state via 8 orchestrator methods/API
  routes; nothing cross-checks a search term against it yet (see "What this doesn't do yet" in that doc).
- **Deferred from Stage 1** (see docs/SEARCH_PLANNING.md's last section and docs/KNOWN_LIMITATIONS.md):
  per-provider query syntax/operators beyond a plain-text phrase; automatic query broadening/alternatives when a
  `case` search comes up empty; cross-checking a search term against the case reference sheet's facts;
  NARA/Prelinger/NYPL/Europeana-rights-specific historical media work (Stage 3); query-sequence refinement and
  query-construction transparency in the UI (Stage 4).

Stage 2 (Gate 2 UI, manual-URL-import parity, local-clip inclusion, pre-render coverage check) is in progress:

- **Gate 2 "Add links" fixed (#9)** (`pipeline/core/orchestrator.py::add_reviewable_asset`, `pipeline/api/app.py::
  assets_add_url`, `POST /jobs/{id}/assets/add-url`): the old behaviour queued pasted URLs into the job's URL list
  and downloaded them on the *next* sourcing round, with no per-link feedback at all -- a typo or a dead link
  just silently produced nothing, or a plain webpage link failed with a raw yt-dlp stderr tail. Each pasted line
  now downloads synchronously, one at a time (same `UrlListSource.fetch_one` Gate 3's "drop a link on a scene"
  already used), and reports its own success or a specific, readable failure -- "that link is already in this
  project" for a duplicate, or a plain-language "this doesn't look like a direct video/photo link" hint alongside
  yt-dlp's own error when the link is evidently a webpage rather than media (`pipeline/sources/urls.py`'s
  `NOT_MEDIA_HINTS` -- the hint is added next to yt-dlp's own text, never in place of it, since the heuristic can
  be wrong). Every added asset lands `pending`, exactly like a searched one -- Gate 2 is itself the review step,
  so nothing added this way is ever auto-approved, unlike Gate 3's drop-onto-a-scene (`add_scene_asset`), which
  auto-approves a low-risk clip because it's already past review. No login/paywall is bypassed either way; a link
  behind one just fails the same way it would for yt-dlp on its own. The old `extra_urls_text`/`POST /assets/
  reject` bulk-queue mechanism still exists (for scripted/CLI use), it's just no longer what the review page's
  "Add links" box calls.
- **Provenance now actually recorded (#9/#10, partial)**: `Asset.import_method` (`search`/`manual_url`/
  `local_folder`/`scene_upload`) existed on the model since Stage 1 but nothing ever set it to anything but the
  default `"search"` -- a folder-imported or dragged-in asset was indistinguishable from a keyword-search result
  in its own metadata. `pipeline/sources/urls.py`, `pipeline/sources/upload.py` and `pipeline/sources/folder.py`
  now stamp the correct value. `FolderSource` still imports the *entire* `library/scraped/` folder into any job
  listing `folder` as a source with no per-job filtering, and `owner_submitted`/`owner_note` are still never set
  by anything -- that per-job-inclusion piece of #10 is not done yet (next up).
- **A real API bug fixed along the way**: an `HTTPException` raised directly in a route handler (as several
  Gate 2/3 handlers do for a 400/409/422) came back as a **plain-text** body, not the `{"error": ...}` JSON shape
  the review page's `api()` helper and every other endpoint assume -- so a specific failure message like the new
  "doesn't look like a direct video/photo link" hint would silently never have reached the page; the frontend's
  `fetch` can't parse plain text as JSON and falls back to the generic HTTP status text instead. Fixed with one
  `exception_handlers` entry in `pipeline/api/app.py::create_app`, which fixes it for every existing handler that
  raises `HTTPException` directly, not just the new one.
- **Explicit per-job local-clip inclusion (#10)** (`pipeline/sources/folder.py`, `Orchestrator.reject_assets`'s
  `folder_files` param, `GET /jobs/{id}/folder-files`, review page's "Add your own footage" panel): `FolderSource`
  no longer imports `library/scraped/`'s entire contents into every job that lists `folder` as a source -- it
  imports nothing until the job's `providers.options["folder_files"]` names specific relative paths, picked
  after browsing what's actually there (`list_available()`). Queued the same way a pasted URL list is (merged
  in, `folder` added to `providers.sources` if needed, pulled on the next sourcing round) rather than
  synchronously like #9's links -- these are already-local files with no download/network failure mode, so
  there's nothing that needs per-file success/failure feedback the way a URL fetch does. Every file picked this
  way is `owner_submitted=True` with your note as `owner_note`: picking it for a job IS the explicit "this is
  mine" action, never inferred.
- **Identity/rights/category write path (#7/#8, groundwork for #13)** (`Orchestrator.set_asset_identity`/
  `set_asset_rights`/`set_asset_category`, `POST /jobs/{id}/assets/{asset_id}/identity|rights|category`): Stage 1
  built the `category`/`identity_status`/`rights_status` fields onto `Asset` but never built anything that could
  actually set them past their defaults -- there was no way for a human to record "this is Jane Doe, per the
  caption" or "checked, this is CC0" anywhere, which made the whole identity/rights half of the data model dead
  weight. Each setter works the same way `label_asset` already does: at any job state, not just while sitting at
  Gate 2, since a person may want to record who's pictured or where rights stand well after the asset was
  approved or rejected; requires a named reviewer, stamps `*_reviewer`/`*_reviewed_at`; validates its status
  against the model's literal values; and the three axes are fully independent saves -- setting one never touches
  another. Logged to the decision log with before/after like every other mutation.
- **Gate 2 UI surfacing (#13)** (`pipeline/api/review_page.py`): every card now shows category/identity/rights as
  badges next to the existing score/risk ones ("not categorized" until someone sets one; identity/rights badges
  colored only when they land somewhere unambiguous -- verified identity or a clear open rights status green,
  disputed identity red, unresolved rights amber), and a "Case connection & rights" panel (read-only summary --
  who set what, when, with what evidence/notes -- plus three inline save forms) that's independent of the
  existing "Why this score and risk" panel and never auto-fills from it. `GET /jobs/{id}/asset-report`
  (`Orchestrator.asset_report`) feeds a collapsible "Sources & rights so far" panel above the grid: per-source
  photo/video/research counts, category/identity/rights breakdowns across the whole pool, and the labeling cost
  run up so far for the job (reusing the existing `usage_summary` rollup, not a second cost calculation) --
  collapsed and lazily loaded so it costs nothing when not opened. Backend: 17 new tests across
  `tests/test_asset_identity_rights.py` and `tests/test_api_sources.py`, full suite at 417, all passing.
- **Pre-render visual coverage check (#12)** (`Orchestrator._visual_coverage`/`check_visual_coverage`,
  `GET /jobs/{id}/visual-coverage`, `POST /jobs/{id}/scenes/approve` `override_note`, Gate 3's "Visual
  coverage" panel in `pipeline/api/review_page.py`): `approve_scenes` now refuses to move a job to
  `RENDERING` while any `visual_checklist` item is still sitting at `needed`/`candidates_found` -- the two
  statuses that mean nobody's actually decided what happens there yet -- unless the caller also gives a
  written `override_note`, which gets logged alongside exactly which items were left unresolved. Getting
  an item to one of the three resolved statuses (`fulfilled`/`not_available`/`skipped`) was already possible
  through `update_checklist_item`, but nothing required a reason for `not_available`/`skipped` or an asset
  for `fulfilled` until now -- that gap is closed too, so none of the three can happen silently either.
  `check_visual_coverage` is read-only and callable at any job state (has_checklist=false for a job that
  never used the checklist at all means nothing here gates it -- opt-in, not a new requirement forced onto
  every job), and cross-references each unresolved item against scenes that share its linked keyword term
  via `Scene.search_terms`, so the report says which scene a gap actually affects. The report also carries
  a fixed 5-option remediation menu (fulfill with a specific asset / search or add more material at Gate 2
  or 3 / mark not available with a reason / mark skipped with a reason / approve anyway with a written
  override), surfaced in Gate 3's new "Visual coverage" panel with inline mark-fulfilled/not-available/
  skipped actions and an override-note box feeding "Approve and render". Coverage also catches one step
  further than "unresolved": a `case`-group item already marked `fulfilled` whose asset isn't actually
  categorized `verified_case`/`unverified_case_candidate` is flagged as a `category_mismatch` (not blocked
  outright -- a human may deliberately want a historical stand-in -- but never silent, and it still counts
  toward `ready`/gates `approve_scenes` the same as an unresolved item) -- otherwise "fulfilled" could point
  at any approved asset with nothing checking it was actually case material, which was exactly the gap #12
  exists to close. 23 new tests across `tests/test_visual_coverage.py`, `tests/test_case_reference.py` and
  `tests/test_api_sources.py`, full suite at 440, all passing.
