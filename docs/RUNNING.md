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
