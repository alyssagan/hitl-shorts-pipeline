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
  log to write into; it prints its one call's tokens directly to the terminal and also writes them into a
  `.meta.json` sidecar next to the keywords file -- see "Where a keywords file came from" below for how that
  reaches a job's decision log once the file is actually used.
- Non-LLM API calls (Wikipedia, Commons, Pexels, Pixabay, Unsplash, NASA, Archive, LOC, Smithsonian) were already
  logged in full to `sources/<source>/requests.jsonl` -- see the table at the top of this document -- this
  section is specifically about the LLM calls, which weren't counted anywhere before.

## LLM fallback provider
Every LLM call already retries the *same* provider a few times on 429/500/502/503/504 (see the debugging
checklist above). `[llm_fallback]` in `config/pipeline.toml` adds a second line of defense: a single backup
provider that a call is retried against **once**, only after the primary is still failing after its own
retries. Off automatically until a key is set (`GROQ_API_KEY`, or `LLM_FALLBACK_API_KEY` to override which env
var), same "never a hard requirement" pattern as `[relevance]`'s LLM step -- no key, no fallback, nothing else
changes.

The default backup is **Groq** (`api.groq.com`) -- a different company from xAI's **Grok**, easy to mix up.
Groq has a genuine free developer tier (no credit card) and is an OpenAI-compatible drop-in swap, which is why
`[llm_fallback]`'s shape matches `[keywords]`/`[relevance]`/`[script]` exactly (`base_url`, `model`,
`api_key_env`). Get a free key at [console.groq.com/keys](https://console.groq.com/keys) and put it in `.env`
as `GROQ_API_KEY=...`.

When it fires, you'll see it in the activity log:

```
WARN  llm  script writer: gemini-3.6-flash unavailable (503) after retries; trying backup provider groq (openai/gpt-oss-120b)
INFO  llm  script writer: backup provider groq (openai/gpt-oss-120b) answered
```

and in the decision log: the `stage`'s own trace (`generated_script_and_scenes`'s `logic`, `proposed_keywords`'s
`logic`) records `model`/`endpoint` as **whichever provider actually answered**, plus a `fell_back_from` note
with what the primary would have been, and a `provider` field -- never silently attributed to Gemini when it
wasn't Gemini that answered. The `llm_call` entry (see "Tokens / cost" above) is tagged the same way, with
`what` suffixed `(fallback: groq)`, so tokens/cost are never miscounted against the wrong provider or model
either. `scripts/make_keywords.py`, which has no decision log of its own, prints the same information and
records it in its `.meta.json` sidecar (below) instead.

### "How do I know when it'll restart?"
A 429 means "busy," but not every provider says *how long*. When one does -- Groq sends a real `Retry-After`
header on every 429 it returns, and some Google endpoints nest a `retryDelay` in the error body -- that's what
actually gets waited on (capped at 120s so a huge ask can't hang a run), and the log says so plainly:

```
WARN  llm  script writer: model busy (429); server asked to wait 9s; retry 1/4
```

Gemini's OpenAI-compatible endpoint (`v1beta/openai/...`, what this pipeline actually calls) is the common case
that sends **neither** -- when that happens, the log says so honestly instead of inventing a countdown:

```
WARN  llm  script writer: model busy (429, no wait-time hint from the server); retry 1/4 in 4s
```

When neither the pipeline nor `make_keywords.py` gets a real number and gives up entirely, the final message
explains the two different things a 429 with no explanation could mean: Gemini's per-minute limit (a rolling
window -- just try again shortly) versus its per-day limit (resets at midnight Pacific Time), and points at
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) to see which one actually applies to
your key right now, since that's not something this pipeline can see from the outside.

## Where a keywords file came from
`scripts/make_keywords.py` writes `<file>.meta.json` next to every keywords file it generates -- which
provider/model answered, tokens used, and when -- since that script runs before any job exists and has nowhere
else to put it. That's the record of a SUCCESSFUL generation; a run that fails (busy free tier, a rejected key,
an unavailable model) has no keywords file to attach a sidecar to, so it instead gets a line in
`library/keywords/make_keywords.log` -- an append-only log in the same format `logs/pipeline.log` uses
(timestamp, level, message, `key=val` details), covering every retry, every fallback attempt, and the final
outcome (with the real status code and error detail, not just "it failed"). Without this, a failed run's only
record was whatever was still visible in your terminal -- gone the moment the window closed. Gitignored, since
it's a local operational log, not something to commit.

