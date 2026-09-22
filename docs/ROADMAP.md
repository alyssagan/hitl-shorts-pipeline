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
- **Tokens**: Gemini's OpenAI-compatible replies include a `usage` block (prompt/completion/total tokens). Record it per call (keyword model,
  script writer, make_keywords) into the project's log and a `USAGE.md`/`usage.json`: calls, tokens in and out, model, seconds.
- **Cost**: free tier is $0, but track it anyway: tokens against the free quota (requests per minute/day) so we see how close we are; a price
  table in config so switching to a paid model shows a real estimate.
- **Time**: done, see "Done: stage timing / provenance" above (`docs/LOGGING.md`, `GET /jobs/<id>/timing`).
- **Per-project summary**: the timing half is done (`job_summary` decision-log entry); still to add: sources pulled, assets found/approved,
  tokens, cost, render length -- and surfacing it in the Slack "done" message once notifications (item A) exist.
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
