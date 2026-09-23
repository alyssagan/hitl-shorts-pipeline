# Relevance-scoring method changelog

**Append-only.** This file is a permanent record of every version of every relevance-scoring algorithm this
pipeline has ever shipped, in the order they shipped. A new entry is *added* every time the formula in
`pipeline/vetting/tfidf_relevance.py`, `pipeline/vetting/llm_relevance.py`, or the word-match fallback in
`pipeline/vetting/rules.py` changes -- never edit or remove an existing entry, even a superseded or buggy one.
That mirrors the same append-only rule `decisions.jsonl` follows (docs/LOGGING.md): the point isn't that every
past version was good, it's that the record of what ran, and when, is never lost.

Why this file exists at all: this pipeline has three relevance-scoring methods (docs/SCORING.md), and each one
has its own version identifier that gets recorded on every asset it scores (`Vetting.relevance_method`, e.g.
`"tfidf-v3"`) and in that job's decision log. This file is where you look up what a given version identifier
*actually did* -- the formula, what changed from the version before it, and why -- so old and new scores can be
compared meaningfully instead of just "the code changed at some point."

**How to add an entry:** bump the `VERSION` constant in the file that owns that scorer (`tfidf_relevance.py`,
`llm_relevance.py`, or `rules.py`'s `KEYWORD_MATCH_VERSION`), then append a new section below, same day, same
change. `tests/test_relevance_versioning.py` guards the wiring (it fails if `rules.py`'s defaults drift from the
live `VERSION` constants) but nothing can force a changelog entry to be written by hand -- that discipline is on
whoever makes the next change. If you skip it, the honest thing to do is add the entry retroactively, the same
way the entries below were reconstructed after the fact for the first three TF-IDF versions.

---

## keyword-match

### keyword-match-v1 -- 2026-09-22 (first tracked version)
**Status:** current.
The plain word-overlap fallback used only when neither TF-IDF nor the LLM produced a score for an asset (`relevance()`
in `pipeline/vetting/rules.py`). Formula: best, over the approved keywords, of (that keyword's own words found in
the asset's title/description/tags/page-URL) / (that keyword's words after removing filler words). No history
before this -- this function hasn't changed since it was written, this is just the first time it was given a
version identifier at all.

---

## tfidf (the deterministic local baseline, `pipeline/vetting/tfidf_relevance.py`)

### tfidf-v1 -- undated, superseded before version tracking existed
**Status:** superseded. No asset in any job's decision log can be confirmed to have been scored by exactly this
version -- it predates `VERSION` existing as a field at all, so old `vetted_asset` entries just say `tfidf` with
no way to tell v1 from v2 apart. Reconstructed from git history / code review, not from a log.
Pooled every approved keyword into a single combined query before scoring an asset against it. **Bug:** a real
subject is usually covered by many keywords, each describing a different facet of it (e.g. "... suspects", "...
victims", "... letter"), and a given asset usually only matches one facet. Pooling diluted a dead-on match under
a dozen other keywords' unrelated words, pushing genuinely on-topic assets down to roughly 0.1-0.2 and
effectively hiding them below the default 50% threshold.

### tfidf-v2 -- undated, superseded before version tracking existed
**Status:** superseded, same caveat as v1: not distinguishable from v1 or v3 in any pre-existing log entry.
Fixed the v1 dilution bug: scores each approved keyword *separately* and keeps the best match, the same "best
approved keyword wins" rule the word-match fallback already used. **Remaining bug:** scored similarity as
*symmetric cosine similarity* between the keyword's words and the asset's words. Commons assets often carry
long, useful category lists ("Jack the Ripper suspects | Whitechapel murders | ..."), and cosine penalizes an
asset for having *extra* words the keyword didn't use, the same as it penalizes an asset for missing the
keyword's words. A dead-on match still scored only about 0.2-0.3 purely because the asset's own text was longer
than the keyword.

### tfidf-v3 -- 2026-09-22 (current)
**Status:** current, ships as `pipeline/vetting/tfidf_relevance.VERSION`.
Fixed the v2 cosine bug: scores each keyword's words as an **IDF-weighted recall** against the asset's words --
"what share of the keyword's own (rarity-weighted) words are found in the asset" -- instead of symmetric cosine
similarity. The asset's own extra words no longer count against it; a rare, specific word (e.g. "kosminski")
counts for more than a common one that appears in almost every asset about the topic (e.g. "jack", "ripper").
Formula in full: for each approved keyword, `(sum of idf(w) for w in the keyword's words that also appear in the
asset) / (sum of idf(w) for all of the keyword's words)`, `idf(w) = ln((1 + N) / (1 + df(w))) + 1` (smoothed, like
scikit-learn's default) over a corpus of the batch's assets plus the keywords' own words; an asset's score is the
best across all approved keywords. See `pipeline/vetting/tfidf_relevance.py`'s module docstring and `FORMULA`
constant, and `docs/SCORING.md`.

---

## llm-semantic (the LLM's meaning-aware second opinion, `pipeline/vetting/llm_relevance.py`)

### llm-semantic-v1 -- 2026-09-22 (first tracked version, current)
**Status:** current, ships as `pipeline/vetting/llm_relevance.VERSION`.
A free-tier LLM is shown each asset's title/description/tags/source/kind, plus the job's subject and approved
keywords, and returns a 0-100 judgment of whether the asset actually relates to the topic *in meaning* -- the
right place, period, event, document or person -- not just shared vocabulary, with a one-line reason. Only asked
about assets whose TF-IDF score is "borderline" (docs/SCORING.md). This version identifier tracks the *method*
(the prompt and how its answer is used), not which model answers it: Gemini vs. the Groq fallback
(docs/LOGGING.md "LLM fallback provider") is the same `llm-semantic-v1` method, just answered by a different
model that round -- which model actually answered is recorded separately (`docs/LOGGING.md`, usage tracking).
No history before this -- first version.

---

## Where this is recorded per run

- **Per asset**: `Vetting.relevance_method` on the asset itself, and (as of the entries added alongside this
  changelog) the `vetted_asset` decision-log entry's `logic.relevance_method`.
- **Per run**: the `relevance_scoring` decision-log entry's `logic.methods_used_this_round` and
  `logic.formulas_by_method` -- only the method version(s) that actually scored an asset that round, with the
  real formula text for each, sourced from this changelog (see `pipeline/core/orchestrator.py`'s
  `RELEVANCE_FORMULA_BY_METHOD`).

Neither of those existed before this changelog was added (docs/LOGGING.md and docs/SCORING.md have the details).
