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
| `--resume JOB_ID` | Continue an earlier run |

## Many keywords: batches
Each round searches at most `max_queries` keywords (default 8, in `config/pipeline.toml`). With more than that, the extra ones wait.
Type `more` at the terminal prompt (or use "Search again" in the browser) and the next round takes the keywords not yet searched;
once all have been searched it goes back to page 2 of each. Your new search terms typed at "more" go first. So a 20-keyword file
= 3 rounds, with the review page accumulating the results. Raise `max_queries` for bigger rounds (more downloads per round).

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

## See what's happening
The terminal now streams the pipeline's activity log while it works (`--quiet` to hide, `--debug` for more). Every project has its own
`logs/pipeline.log`, and the review page has an Activity log panel. Details and a debugging checklist: docs/LOGGING.md.
