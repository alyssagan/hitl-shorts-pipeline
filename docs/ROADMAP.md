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
