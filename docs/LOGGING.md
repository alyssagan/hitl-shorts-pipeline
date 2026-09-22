# Logging: what is the pipeline doing right now?

There are three logs, each for a different question.

| Log | Where | Answers |
|---|---|---|
| **Activity log** (new) | `projects/<name>-<id>/logs/pipeline.log` | What happened, step by step, and how long it took. Start here when something is slow or wrong. |
| **Decision log** | `projects/<name>-<id>/DECISIONS.md` (+ `decisions.jsonl`) | Who decided what and why (tamper-evident) -- also where stage timing/provenance lives, see below. |
| **Request logs** | `projects/<name>-<id>/sources/<source>/requests.jsonl` | Every HTTP request a source made: URL, status, time, size. |

## Reading the activity log
One line per event, oldest first:

```
2026-09-20T23:59:01Z INFO  stage     START sourcing_running  job=a1b2c3 providers={...}
2026-09-20T23:59:01Z INFO  sourcing  pulling from wikipedia  queries=['whitechapel 1888']  known_assets=0
2026-09-20T23:59:03Z INFO  sourcing  wikipedia 'whitechapel 1888'  found=8  kept=6  skipped=2  page=1
2026-09-20T23:59:03Z INFO  sourcing  wikipedia done in 1.9s  new_assets=6  new_references=1
2026-09-20T23:59:09Z WARN  sourcing  pexels skipped: PEXELS_API_KEY is not set
2026-09-20T23:59:12Z INFO  vetting   vetted 40 assets  risk={'low': 30, 'medium': 8, 'high': 2}  scored_against=[...]  min_relevance=0.5
2026-09-20T23:59:12Z INFO  state     vetting_running -> assets_review  event=vetting_done
```

The second column is the level: **DEBUG** (each HTTP request, each asset kept, each retry), **INFO** (each step, each decision, each state change,
each start and end with timing), **WARN** (odd but the run continues, e.g. a busy model being retried, a source skipped) and **ERROR** (a stage failed;
the next line has the traceback). The third column is the component: `stage`, `state`, `keywords`, `sourcing`, `vetting`, `script`, `llm`, `render`,
`mpt` (MoneyPrinterTurbo), `http`, `decision`, `project`.

## Seeing it live
- **Terminal (`poc.py`)**: new lines stream under the "waiting..." line automatically (INFO and up). `--debug` adds DEBUG, `--quiet` turns it off.
- **Review page**: the "Activity log" panel at the bottom (pick INFO/DEBUG/WARN/ERROR; refreshes with the page).
- **Browser/curl**: `http://localhost:8000/jobs/<id>/log` (add `?level=DEBUG`, `&tail=100`, or `&format=json&after=N` for only the new lines).
- **Docker**: `docker compose logs -f pipeline` (INFO and up; set `LOG_LEVEL=DEBUG` in `.env` and recreate the container for DEBUG).
  `docker compose logs -f mpt` shows MoneyPrinterTurbo's own output (the render itself).
- **File**: open `logs/pipeline.log` (always includes DEBUG).

## Debugging checklist
1. Job says `failed`: find the `ERROR ... FAILED <state>` line and the traceback under it.
2. Something slow: look at the `END <state> (Ns)` and `<source> done in Ns` lines.
3. Source returned nothing: look for `found=0` / `skipped=` on its line, then `sources/<source>/requests.jsonl` for the raw requests.
4. LLM problems: `WARN llm ... model busy (503); retry 2/4` means Google's free tier was overloaded; it retries up to 4 times (waits 4, 10, 25, 45 s) before failing.
5. Render problems: `mpt` lines show the render task's state and progress; `docker compose logs mpt` has the detail.

Secrets are never written: any detail named key, api_key, token, secret, password or authorization shows as `***`, and URLs have keys stripped.

## Timing / provenance
"How long did each step take" is not just in the activity log's `END <state> (Ns)` lines (which are ephemeral text,
not really queryable). Every stage's start, finish and duration is **also** written as an entry in the tamper-evident
decision log (`DECISIONS.md` / `decisions.jsonl`) -- actions `stage_started` and `stage_finished`, the latter with
`duration_seconds` in its `outputs`. A stage that fails records `stage_failed` with `duration_seconds` too, so you know
how long it ran before it gave up. This makes timing part of the permanent, auditable record of the project, not
something you have to grep out of a log file that could get rotated or deleted.

On top of that, `GET /jobs/<id>/timing` computes a rollup at any point in the job's life (works on a job that's still
running -- the numbers just stop at "now" -- as well as a finished one):

```json
{
  "total_wall_seconds": 187.4,
  "time_per_stage_seconds": {"keywords_running": 9.8, "sourcing_running": 64.3, "scenes_running": 22.1, "rendering": 91.2},
  "stage_run_counts": {"keywords_running": 1, "sourcing_running": 1, "scenes_running": 1, "rendering": 1},
  "time_waiting_on_you_seconds": 41.0,
  "waits": [{"closed_by": "approved_keywords", "at": "2026-09-22T21:25:31Z", "waited_seconds": 18.0}, ...]
}
```

