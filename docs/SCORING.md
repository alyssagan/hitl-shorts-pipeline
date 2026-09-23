# How relevance scoring works

Goal: stop irrelevant assets (e.g. Rip Van Winkle for "jack the ripper") from crowding the review, and score them by whether they
actually relate to your story, not just whether their caption happens to share words with your keywords -- while staying free,
fast, and not dependent on any LLM's rate limit to finish a review.

There are two scorers, used together as a **hybrid**: a fast, free, local baseline runs on everything, and the LLM is only
consulted for the assets that baseline can't confidently call.

1. **TF-IDF baseline** (`pipeline/vetting/tfidf_relevance.py`) -- the default, and the one most assets actually get. A classic,
   deterministic, pure-stdlib text-similarity score (no network, no model download, no rate limit) between the asset's own text
   (title/description/tags/page URL) and your approved keywords. It runs locally on **every pending asset, every round**, in
   well under a second regardless of how many assets there are.
2. **LLM semantic scoring** (`pipeline/vetting/llm_relevance.py`) -- a free-tier LLM (Gemini, same as `--keywords llm` and the
   script writer) is shown each asset's title, description, tags and source/kind, together with your subject and approved
   keywords, and judges whether it actually depicts or relates to the topic *in meaning*, not just vocabulary. It is **only
   asked about assets whose TF-IDF score is "borderline"** -- close enough to your threshold that the cheap score alone isn't a
   confident call (see "Borderline: who gets the LLM" below). It still scores in batches (25 assets per call by default), and a
   hard per-round cap keeps a bad case (lots of borderline assets at once) from burning through the free tier.

Each asset's `vetting.relevance_method` says which one produced its score, and **which version of that method**
(`docs/SCORING_CHANGELOG.md` has the full history of every version ever shipped): `llm-semantic-v1`, `tfidf-v3`,
or (only if TF-IDF itself found no usable text and no LLM score exists either) `keyword-match-v1`, optionally with
a trailing `(...)` note when it's a fallback (e.g. `keyword-match-v1 (LLM unavailable/failed for this item)`).
The review page's "Why this score and risk" panel shows it next to the score.

## Why hybrid, not "LLM for everything"
Sending every asset to an LLM (the old default) meant a 240-asset review made ~10 batched calls -- fine on a good day, but it
scaled with the number of assets, needed a network round trip and a key, and re-ran into the same free-tier "busy" 429/503s
every source and script call can hit. It was also, by definition, non-deterministic: asking again could get a slightly
different score. TF-IDF is deterministic (same inputs, same output, forever), has no "tier" to run out of, and -- because it's
free -- can afford to run on every asset every round instead of being rationed. The LLM's real value is understanding *meaning*
(a generic "Victorian street" photo with the right words but nothing to do with this specific case; a genuinely correct photo
with a vague caption), and that value matters most exactly where TF-IDF is least sure: near the threshold. So that's the only
place it's spent.

## Borderline: who gets the LLM
After TF-IDF scores every pending asset, an asset is "borderline" -- and gets a second, LLM opinion -- if its TF-IDF score is
within `borderline_band` (default **0.15**) of `min_relevance` (default **0.5**), i.e. by default, a TF-IDF score between 35%
and 65%. Outside that window, TF-IDF is treated as confident enough on its own:
- clearly above the band: almost certainly relevant, no need to spend an LLM call confirming it;
- clearly below the band: almost certainly not relevant, same reasoning.

If more assets are borderline than `max_llm_per_round` (default **40**) in a single round, the ones **closest to the
threshold** win the cap -- they're the hardest calls, so the LLM's judgement is worth the most there. The rest simply keep
their TF-IDF score for this round; they're picked up again (and re-evaluated as borderline or not) on a later "search again"
round if they're still pending.

## The threshold
Default **50%** (`--min-relevance 0.5`; stored in the job as option `min_relevance`), applied the same way regardless of which
scorer produced the number. Under the threshold:
- the asset is flagged `RELEVANCE_LOW` (low severity, never raises risk),
- it is **hidden** from the default asset review list (collapsed to one line in the terminal; hidden behind a toggle on the review
  page), and `ok` / "Use all shown" never approve it,
- it is still downloaded, still numbered/clickable, and can still be approved by hand.

Every asset's score, method+version, and reason are shown in the review and written to `DECISIONS.md` / `decisions.jsonl` (the
`vetted_asset` entry's `logic.relevance_method`), and the `vetting` line in the activity log (`docs/LOGGING.md`) records how many
assets each scorer version covered that round. See "Tracking changes to the scoring algorithm" below for how algorithm *changes*,
not just per-asset results, are kept.

## Only scoring what's necessary
"Search again" keeps every not-yet-decided asset from earlier rounds and adds new ones from the next search. TF-IDF is free, so
it's simply recomputed for every pending asset each round -- there's no reason to cache it. The LLM is the scarce resource, so
each vetting round only sends it assets that actually need a second opinion:
- assets that already have a good `llm-semantic` score from an earlier round are **never** resent or silently downgraded --
  `vet_asset()` keeps their prior score untouched;
- of what's left, only the ones whose fresh TF-IDF score is borderline this round are sent, capped as described above.

The activity log (`docs/LOGGING.md`) says exactly what happened each round, for example:

