# How relevance scoring works

Goal: stop irrelevant assets (e.g. Rip Van Winkle for "jack the ripper") from crowding the review, and score them by whether they
actually relate to your story, not just whether their caption happens to share words with your keywords.

There are two scorers. Every asset gets exactly one score, from whichever of these produced it:

1. **LLM semantic scoring** (`pipeline/vetting/llm_relevance.py`) — the default. A free-tier LLM (Gemini, same as `--keywords llm` and
   the script writer) is shown each asset's title, description, tags and source/kind, together with your subject and approved
   keywords, and judges whether it actually depicts or relates to the topic *in meaning*, not just vocabulary. It scores in
   **batches** (25 assets per call by default), so a 240-asset review is about 10 calls, well inside the free tier.
2. **Keyword word-matching** (`pipeline/vetting/rules.py`, `relevance()`) — the fallback. The share of an approved keyword's
   meaningful words found literally in the asset's title/description/tags/page URL. Fast, free, needs no network. Used automatically
   whenever the LLM scorer is off, unkeyed, or fails for a given asset or batch — scoring never blocks a run.

Each asset's `vetting.relevance_method` says which one produced its score: `llm-semantic`, `keyword-match`, or
`keyword-match (LLM unavailable/failed for this item)`. The review page's "Why this score and risk" panel shows it next to the score.

## Why semantic scoring is the default
Word-matching has two failure modes that came up in real runs: a generic "Victorian street" photo with the right words in its caption
scores well even when it has nothing to do with this specific case, and a genuinely correct photo with a vague or missing caption
scores 0% even though it's exactly right. The LLM is shown the same text but judges meaning: does this really look like the right
place/period/event/document, not just "does it contain these letters". A caption with no informative text still can't be verified, so
it's still told to score low — the model can't see the image either, only its metadata.

## Turning it off
Set `enabled = false` under `[relevance]` in `config/pipeline.toml`, or unset `GEMINI_API_KEY`, to use only keyword word-matching
(no LLM calls, no cost, no network dependency). Nothing else about the review changes.

## The threshold
Default **50%** (`--min-relevance 0.5`; stored in the job as option `min_relevance`), applied the same way regardless of which
scorer produced the number. Under the threshold:
- the asset is flagged `RELEVANCE_LOW` (low severity, never raises risk),
- it is **hidden** from the default asset review list (collapsed to one line in the terminal; hidden behind a toggle on the review
  page), and `ok` / "Use all shown" never approve it,
- it is still downloaded, still numbered/clickable, and can still be approved by hand.

Every asset's score, method and reason are shown in the review, written to `DECISIONS.md` / `decisions.jsonl`, and the `vetting`
line in the activity log (`docs/LOGGING.md`) records how many assets each scorer covered that round.

## Cost and reliability
- **Cost**: $0 on the free tier. Batching keeps calls low (~10 per 240 assets); see `docs/LOGGING.md` and the ideas backlog in
  `docs/ROADMAP.md` for token/cost tracking across the whole pipeline.
- **Free-tier "busy" errors** (429/503) retry automatically (`pipeline/stages/llm_http.py`), same as the keyword and script models.
- **A batch that still fails** (bad reply, retries exhausted) only affects that batch's assets, which fall back to word-matching —
  never the whole run.
- **No image analysis**: only text metadata is sent, never the image/video bytes, so this stays fast, cheap and free-tier-friendly.
  It also means an asset with an empty or generic caption still can't be reliably judged by either scorer.

## Config (`config/pipeline.toml`)
```
[relevance]
enabled = true
api_key_env = "GEMINI_API_KEY"   # reuses your existing key; RELEVANCE_LLM_API_KEY in .env overrides it
batch_size = 25
# base_url / model default to the same ones as [keywords]
```

## Tuning

| Situation | Try |
|---|---|
| Too little shown | `--min-relevance 0.3`, or add more specific keywords |
| Still junk shown | `--min-relevance 0.7` |
| Good photos hidden (vague/missing captions, neither scorer can confirm them) | lower the threshold, or check "show below threshold" / type `hidden` |
| Want $0 and no network dependency | `enabled = false` under `[relevance]` |

## Limits (see KNOWN_LIMITATIONS #16 and #17)
Both scorers work from text metadata, not the picture itself, so a wrong image with a good caption can still score high, and a
right image with no caption can still score low. LLM scoring understands meaning, not just words, but it's still reading a
description, not looking at the photo — a future option is a real vision check of the thumbnail (see `docs/OPTIONS_TO_TRY.md`).
The two scorers can disagree; `relevance_method` on each asset tells you which one actually produced its number, and your
Use/Irrelevant clicks (`docs/REVIEW_UI.md`, `RELEVANCE_LABELS.jsonl`) are the record for checking how well either one is doing.
