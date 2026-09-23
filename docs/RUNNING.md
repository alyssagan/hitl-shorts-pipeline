# How to run it

From the repo folder, after `docker compose up -d --build` (rebuild whenever code changed):

```
python3 scripts/poc.py "true crime jack the ripper" --keywords manual \
  --sources wikipedia,commons,loc,archive,pexels --reviewer "Aly"
```

Everything on one line if you prefer (drop the `\`). At the asset review open http://localhost:8000/review, submit there, then type `web`.

| Flag | What it does |
|---|---|
| `--keywords manual` | You type the keywords (no API key). `llm` = Gemini suggests them |
| `--keywords-file FILE` | Adds every keyword in the file (one per line). Example: `library/keywords/jack-the-ripper.txt` |
| `--sources a,b,c` | Which sources to pull from (wikipedia, commons, pexels, pixabay, unsplash, nasa, archive, loc, smithsonian, urls) |
| `--urls FILE` | Videos from a list of links (docs/URL_LIST.md) |
| `--min-relevance 0.5` | Hide assets scoring below this |
| `--max-queries N` | How many approved keywords to search per round, **for this job only** (overrides the `config/pipeline.toml` default of 8) |
| `--per-query N` | How many photos EACH SOURCE keeps PER KEYWORD, **for this job only** (overrides the `config/pipeline.toml` default of 4) |
| `--videos-per-query N` | Same, for video clips (default 2) |
| `--resume JOB_ID` | Continue an earlier run |

## Many keywords: batches
Each round searches at most `max_queries` keywords: 8 by default (`config/pipeline.toml`'s `[sources] max_queries`), or whatever
you pass with `--max-queries` for that one job. With more approved keywords than that, the extra ones wait.
Type `more` at the terminal prompt (or click **Next batch** in the browser review page) and the next round takes the keywords not
yet searched; once all have been searched it goes back to page 2 of each. Your new search terms typed at "more" go first. So a
20-keyword file at the default 8 = 3 rounds, with the review page accumulating the results.

**From the browser**, no restart needed: on the asset review page, the "Not enough good ones? Get more" panel has a **Next
batch** button with a batch-size field (defaults to 10). Click it and it searches that many not-yet-searched keywords, no typing
required, and sets that size as the job's `max_queries` for every round after — so you only have to pick it once. See
docs/REVIEW_UI.md.

**From the terminal**, if you're worried 8 won't turn up enough for a topic, raise it per job instead of editing the config:

```bash
python3 scripts/poc.py "true crime jack the ripper" --keywords manual --keywords-file library/keywords/jack-the-ripper.txt \
  --sources wikipedia,commons,loc,archive --max-queries 20 --reviewer "Aly"
```

That searches all 20 keywords in one round instead of 3 (more downloads, more vetting to review, but nothing left waiting).
`--max-queries` only affects that one job; other jobs, and the API's `min_relevance`-style default, are unchanged. To change the
default for every future job instead, edit `config/pipeline.toml`'s `[sources] max_queries` and rebuild:
`docker compose build --no-cache pipeline && docker compose up -d --force-recreate pipeline`.

## Not finding enough good photos? Pull more, then filter by score
If keyword search keeps missing, the fix usually isn't better keywords -- it's more candidates per keyword, filtered by the
relevance score you already get for free. Two independent knobs, both per-job, no config edit or rebuild needed:

- `--max-queries` (above): how many DIFFERENT keywords get searched per round.
- `--per-query` / `--videos-per-query`: how many photos/clips come back FOR EACH keyword, from each source
  (`config/pipeline.toml`'s `[sources] per_query`/`videos_per_query`, default 4/2 -- pipeline/sources/base.py).

Raising `--per-query` pulls a bigger pool of candidates per keyword; the relevance score (TF-IDF, backed up by an LLM
opinion on borderline ones -- docs/SCORING.md) and the review page's **min score slider** then do the filtering, so you're
not depending on the search itself already being precise:

```bash
python3 scripts/poc.py "true crime jack the ripper" --keywords manual --keywords-file library/keywords/jack-the-ripper.txt \
  --sources wikipedia,commons,loc,archive,pexels --per-query 15 --min-relevance 0.5 --reviewer "Aly"
```

That's the same shape as `--max-queries`: it only affects this job's `job.providers.options`, not the config default, and
`pipeline/stages/sourcing.py` applies it to every source adapter that supports paging (all the search-based ones; wikipedia,
folder and urls ignore it since they don't page through results the same way). More candidates does mean more to vet and
more to look at in the review page -- the min score slider is what keeps that manageable; raise `--min-relevance` too if the
extra volume is mostly noise.

## Example
```
python3 scripts/poc.py "true crime jack the ripper" --keywords manual --keywords-file library/keywords/jack-the-ripper.txt \
  --sources wikipedia,commons,loc,archive --reviewer "Aly"
```

## Generate the keyword file with AI (free tier)
```
python3 scripts/make_keywords.py "jack the ripper"            # writes library/keywords/jack-the-ripper.txt
python3 scripts/make_keywords.py "h h holmes" --n 30 --era "1890s Chicago"
python3 scripts/make_keywords.py "jack the ripper" --print    # just show it
python3 scripts/make_keywords.py "jack the ripper" --show-prompt   # see/adjust the prompt (it's PROMPT in the script)
```
Uses your free `GEMINI_API_KEY` from `.env` (Google AI Studio) and the model in `config/pipeline.toml` `[keywords]`. The prompt is written for
true-crime reels: 2 to 5 word phrases with years and places, spread over places, documents and press, investigation and justice, era and daily life,
atmosphere b-roll and people, aimed at archive-friendly material, no graphic content, no victims' names. Single words and repeats are dropped.
Read and edit the file, then use it with `--keywords-file`. Files live in `library/keywords/` (the first one, `jack-the-ripper.txt`, was hand-written).

## Keyword files, always
Every job writes its own `keywords_proposed.json` (what the keyword stage came up with, before you decide
anything) and, once you approve at Gate 1, `keywords_approved.json` (the final list, in
`projects/<name>-<id>/`) -- for **every** `--keywords` provider, not just `llm`. This is what those two files
look like:

```json
// keywords_proposed.json
{"generated_at": "2026-09-23T15:04:00Z", "provider": "llm", "logic": {"model": "gemini-3.6-flash", "...": "..."},
 "keywords": [{"id": "...", "term": "whitechapel 1888", "rank": 1, "search_volume": 720, "difficulty": 34, "why": "..."}]}

// keywords_approved.json
{"job_id": "a1b2c3d4e5f6", "subject": "jack the ripper", "approved_at": "2026-09-23T15:06:12Z", "reviewer": "Aly",
 "note": "", "keywords": ["whitechapel 1888", "victorian london"], "added_by_human": ["victorian london"],
 "rejected_by_omission": ["gothic horror generic"]}
```

**`--keywords manual` with no `--keywords-file`** no longer silently guesses keywords from the subject the
moment you run it. Instead `poc.py` writes an empty, editable file and waits for you:

```
--keywords manual makes no LLM call, and no --keywords-file was given -- an editable keywords file
is waiting for you instead, at:
  library/keywords/_drafts/jack-the-ripper-20260923-150400.txt
Open it, add one search phrase per line, save it, then come back here.
Press Enter once you've saved your edits (or right away to skip and fall back to the subject itself):
```

Open it in any editor, add one phrase per line, save, press Enter in the terminal. Leaving it blank (pressing
Enter right away) falls back to the old subject-derived guess, exactly like before this existed -- nothing is
worse off, you just get a real file to work from if you want one.

**Reusing a subject's keywords**: once a job's keywords clear Gate 1, they're also copied into
`library/keywords/<subject slug>/<job id>.json` -- a small per-subject library (config/pipeline.toml's
`[library] keywords_dir`; blank to turn this off). The next time you run `poc.py` with the same subject (and no
`--keywords-file`), it offers what's there before doing anything else:

```
Found 2 saved keyword set(s) for 'jack the ripper':
  1. 2026-09-22T18:14:00Z -- job f4f4cd217aa7, 11 keyword(s): whitechapel 1888, victorian london, ...
  2. 2026-09-20T09:02:11Z -- job 2648b26311fe, 8 keyword(s): scotland yard, gaslight street, ...
Reuse one of these (1-2), or press Enter to generate fresh keywords instead:
```

Picking one only *reads* that saved file -- your new job gets its own `keywords_proposed.json`/
`keywords_approved.json` and still goes through Gate 1 for you to add, drop or reject terms; nothing already
saved is ever modified or overwritten (every job's copy is named by its own job id). Pressing Enter falls
through to whatever `--keywords` you passed, same as if nothing had been saved yet.

An explicit `--keywords-file FILE` always wins over both of these -- it skips the reuse prompt and the draft
file entirely and loads exactly that file, same as it always has.

## See what's happening
The terminal now streams the pipeline's activity log while it works (`--quiet` to hide, `--debug` for more). Every project has its own
`logs/pipeline.log`, and the review page has an Activity log panel. Details and a debugging checklist: docs/LOGGING.md.