When you later run `poc.py --keywords manual --keywords-file <file>`, `poc.py` looks for the `.meta.json`
sidecar and, if it's there, passes it straight through as `job.providers.options["keywords_provenance"]`.
`pipeline/stages/keywords/manual.py`'s `ManualKeywordStage` then puts it into its own `last_trace`, which lands
in the `proposed_keywords` decision-log entry's `logic` -- the exact same field the LLM-generated-keywords path
(`--keywords llm`) already uses for its own model/endpoint/prompt:

```json
{"action": "proposed_keywords", "logic": {"method": "seed keywords read from a file (--keywords-file)",
 "file": "library/keywords/jack-the-ripper.txt", "provider": "primary", "model": "gemini-3.6-flash",
 "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "generated_at": "2026-09-22T21:03:40Z",
 "usage": {"prompt_tokens": 640, "completion_tokens": 210, "total_tokens": 850}}}
```

A hand-written keywords file (no `make_keywords.py`, no sidecar) still records *where* the keywords came from
-- just the file path, with no model/tokens to report, since none were spent. No `--keywords-file` at all
records that too (`"the subject and the subject without filler words (no --keywords-file was given)"`). Either
way, "which API, which model, tokens, where the keywords came from" is answered by looking at one job's own
decision log, not by remembering which terminal session generated the file weeks earlier.

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

## Relevance-scoring method and version
That activity-log line only ever shows a per-round *count* by tier ("tfidf" / "llm-semantic" / "keyword-match"), and it rotates
away with the rest of the activity log. The permanent, per-job record is in `decisions.jsonl` / `DECISIONS.md`, and it now
includes not just *which tier* scored an asset but *which version* of that tier's algorithm (docs/SCORING_CHANGELOG.md has the
full version history) -- this closes a real gap: earlier, the algorithm could change (and did -- twice, for TF-IDF) with
nothing in any log saying so.

Each `vetted_asset` entry (one per pending asset, every vetting round) carries the asset's exact scorer+version as two
SEPARATE fields, `logic.scoring_method` and `logic.method_version` -- plus `logic.contributions` (every method that scored
this asset this round, not just the one that won) and `logic.contribution_note` (a plain-English sentence explaining how the
final decision was reached):

```json
{"action": "vetted_asset", "subject": {"asset_id": "a17", "title": "Whitechapel murders map, 1888"},
 "decision": "risk low, relevance 82%",
 "logic": {"method": "rules-v1", "scoring_method": "tfidf", "method_version": "tfidf-v1", "scoring_fallback_note": null,
           "relevance_threshold": 0.5, "relevance_decision": "relevant",
           "contribution_note": "Only the TF-IDF baseline scored this asset this round ...",
           "contributions": [{"scoring_method": "tfidf", "method_version": "tfidf-v1", "score": 0.82, "why": "...", "used_for_decision": true}],
           "rules_checked": [...], "fired": []}}
```

`logic.method` is the *risk-rules* engine's version (duplicate detection, resolution checks, etc. -- docs/VETTING.md) --
unrelated to relevance scoring, despite the similar name. `logic.scoring_method` / `logic.method_version` are the ones that
answer "which sort of method scored this specific image, and which version of it": `tfidf` / `tfidf-v1`, `llm-semantic` /
`llm-semantic-v1`, or `keyword-match` / `keyword-match-v1`, with `logic.scoring_fallback_note` set separately when it's a
fallback (e.g. "LLM unavailable/failed for this item").

The once-per-round `relevance_scoring` entry now also records the *real* formula for whichever method(s) actually ran that
round, instead of a single static description that didn't move when the code did:

```json
{"action": "relevance_scoring", "decision": "34 hidden below 50%",
 "logic": {"methods_used_this_round": ["llm-semantic-v1", "tfidf-v1"],
           "formulas_by_method": {"llm-semantic-v1": "not a mathematical formula -- a judgment call. ... a 0-100 judgment ...",
                                   "tfidf-v1": "score = the BEST, over each approved keyword, of the IDF-weighted recall ..."},
           "keywords_scored_against": [...], "threshold": 0.5, "changelog": "docs/SCORING_CHANGELOG.md"}}
```

To compare how different scoring approaches performed across runs -- the original ask behind this whole section -- use
`scripts/evaluate_relevance.py` (docs/EVALUATION.md), which does this for you from `RELEVANCE_LABELS.jsonl` (grouped by
`scoring_method`+`method_version`, with real Use/Duplicate/Irrelevant outcomes, not just what the machine predicted). To look
at the raw decision log by hand instead: grep a job's `decisions.jsonl` for `"action": "vetted_asset"` and group by
`logic.scoring_method` + `logic.method_version`; `docs/SCORING_CHANGELOG.md` tells you what each version string actually did.
