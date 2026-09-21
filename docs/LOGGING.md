# Logging: what is the pipeline doing right now?

There are three logs, each for a different question.

| Log | Where | Answers |
|---|---|---|
| **Activity log** (new) | `projects/<name>-<id>/logs/pipeline.log` | What happened, step by step, and how long it took. Start here when something is slow or wrong. |
| **Decision log** | `projects/<name>-<id>/DECISIONS.md` (+ `decisions.jsonl`) | Who decided what and why (tamper-evident). |
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
