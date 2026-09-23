"""Deterministic, local relevance baseline (docs/SCORING.md): an IDF-weighted match between an asset's text and
the approved keywords, with no LLM call, no network, and no rate limit -- the same score every time for the same
inputs. This is the everyday scorer for most assets; the LLM (pipeline/vetting/llm_relevance.py) is only asked
for a second, meaning-aware opinion on the assets whose score lands close to the approval threshold (Orchestrator.
_score_relevance in pipeline/core/orchestrator.py picks those and caps how many go each round), so a run needs far
fewer LLM calls, never runs out of "tier", and gets the same baseline answer if you rerun it.

Pure stdlib (no numpy/sklearn/model download), reusing the tokenizer, stopword list and text-extraction already
used by the word-match fallback (`relevance()` in pipeline/vetting/rules.py) so "the same words count" the same
way across all three scorers.

Two lessons from an earlier version of this file, both kept as comments near the code they fixed:

1. Score each keyword separately and take the best (same "best approved keyword wins" rule `relevance()` uses),
   never pool every approved keyword into one combined query. A real subject is covered by many keywords that
   each describe a different facet of it ("... suspects", "... victims", "... letter", ...), and any one asset
   usually matches just one facet -- pooling diluted a dead-on match under a dozen other keywords' unrelated
   words and pushed genuinely on-topic assets' scores down to ~0.1-0.2, effectively hiding everything.
2. Score as an IDF-weighted RECALL of the keyword's words (what share of the keyword's, weighted-by-rarity,
   words are found in the asset), not a symmetric cosine similarity. Cosine also penalizes the *asset's* extra
   words (Commons assets carry long, useful category lists -- "Jack the Ripper suspects|Whitechapel murders|..."),
   so a dead-on match still scored ~0.2-0.3 purely because the asset's text had many words the short keyword
   didn't. Recall only asks "is the keyword's own meaning present in this asset", which is what min_relevance
   was calibrated against (docs/SCORING.md) and matches how a human reads the "why" text: it names which of the
   keyword's words were found, same as the plain word-match fallback, just weighted so a rare, specific word
   (e.g. "kosminski") counts for more than a common one that appears in almost every asset about this topic
   (e.g. "jack", "ripper").
"""
from __future__ import annotations

import math

from ..core.models import Asset
from .rules import STOPWORDS, asset_text, clean_term, tokens

# Version of THIS scoring algorithm (docs/SCORING_CHANGELOG.md) -- bump this and add a changelog entry any time the
# math in tfidf_scores() below changes, so a run's decision log (Vetting.relevance_method, and the vetted_asset /
# relevance_scoring entries in decisions.jsonl) says exactly which formula actually produced each asset's score.
#
# History (see the changelog for the full story): this file has had three real implementations. Only this one
# (v3) was ever given a version identifier -- v1 and v2 predate this constant entirely, so no asset scored by
# either of them is distinguishable from the other in old logs; they're only reconstructable from git history.
#   v1 - pooled every approved keyword into one combined query before scoring (diluted real matches)
#   v2 - scored each keyword separately, kept the best (fixed dilution) -- but used symmetric cosine similarity,
#        which penalized an asset for its own extra words (Commons category lists) as much as a keyword mismatch
#   v3 - same per-keyword scoring as v2, but IDF-weighted RECALL instead of cosine (module docstring above) --
#        the version this file ships today
VERSION = "tfidf-v3"
FORMULA = ("score = the BEST, over each approved keyword, of the IDF-weighted recall of that keyword's own words in "
           "the asset's text: (sum of idf(w) for w in the keyword's words that also appear in the asset) / (sum of "
           "idf(w) for all of the keyword's words), where idf(w) = ln((1 + N) / (1 + df(w))) + 1 (smoothed, like "
           "scikit-learn's default) over a corpus of this batch's assets plus the keywords' own words. Recall, not "
           "cosine similarity -- the asset's own extra words never count against it. 0.0 to 1.0. See docs/SCORING.md.")


def _keyword_tokens(term: str) -> list[str]:
    seen: list[str] = []
    for w in tokens(clean_term(term)):
        if w not in STOPWORDS and w not in seen:
            seen.append(w)
    return seen


def tfidf_scores(assets: list[Asset], topic_terms: list[str]) -> dict[str, tuple[float, str]]:
    """Returns {asset_id: (0..1 score, 'why' text)} for every asset in `assets`: the BEST IDF-weighted recall
    across all approved keywords (see module docstring for why per-keyword and why recall, not cosine). The IDF
    corpus is this batch's assets plus every keyword's own words, so even a single-asset batch (a test, or a
    "search again" round that only adds one new asset) still gets a sane score instead of a divide-by-zero.
    An empty result means there was nothing to score against (no usable topic terms) -- treat that like any
    other "no score" case, the same as `relevance()` returning `(None, "")`."""
    keyword_bags = [(term, _keyword_tokens(term)) for term in topic_terms]
    keyword_bags = [(term, want) for term, want in keyword_bags if want]
    if not assets or not keyword_bags:
        return {}

    doc_tokens: dict[str, set[str]] = {a.id: tokens(asset_text(a)) for a in assets}
    corpus = list(doc_tokens.values()) + [set(want) for _, want in keyword_bags]
    n_docs = len(corpus)

    df: dict[str, int] = {}
    for doc in corpus:
        for w in doc:
            df[w] = df.get(w, 0) + 1

    def idf(w: str) -> float:
        # Smoothed IDF (as in scikit-learn's default): never zero, never negative, common words still count a
        # little less than rare ones.
        return math.log((1 + n_docs) / (1 + df.get(w, 0))) + 1.0

    out: dict[str, tuple[float, str]] = {}
    for a in assets:
        have = doc_tokens[a.id]
        if not have:
            out[a.id] = (0.0, "no usable words in title/description/tags/page-URL")
            continue
        best_score, best_term, best_hit, best_want = 0.0, "", [], []
        for term, want in keyword_bags:
            hit = [w for w in want if w in have]
            total_weight = sum(idf(w) for w in want)
            score = (sum(idf(w) for w in hit) / total_weight) if total_weight else 0.0
            if score > best_score:
                best_score, best_term, best_hit, best_want = score, term, hit, want
        if best_hit:
            why = f"matched {len(best_hit)} of {len(best_want)} words of '{best_term}' ({', '.join(best_hit)}), weighted by how distinctive each word is"
        else:
            why = "no words shared with any approved keyword (score 0)"
        out[a.id] = (max(0.0, min(1.0, best_score)), why)
    return out
