# How relevance scoring works

Goal: stop irrelevant assets (e.g. Rip Van Winkle for "jack the ripper") from crowding the review. Code: `pipeline/vetting/rules.py`
(`relevance`, `vet_asset`). Version: rules-v1.

## The score (0-100%)

For every asset, against every **approved keyword** (plus any extra search terms you typed at "search again"):

1. **Keyword words.** Lowercase the keyword, split into words, drop filler words (`true, crime, facts, about, story, history, video,
   photo, the, of, in, ...`; full list is `STOPWORDS` in the code and is written to the decision log on every run).
2. **Asset words.** Take the asset's title, description, categories/tags and the words in its page URL (e.g. `.../whitechapel-street-123`).
   The search query that found it is NOT used, otherwise every result would score 100%. A trailing "s" is ignored (letters = letter).
3. **Per keyword:** `matched words / keyword words`. Example: `jack ripper` on "Mitre Square, site of a Jack the Ripper murder" = 2/2 = 100%.
   On "Woman rip a paper" = 0/2 = 0%. `whitechapel killer` on a page URL containing "whitechapel" = 1/2 = 50%.
4. **Asset score = the best keyword's score.**

## The threshold

Default **50%** (`--min-relevance 0.5`; stored in the job as option `min_relevance`). Under the threshold:
- the asset is flagged `RELEVANCE_LOW` (low severity, never raises risk),
- it is **hidden** from the default asset review list, and `ok` never approves it,
- it is still downloaded, still numbered the same, and type `hidden` at the prompt to list them (you can still approve one by number).

Every asset's score is shown next to it in the review and written to `DECISIONS.md` / `decisions.jsonl`, and a `relevance_scoring`
entry records the formula, keywords, filler words, threshold and how many were hidden.

## Tuning

| Situation | Try |
|---|---|
| Too little shown | `--min-relevance 0.3`, or add more specific keywords |
| Still junk shown | `--min-relevance 0.7` |
| Good photos hidden (untitled/vague captions) | lower the threshold, or type `hidden` |

## Limits (see KNOWN_LIMITATIONS #16)

It is word matching, not understanding. Vague or missing titles score low; a caption that repeats your words about the wrong thing
scores high. Use specific multi-word keywords ("whitechapel 1888") for better scores. A free vision check of the thumbnail is a
possible later upgrade.