- `time_per_stage_seconds` / `stage_run_counts`: summed across every time that stage ran -- a "search again" round
  that re-enters `sourcing_running` a second time adds to the same total rather than overwriting it.
- `time_waiting_on_you_seconds` / `waits`: the gap between a stage finishing (a review gate opening) and the next
  decision you make there -- how much of the total wall time was actually you, not the machine.
- Once a job reaches `completed` or `failed`, this same rollup is written one final time to the decision log as a
  `job_summary` entry, so it's part of the permanent record even if nobody happens to hit the endpoint.

`scripts/poc.py` prints a short version of this automatically when a job finishes (success or failure) --
that's also where the retry command comes from when a stage like Gemini's script writer hits a transient
error (`docs/KNOWN_LIMITATIONS.md` #1, #6).

## Tokens / cost
Every LLM call in the pipeline (keyword generation, the script writer, and the relevance scorer's borderline
"second opinion" calls) goes through one shared function, `pipeline/stages/llm_http.py::post_chat()`. When the
model's reply carries an OpenAI-style `usage` block -- Gemini's OpenAI-compatible endpoint always sends one on a
200 -- that call is recorded (`pipeline/core/usage.py`) and turned into its own `llm_call` entry in the same
tamper-evident decision log the rest of this document describes, right next to that stage's `stage_started`/
`stage_finished` entries:

```json
{"action": "llm_call", "stage": "vetting", "actor": {"type": "ai", "name": "relevance batch 1/1", "model": "gemini-3.6-flash"},
 "subject": {"what": "relevance batch 1/1", "model": "gemini-3.6-flash"},
 "outputs": {"prompt_tokens": 812, "completion_tokens": 96, "total_tokens": 908, "cost_usd": 0.0}}
```

`GET /jobs/<id>/usage` rolls every `llm_call` entry a job has made (so far, or ever, once it's finished) up into
totals and a per-model breakdown, the same way `/timing` rolls up stage durations:

```json
{
  "calls": 4, "prompt_tokens": 3120, "completion_tokens": 340, "total_tokens": 3460, "cost_usd": 0.0,
  "by_model": {"gemini-3.6-flash": {"calls": 4, "prompt_tokens": 3120, "completion_tokens": 340, "total_tokens": 3460, "cost_usd": 0.0}},
  "free_quota": {"gemini-3.6-flash": {"requests_per_minute": 15, "requests_per_day": 1500}}
}
```

- **Cost** comes from `config/pipeline.toml`'s `[usage.prices.<model>]` table ($ per 1,000,000 tokens, in and
  out separately). A model with no entry -- every model this pipeline points at by default, all free tier --
  costs exactly $0, which is accurate, not a placeholder. Point `[keywords]`/`[relevance]`/`[script]` at a paid
  model or tier and add its price there; every number above updates automatically, no code changes.
- **`free_quota`** is Google's published free-tier limits for the model, from `[usage.free_quota.<model>]` --
  purely informational (the pipeline does not rate-limit itself against it), so you can eyeball how close a job's
  call count came to it.
- Once a job reaches `completed` or `failed`, the rollup is written one final time to the decision log as a
  `usage_summary` entry, same as `job_summary` for timing, so it's part of the permanent record even if nobody
  hits the endpoint.
- `scripts/poc.py` prints a short version automatically when a job finishes (success or failure), right after
  the timing summary -- nothing to print if the job made no LLM calls at all.
- `scripts/make_keywords.py` runs *before* any job exists (it just writes a keywords file), so it has no decision
  log to write into; it prints its one call's tokens directly to the terminal instead.
- Non-LLM API calls (Wikipedia, Commons, Pexels, Pixabay, Unsplash, NASA, Archive, LOC, Smithsonian) were already
  logged in full to `sources/<source>/requests.jsonl` -- see the table at the top of this document -- this
  section is specifically about the LLM calls, which weren't counted anywhere before.

## Relevance scoring and "search again"
The `relevance` component logs, each vetting round: how many pending assets got a free, local TF-IDF baseline score; how many
already had a good LLM score from an earlier round and were kept, not re-sent; how many TF-IDF called confidently enough to
skip the LLM; and how many were actually borderline and sent to the LLM this round (and whether the per-round cap trimmed that
list) -- see docs/SCORING.md "Borderline: who gets the LLM" and "Only scoring what's necessary". If you're watching the
free-tier rate limit, the last line is the one to check: a round with no "scoring N asset(s) with ..." line made zero LLM calls.

```
INFO  relevance  tfidf baseline scored 240 of 240 pending asset(s)
INFO  relevance  keeping 187 previously LLM-scored asset(s) from an earlier round
INFO  relevance  41 asset(s) are clearly scored by TF-IDF (not within 0.15 of the 50% threshold), skipping the LLM for them
INFO  relevance  scoring 12 asset(s) with gemini-3.6-flash in 1 batch(es)
```
