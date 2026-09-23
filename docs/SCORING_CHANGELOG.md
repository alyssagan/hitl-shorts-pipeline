# Relevance-scoring method changelog

**Append-only.** This file is a permanent record of every TRACKED version of every relevance-scoring algorithm
this pipeline ships, in the order they shipped. A new entry is *added* every time the formula in
`pipeline/vetting/tfidf_relevance.py`, `pipeline/vetting/llm_relevance.py`, or the word-match fallback in
`pipeline/vetting/rules.py` changes meaningfully -- never edit or remove an existing entry, even a superseded
one. That mirrors the same append-only rule `decisions.jsonl` follows (docs/LOGGING.md): the point isn't that
every past version was good, it's that the record of what ran, and when, is never lost.

Why this file exists at all: this pipeline has three relevance-scoring methods (docs/SCORING.md), and each one
has its own version identifier that gets recorded, as two SEPARATE fields, on every asset it scores
(`Vetting.scoring_method`, e.g. `"tfidf"`, and `Vetting.method_version`, e.g. `"tfidf-v1"`) and in that job's
decision log and `RELEVANCE_LABELS.jsonl`. This file is where you look up what a given version identifier
*actually did* -- the formula, what changed from the version before it, and why -- so old and new scores can be
compared meaningfully (docs/EVALUATION.md) instead of just "the code changed at some point."

