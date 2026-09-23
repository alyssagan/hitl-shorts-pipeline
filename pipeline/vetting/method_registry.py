"""Single source of truth for what each relevance-scoring method+version actually does: what it does, its
parameters/threshold, its prompt/model where applicable, when it changed and why, and where to read more. This is
what lets you understand a historical score (Vetting.scoring_method/method_version, or a decisions.jsonl /
RELEVANCE_LABELS.jsonl row's same fields) without searching through code -- pipeline.api.app exposes it at
GET /methods, and the review page's "why" panel fetches it so a score can be explained inline (docs/REVIEW_UI.md).

Each entry pulls its VERSION/FORMULA straight from the module that owns that scorer (tfidf_relevance.py,
llm_relevance.py, rules.py's KEYWORD_MATCH_VERSION/FORMULA) rather than duplicating those strings here, so there
is exactly one place a version identifier and its formula are ever written down.

docs/SCORING_CHANGELOG.md is the fuller, prose historical record (what changed between versions and why);
tests/test_relevance_versioning.py cross-checks that every version here is mentioned there, so the two can't
silently drift apart the way the old, unversioned `method` field did before any of this existed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import llm_relevance, tfidf_relevance
from .rules import KEYWORD_MATCH_FORMULA, KEYWORD_MATCH_VERSION, RELEVANCE_MIN


@dataclass(frozen=True)
class MethodDefinition:
    scoring_method: str                          # "tfidf" | "llm-semantic" | "keyword-match"
    method_version: str                          # e.g. "tfidf-v1"
    status: str                                   # "current" | "superseded"
    description: str
    formula_or_prompt: str
    parameters: dict[str, str] = field(default_factory=dict)
    model_note: str = ""                          # set only for llm-semantic: where the prompt text/model lives
    changed: str = ""                             # ISO date this version started shipping
    why_changed: str = ""
    commit: str = ""                              # left blank: this pipeline never runs git commands against your
                                                   # repo on its own, so it can't fill this in -- add the commit
                                                   # hash yourself here if it's useful to you (docs/SCORING_CHANGELOG.md)
    doc: str = "docs/SCORING_CHANGELOG.md"


METHOD_VERSIONS: dict[str, MethodDefinition] = {
    KEYWORD_MATCH_VERSION: MethodDefinition(
        scoring_method="keyword-match", method_version=KEYWORD_MATCH_VERSION, status="current",
        description=("Plain word-overlap fallback, used only when neither TF-IDF nor the LLM produced a score for "
                      "an asset (docs/SCORING.md)."),
        formula_or_prompt=KEYWORD_MATCH_FORMULA,
        parameters={"relevance_threshold": f"{RELEVANCE_MIN} default (--min-relevance / job option min_relevance)"},
        changed="2026-09-22",
        why_changed="First tracked version -- this function's logic predates version tracking entirely and hasn't changed since.",
    ),
    tfidf_relevance.VERSION: MethodDefinition(
        scoring_method="tfidf", method_version=tfidf_relevance.VERSION, status="current",
        description=("Deterministic local baseline: best-keyword IDF-weighted recall of an approved keyword's "
                      "words in the asset's own text. No network call, same answer every time (docs/SCORING.md)."),
        formula_or_prompt=tfidf_relevance.FORMULA,
        parameters={"relevance_threshold": f"{RELEVANCE_MIN} default",
                     "borderline_band": "0.15 default -- how close to the threshold sends an asset to the LLM too"},
        changed="2026-09-22",
        why_changed=("First TRACKED version. Earlier, unversioned implementations of this file existed and were "
                      "fixed twice before version tracking began -- see docs/SCORING_CHANGELOG.md 'Pre-tracking "
                      "history' for what's verifiable about them from this file's own module docstring. No "
                      "historical score can be attributed to one of them specifically; they are not separately "
                      "addressable versions."),
    ),
    llm_relevance.VERSION: MethodDefinition(
        scoring_method="llm-semantic", method_version=llm_relevance.VERSION, status="current",
        description=("A free-tier LLM judges meaning-level relevance (not word overlap) for the TF-IDF-borderline "
                      "assets only (docs/SCORING.md)."),
        formula_or_prompt=llm_relevance.FORMULA,
        parameters={"batch_size": "25 default", "borderline_band": "0.15 default", "max_llm_per_round": "40 default"},
        model_note=("Versions the PROMPT/method, not the model answering it -- see PROMPT in "
                     "pipeline/vetting/llm_relevance.py for the exact wording, and docs/LOGGING.md for which model "
                     "(Gemini, or the Groq fallback) actually answered a given call."),
        changed="2026-09-22",
        why_changed="First tracked version.",
    ),
}


def get(method_version: str) -> MethodDefinition | None:
    return METHOD_VERSIONS.get(method_version)


def as_json() -> dict[str, dict]:
    """JSON-servable form (pipeline.api.app's GET /methods)."""
    return {v: asdict(d) for v, d in METHOD_VERSIONS.items()}
