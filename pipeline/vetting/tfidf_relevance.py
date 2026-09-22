"""Deterministic, local relevance baseline (docs/SCORING.md): TF-IDF cosine similarity between an asset's text
and the approved keywords, with no LLM call, no network, and no rate limit -- the same score every time for the
same inputs. This is the everyday scorer for most assets; the LLM (pipeline/vetting/llm_relevance.py) is only
asked for a second, meaning-aware opinion on the assets whose TF-IDF score lands close to the approval threshold
(Orchestrator._score_relevance in pipeline/core/orchestrator.py picks those and caps how many go each round), so a
run needs far fewer LLM calls, never runs out of "tier", and gets the same baseline answer if you rerun it.

Pure stdlib (no numpy/sklearn/model download), reusing the tokenizer, stopword list and text-extraction already
used by the word-match fallback (`relevance()` in pipeline/vetting/rules.py) so "the same words count" the same
way across all three scorers.
"""
from __future__ import annotations

import math

from ..core.models import Asset
from .rules import STOPWORDS, asset_text, clean_term, tokens


def _query_tokens(topic_terms: list[str]) -> list[str]:
    """One bag of query tokens across every approved keyword. A word repeated across keywords counts more than
    once, which is fine -- it weights the query toward whatever the researcher's keywords emphasize most."""
    out: list[str] = []
    for term in topic_terms:
        out.extend(w for w in tokens(clean_term(term)) if w not in STOPWORDS)
    return out


def tfidf_scores(assets: list[Asset], topic_terms: list[str]) -> dict[str, tuple[float, str]]:
    """Returns {asset_id: (0..1 cosine similarity to the approved keywords, 'why' text)} for every asset in
    `assets`. The IDF corpus is this batch's assets plus the query itself, so even a single-asset batch (a test,
    or a "search again" round that only adds one new asset) still gets a sane score instead of a divide-by-zero.
    An empty result means there was nothing to score against (no usable topic terms) -- treat that like any
    other "no score" case, the same as `relevance()` returning `(None, "")`."""
    query = _query_tokens(topic_terms)
    if not assets or not query:
        return {}

    doc_tokens: dict[str, list[str]] = {a.id: sorted(tokens(asset_text(a))) for a in assets}
    corpus = list(doc_tokens.values()) + [query]
    n_docs = len(corpus)

    df: dict[str, int] = {}
    for doc in corpus:
        for w in set(doc):
            df[w] = df.get(w, 0) + 1

    def idf(w: str) -> float:
        # Smoothed IDF (as in scikit-learn's default): never zero, never negative, common words still count a
        # little less than rare ones.
        return math.log((1 + n_docs) / (1 + df.get(w, 0))) + 1.0

    def vector(words: list[str]) -> dict[str, float]:
        tf: dict[str, int] = {}
        for w in words:
            tf[w] = tf.get(w, 0) + 1
        return {w: c * idf(w) for w, c in tf.items()}

    qvec = vector(query)
    qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0

    out: dict[str, tuple[float, str]] = {}
    for a in assets:
        dvec = vector(doc_tokens[a.id])
        if not dvec:
            out[a.id] = (0.0, "no usable words in title/description/tags/page-URL")
            continue
        dnorm = math.sqrt(sum(v * v for v in dvec.values())) or 1.0
        shared = set(qvec) & set(dvec)
        dot = sum(qvec[w] * dvec[w] for w in shared)
        score = max(0.0, min(1.0, dot / (qnorm * dnorm)))
        if shared:
            top = sorted(shared, key=lambda w: -(qvec[w] * dvec[w]))[:5]
            why = "TF-IDF cosine match on: " + ", ".join(top)
        else:
            why = "no words shared with the approved keywords (TF-IDF score 0)"
        out[a.id] = (score, why)
    return out