**How to add an entry:** bump the `VERSION` constant in the file that owns that scorer (`tfidf_relevance.py`,
`llm_relevance.py`, or `rules.py`'s `KEYWORD_MATCH_VERSION`), add its new `MethodDefinition` to
`pipeline/vetting/method_registry.py`, then append a new section below, same day, same change.
`tests/test_relevance_versioning.py` guards the wiring (it fails if `rules.py`'s defaults drift from the live
`VERSION` constants, or if `method_registry.py` doesn't cover every live version) but nothing can force a
changelog entry to be written by hand -- that discipline is on whoever makes the next change.

**A hard rule for this file, going forward:** an entry only describes a version if a real score in some job's
`decisions.jsonl` or `RELEVANCE_LABELS.jsonl` can actually be attributed to it (i.e. it shipped after version
tracking existed), OR the entry is explicitly a "pre-tracking history" note that only claims what's verifiable
from the working tree / git history, and does NOT invent a version identifier for something no log ever
recorded. See "Pre-tracking history" under TF-IDF below for what that looks like in practice -- it's prose, not
an addressable version.

---

## keyword-match

### keyword-match-v1 -- 2026-09-22 (first tracked version, current)
**Status:** current, ships as `pipeline/vetting/rules.KEYWORD_MATCH_VERSION`.
The plain word-overlap fallback used only when neither TF-IDF nor the LLM produced a score for an asset
(`relevance()` in `pipeline/vetting/rules.py`). Formula: best, over the approved keywords, of (that keyword's
own words found in the asset's title/description/tags/page-URL) / (that keyword's words after removing filler
words). No history before this -- this function hasn't changed since it was written; this is just the first
time it was given a version identifier at all.

---

## tfidf (the deterministic local baseline, `pipeline/vetting/tfidf_relevance.py`)

### tfidf-v1 -- 2026-09-22 (first TRACKED version, current)
**Status:** current, ships as `pipeline/vetting/tfidf_relevance.VERSION`.
Scores each approved keyword's words as an **IDF-weighted recall** against the asset's own words -- "what share
of the keyword's own (rarity-weighted) words are found in the asset" -- and takes the best score across all
approved keywords. Formula in full: for each approved keyword, `(sum of idf(w) for w in the keyword's words that
also appear in the asset) / (sum of idf(w) for all of the keyword's words)`, `idf(w) = ln((1 + N) / (1 + df(w)))
+ 1` (smoothed, like scikit-learn's default) over a corpus of the batch's assets plus the keywords' own words.
See `pipeline/vetting/tfidf_relevance.py`'s module docstring and `FORMULA` constant, and `docs/SCORING.md`.

This is the **first version of this file's scoring math that was ever given a version identifier and logged**.
Nothing below this line is a separate, addressable version -- see "Pre-tracking history" immediately below for
what can and can't honestly be said about what ran before this.

#### Pre-tracking history (not a version -- do not cite a "tfidf-v0", "-v2" etc. against any real score)
Before `VERSION`/`method_version` existed as a field at all, this file's scoring math was rewritten twice. Two
lessons from that earlier code are kept as comments in `tfidf_relevance.py`'s module docstring, because they're
still true and still explain choices in the current formula:

1. Score each approved keyword separately and take the best match, never pool every approved keyword into one
   combined query. A real subject is covered by many keywords that each describe a different facet of it
   ("... suspects", "... victims", "... letter", ...), and any one asset usually matches just one facet --
   pooling diluted a dead-on match under a dozen other keywords' unrelated words.
2. Score as an IDF-weighted **recall** of the keyword's words, not a symmetric **cosine similarity**. Cosine
   also penalizes the asset's own extra words (Commons assets often carry long, useful category lists), so a
   dead-on match could score low purely because the asset's text was longer than the keyword.

That is the full extent of what's honestly verifiable about this pre-tracking history: two bugs existed and were
fixed, in that order, described above only insofar as the *current* code and its comments describe them. What is
**not** knowable, and this file will not pretend to know: exact dates either earlier state shipped or was fixed,
which (if any) real assets in an existing job were scored by one versus the other, or a formula precise enough
to reproduce either earlier state's exact numbers. No `Vetting.method_version` in any job predating this
changelog says anything more specific than the bare string `"tfidf"` with no version at all -- `docs/EVALUATION.md`
and `scripts/evaluate_relevance.py` group those rows into an explicit "unversioned / missing provenance" bucket
rather than guessing which pre-tracking state produced them.

---

## llm-semantic (the LLM's meaning-aware second opinion, `pipeline/vetting/llm_relevance.py`)

### llm-semantic-v1 -- 2026-09-22 (first tracked version, current)
**Status:** current, ships as `pipeline/vetting/llm_relevance.VERSION` (`PROMPT_VERSION` is the same string,
named for what it identifies: the prompt/method, not the model answering it).
A free-tier LLM is shown each asset's title/description/tags/source/kind, plus the job's subject and approved
keywords, and returns a 0-100 judgment of whether the asset actually relates to the topic *in meaning* -- the
right place, period, event, document or person -- not just shared vocabulary, with a one-line reason. Only asked
about assets whose TF-IDF score is "borderline" (docs/SCORING.md). The exact wording is `PROMPT` in
`pipeline/vetting/llm_relevance.py`, unchanged since this version shipped.

This version identifier tracks the *prompt/method* (the wording and how its answer is used), not which model
answers it: Gemini vs. the Groq fallback (docs/LOGGING.md "LLM fallback provider") is the same `llm-semantic-v1`
method, just answered by a different model that round -- which model actually answered a given call is recorded
separately, per call, by the usage log (`docs/LOGGING.md` "Tokens / cost"), never by this identifier. No history
before this -- first version.

---

## Where this is recorded per run

- **Per asset**: `Vetting.scoring_method` (e.g. `"tfidf"`) and `Vetting.method_version` (e.g. `"tfidf-v1"`) as
  two separate fields on the asset itself; the `vetted_asset` decision-log entry's `logic.scoring_method` /
  `logic.method_version` (plus `logic.scoring_fallback_note`, `logic.relevance_threshold`,
  `logic.relevance_decision`, `logic.contributions` and `logic.contribution_note` -- see docs/LOGGING.md); and,
  once a human reviews the asset, the same fields snapshotted onto its `RELEVANCE_LABELS.jsonl` row (never
  recomputed later -- see docs/EVALUATION.md).
- **Per run**: the `relevance_scoring` decision-log entry's `logic.methods_used_this_round` and
  `logic.formulas_by_method` -- only the method version(s) that actually scored an asset that round, with the
  real formula/prompt text for each, sourced from `pipeline/vetting/method_registry.py` (single source of truth;
  `pipeline/core/orchestrator.py`'s `RELEVANCE_FORMULA_BY_METHOD` is built directly from it).
- **In the review page**: each asset's card shows `scored by: <scoring_method> [<method_version>]` next to its
  score, with a "What does `<method_version>` do?" panel that fetches the same registry live from `GET /methods`
  (docs/REVIEW_UI.md).
- **Across method/version, over time**: `scripts/evaluate_relevance.py` groups `RELEVANCE_LABELS.jsonl` rows by
  `(scoring_method, method_version)` and reports Use yield / Irrelevant selection rate / etc. per group, so a
  future version can be compared against this one honestly (docs/EVALUATION.md).
