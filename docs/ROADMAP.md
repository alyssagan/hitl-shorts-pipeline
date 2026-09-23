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

> Update: the asset review web page (thumbnails, scores, Use/Reject, search again) is built. See docs/REVIEW_UI.md. Scene/script page still to do.


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