```
INFO  relevance  tfidf baseline scored 240 of 240 pending asset(s)
INFO  relevance  keeping 187 previously LLM-scored asset(s) from an earlier round
INFO  relevance  41 asset(s) are clearly scored by TF-IDF (not within 0.15 of the 50% threshold), skipping the LLM for them
INFO  relevance  scoring 12 asset(s) with gemini-3.6-flash in 1 batch(es)
```

This is also how a run "continues" safely: each asset's score and which scorer produced it (`relevance_method`) live on the asset
itself, saved in the project, so restarting the pipeline, resuming a job, or hitting "search again" never repeats work that already
succeeded -- it only ever tops up what's missing.

## Cost and reliability
- **Cost**: $0. TF-IDF is local and free. The LLM is free-tier and now only sees the borderline slice of a run (often a small
  fraction of the total), so a 240-asset review that once needed ~10 LLM calls might now need one or none.
- **Free-tier "busy" errors** (429/503), on the rare round that does call the LLM, retry automatically (`pipeline/stages/llm_http.py`),
  same as the keyword and script models.
- **A batch that still fails** (bad reply, retries exhausted) only affects that batch's assets, which fall back to their
  TF-IDF score -- never the whole run.
- **No image analysis**: only text metadata is used by either scorer, never the image/video bytes, so this stays fast, cheap
  and free-tier-friendly. It also means an asset with an empty or generic caption can't be reliably judged by either method
  (TF-IDF scores it 0; the LLM, if it's borderline enough to be asked, is told to score low when it can't confirm anything).

## Config (`config/pipeline.toml`)
```
[relevance]
enabled = true
api_key_env = "GEMINI_API_KEY"   # reuses your existing key; RELEVANCE_LLM_API_KEY in .env overrides it
batch_size = 25
borderline_band = 0.15           # how close to min_relevance counts as "borderline" and gets a second LLM opinion
max_llm_per_round = 40           # hard cap on LLM calls per vetting round, even if more assets are borderline
# base_url / model default to the same ones as [keywords]
```

## Turning the LLM off
Set `enabled = false` under `[relevance]` in `config/pipeline.toml`, or unset `GEMINI_API_KEY`, to use only the TF-IDF baseline
(no LLM calls, no cost, no network dependency at all for scoring). `_score_relevance()` still computes and returns the TF-IDF
scores for every asset; it just never has an LLM scorer to hand borderline ones to, so `relevance_method` is `tfidf-v3` (the
current TF-IDF version, see below) for everything. Nothing else about the review changes.

## Tuning

| Situation | Try |
|---|---|
| Too little shown | `--min-relevance 0.3`, or add more specific keywords |
| Still junk shown | `--min-relevance 0.7` |
| Too many/few assets getting a second LLM opinion | raise or lower `borderline_band` |
| LLM calls feel like too many | lower `max_llm_per_round`, or lower `borderline_band` |
| Good photos hidden (vague/missing captions, neither scorer can confirm them) | lower the threshold, or check "show below threshold" / type `hidden` |
| Want $0 and no network dependency at all | `enabled = false` under `[relevance]` |

## Limits (see KNOWN_LIMITATIONS #16 and #17)
Both scorers work from text metadata, not the picture itself, so a wrong image with a good caption can still score high, and a
right image with no caption can still score low. The LLM understands meaning, not just words, but it's still reading a
description, not looking at the photo -- a future option is a real vision check of the thumbnail (see `docs/OPTIONS_TO_TRY.md`).
TF-IDF and the LLM can disagree on a borderline asset (the LLM's score wins, by design); `relevance_method` on each asset tells
you which one actually produced its number, and your Use/Irrelevant clicks (`docs/REVIEW_UI.md`, `RELEVANCE_LABELS.jsonl`) are
the record for checking how well either one is doing.

## Tracking changes to the scoring algorithm
Each of the three scorers has its own version identifier -- `KEYWORD_MATCH_VERSION` in `pipeline/vetting/rules.py`,
`VERSION` in `pipeline/vetting/tfidf_relevance.py`, and `VERSION` in `pipeline/vetting/llm_relevance.py` -- and it's
this identifier, not just the tier name, that lands in `relevance_method` (e.g. `tfidf-v3`, not just `tfidf`).
Nothing is ever deleted or relabeled: whenever the actual scoring math changes, the version constant is bumped and
a new, dated entry is appended to **`docs/SCORING_CHANGELOG.md`** describing exactly what changed and why -- that
file is where you look up what a given version identifier actually did. Two rewrites of the TF-IDF formula are
recorded there as `tfidf-v1` and `tfidf-v2` (both superseded, predating version tracking) leading to the current
`tfidf-v3`.

This shows up in two places per run:
- **Per asset**, in the `vetted_asset` decision-log entry's `logic.relevance_method` (added alongside this
  changelog -- previously this field existed on the live asset but was never written to the permanent log).
- **Per run**, in the `relevance_scoring` entry's `logic.methods_used_this_round` and `logic.formulas_by_method`
  -- the real formula for whichever method(s) actually scored an asset that round, not a single static
  description that doesn't track code changes (which is what happened before: the logged formula text described
  the keyword-match formula and never moved even while TF-IDF's real math changed underneath it).

`pipeline/vetting/rules.py`'s `vet_asset()`/`vet_all()` accept the live version identifiers as parameters (the
orchestrator always passes the real, current ones; their defaults exist only for direct/unit-test callers and are
guarded against drifting from the source-of-truth constants by `tests/test_relevance_versioning.py`), so
forgetting to update one of the two after bumping a `VERSION` constant fails a test immediately rather than
silently going stale the way the old, unversioned `method` field did.
