"""Token and $ cost provenance for every LLM call (docs/LOGGING.md "Tokens / cost").

Every LLM call in the pipeline funnels through one place: pipeline.stages.llm_http.post_chat(). When a reply's
JSON includes an OpenAI-style `usage` block (prompt_tokens/completion_tokens/total_tokens -- Gemini's
OpenAI-compatible endpoint always sends one on a 200), post_chat() calls `record()` here with which call it was
(`what`, e.g. "keyword model", "relevance batch 2/3", "script writer") and which model. That is the ONLY thing
post_chat() does differently now; everything else about how it retries and returns is unchanged, and a call with
no `usage` block (a non-200, an endpoint that omits it) simply isn't recorded -- usage tracking must never break
a call.

The orchestrator binds a fresh list around each stage it runs (mirroring core/joblog.py's contextvars pattern
for the per-project activity log) and, once the stage finishes, turns every entry collected during it into its
own `llm_call` decision-log entry -- so token/cost provenance lives in the same tamper-evident, auditable record
as every other decision, never a separate file that can drift out of sync. `Orchestrator.usage_summary()` rolls
up every `llm_call` entry recorded so far for a job (calls, tokens, $ cost, broken down by model and by `what`),
the same way `timing_summary()` rolls up `stage_started`/`stage_finished`. See docs/LOGGING.md.
"""
from __future__ import annotations

import contextvars
from datetime import datetime, timezone
from typing import Any

_calls: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("usage_calls", default=None)

# {model: (prompt_$_per_1M_tokens, completion_$_per_1M_tokens)} -- set once at startup from config/pipeline.toml's
# [usage.prices.<model>] tables via configure_prices(). A model with no entry costs $0 (true for every free-tier
# model this pipeline ships with), never an error.
_PRICES: dict[str, tuple[float, float]] = {}
_QUOTA: dict[str, Any] = {}


def configure_prices(settings: dict[str, Any] | None) -> None:
    """Called once from Orchestrator.__init__ with the loaded config/pipeline.toml. Safe to call repeatedly
    (tests construct fresh Orchestrators often) -- it just replaces the module-level table each time."""
    global _PRICES, _QUOTA
    usage_cfg = (settings or {}).get("usage", {}) if isinstance(settings, dict) else {}
    prices = usage_cfg.get("prices", {}) if isinstance(usage_cfg, dict) else {}
    _PRICES = {
        str(model): (float((p or {}).get("prompt_per_mtok", 0.0)), float((p or {}).get("completion_per_mtok", 0.0)))
        for model, p in prices.items()
    }
    _QUOTA = {str(model): dict(q or {}) for model, q in usage_cfg.get("free_quota", {}).items()} if isinstance(usage_cfg, dict) else {}


def quota_for(model: str) -> dict[str, Any]:
    """The configured free-tier quota for a model (e.g. {"requests_per_minute": 15, "requests_per_day": 1500}),
    or {} if none is configured -- purely informational, so callers/tests never need a default to be present."""
    return dict(_QUOTA.get(model, {}))


def _cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p_price, c_price = _PRICES.get(model, (0.0, 0.0))
    return round((prompt_tokens / 1_000_000) * p_price + (completion_tokens / 1_000_000) * c_price, 6)


def bind() -> contextvars.Token:
    """Start collecting calls made in the current task from here on. Returns a token for `unbind`. Calling
    `record()` with nothing bound (e.g. a test that calls a scorer directly, with no orchestrator around it)
    is a silent no-op, not an error."""
    return _calls.set([])


def unbind(token: contextvars.Token) -> None:
    _calls.reset(token)


def record(what: str, model: str, usage_block: dict[str, Any] | None) -> None:
    """Called by post_chat() after every LLM response. Records one call if (a) something is currently bound
    (an orchestrator-run stage) and (b) the reply actually carried a `usage` block."""
    calls = _calls.get()
    if calls is None or not usage_block:
        return
    prompt = int(usage_block.get("prompt_tokens", 0) or 0)
    completion = int(usage_block.get("completion_tokens", 0) or 0)
    total = int(usage_block.get("total_tokens", 0) or 0) or (prompt + completion)
    calls.append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "what": what, "model": model,
        "prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total,
        "cost_usd": _cost(model, prompt, completion),
    })


def collect() -> list[dict[str, Any]]:
    """Every call recorded in the current context so far. Does not clear the list -- call after a stage
    finishes, once, not repeatedly."""
    return list(_calls.get() or [])


def rollup(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """{"calls": N, "prompt_tokens": N, "completion_tokens": N, "total_tokens": N, "cost_usd": N,
    "by_model": {model: {...same shape...}}}. Used both for a single stage's calls and for every `llm_call`
    decision-log entry a whole job has ever recorded (Orchestrator.usage_summary())."""
    by_model: dict[str, dict[str, Any]] = {}
    total_prompt = total_completion = total_total = 0
    total_cost = 0.0
    for c in calls:
        total_prompt += c["prompt_tokens"]
        total_completion += c["completion_tokens"]
        total_total += c["total_tokens"]
        total_cost += c["cost_usd"]
        m = by_model.setdefault(c["model"], {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                             "total_tokens": 0, "cost_usd": 0.0})
        m["calls"] += 1
        m["prompt_tokens"] += c["prompt_tokens"]
        m["completion_tokens"] += c["completion_tokens"]
        m["total_tokens"] += c["total_tokens"]
        m["cost_usd"] = round(m["cost_usd"] + c["cost_usd"], 6)
    return {"calls": len(calls), "prompt_tokens": total_prompt, "completion_tokens": total_completion,
            "total_tokens": total_total, "cost_usd": round(total_cost, 6), "by_model": by_model}
