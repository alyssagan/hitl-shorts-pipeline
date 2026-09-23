"""One POST to an OpenAI-compatible chat endpoint, with retries and an optional backup provider.

Free tiers (Gemini's especially) answer 429/503 "high demand, try later" now and then. That is temporary, so we wait and
retry a few times instead of failing the whole stage. Every retry is written to the project's activity log.

If the caller passes `fallback` (built from `[llm_fallback]` in config/pipeline.toml by
pipeline/stages/registry.py -- see docs/LOGGING.md "LLM fallback provider"), a request that is still failing
after its normal retries against the primary provider is retried ONE more time against the fallback provider's
own endpoint/model/key before giving up. Every returned response is tagged with which provider actually
answered it (`resp.pipeline_model`, `resp.pipeline_provider`, `resp.pipeline_fell_back`) so a caller building a
decision-log trace can record the truth, not the provider it originally asked.

Every successful reply's `usage` block (prompt/completion/total tokens, if the endpoint sends one -- Gemini's
and Groq's OpenAI-compatible endpoints both always do) is also handed to core/usage.py, tagged with whichever
provider actually answered, which is how every LLM call in the pipeline ends up counted for tokens and $ cost
without every call site having to know about it. See docs/LOGGING.md "Tokens / cost".
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..core import joblog, usage
from ..sources.base import DEFAULT_USER_AGENT

RETRY_STATUS = (429, 500, 502, 503, 504)
WAITS = (4.0, 10.0, 25.0, 45.0)
# Never sleep longer than this even if a provider asks for more -- a huge Retry-After shouldn't turn one
# retry into an effectively-hung stage. If a provider's real ask is bigger than this, that's surfaced in the
# log line (the true number is logged), just not slept for in full.
MAX_SERVER_WAIT = 120.0


def _server_retry_hint(resp: httpx.Response) -> float | None:
    """The provider's OWN suggested wait, in seconds, if it gave one -- a standard `Retry-After` header
    (Groq sends this on every 429: docs/LOGGING.md "LLM fallback provider"), or Google's gRPC-style `retryDelay`
    nested in `error.details[]` on some endpoints. Returns None when neither is present, which is the common
    case for Gemini's OpenAI-compatible endpoint specifically -- it doesn't reliably send either, so callers
    must not assume a hint exists and must say so plainly rather than inventing a countdown."""
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            pass  # an HTTP-date Retry-After, not a plain number of seconds -- not parsed, treated as no hint
    try:
        data = resp.json()
        for d in (data.get("error", {}).get("details") or []):
            rd = d.get("retryDelay")
            if isinstance(rd, str) and rd.endswith("s"):
                return max(0.0, float(rd[:-1]))
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError):
        pass
    return None


def _with_user_agent(headers: dict[str, str] | None) -> dict[str, str]:
    """Every LLM endpoint call gets a real User-Agent, same reasoning as the source adapters
    (pipeline/sources/base.py::DEFAULT_USER_AGENT): some providers sit behind bot-protection (e.g.
    Cloudflare) that blocks Python's bare default UA ("python-httpx/...", "Python-urllib/...") outright --
    a 403 with no relation to the API key or rate limit. Doesn't override a caller-supplied User-Agent."""
    out = dict(headers or {})
    out.setdefault("User-Agent", DEFAULT_USER_AGENT)
    return out


def _tag(resp: httpx.Response, model: str, *, fell_back: bool, provider: str = "primary") -> None:
    """Ordinary attributes on the httpx.Response -- not part of httpx's API, just this codebase's way of
    letting a caller ask "which provider/model actually produced this reply" without changing post_chat()'s
    return type (every existing call site already just reads the Response it gets back)."""
    resp.pipeline_model = model
    resp.pipeline_provider = provider
    resp.pipeline_fell_back = fell_back


def _usage_block(resp: httpx.Response) -> dict[str, Any] | None:
    try:
        return resp.json().get("usage")
    except Exception:  # noqa: BLE001 - a malformed/unparseable body must not break the actual call
        return None


async def post_chat(client: httpx.AsyncClient, url: str, headers: dict[str, str], payload: dict[str, Any], *,
                    what: str = "LLM", waits: tuple[float, ...] | None = None,
                    fallback: dict[str, Any] | None = None, sleep=asyncio.sleep) -> httpx.Response:
    """`fallback`, when given, is {"provider": str, "base_url": str, "model": str, "api_key": str} -- see
    pipeline/stages/registry.py::build_llm_fallback(). Passing None (the default) reproduces the exact
    behavior this function had before a fallback provider existed: no second attempt, and a network error that
    survives every retry is still raised rather than returned.

    `sleep`, injectable for tests (default `asyncio.sleep`): a provider's own Retry-After/retryDelay hint
    (_server_retry_hint()) can ask for up to MAX_SERVER_WAIT seconds, which a test must not actually block on."""
    waits = WAITS if waits is None else waits
    headers = _with_user_agent(headers)
    resp: httpx.Response | None = None
    last_exc: Exception | None = None
    for attempt in range(len(waits) + 1):
        try:
            resp = await client.post(url, headers=headers, json=payload)
            last_exc = None
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt >= len(waits):
                break
            joblog.warn("llm", f"{what}: network error ({type(exc).__name__}); retry {attempt + 1}/{len(waits)} in {waits[attempt]:.0f}s")
            await sleep(waits[attempt])
            continue
        joblog.debug("llm", f"{what}: {resp.status_code}", model=payload.get("model"))
        if resp.status_code in RETRY_STATUS and attempt < len(waits):
            hint = _server_retry_hint(resp)
            if hint is not None:
                wait = min(hint, MAX_SERVER_WAIT)
                capped = f" (capped at {MAX_SERVER_WAIT:.0f}s)" if hint > MAX_SERVER_WAIT else ""
                joblog.warn("llm", f"{what}: model busy ({resp.status_code}); server asked to wait {hint:.0f}s{capped}; "
                                   f"retry {attempt + 1}/{len(waits)}")
            else:
                wait = waits[attempt]
                joblog.warn("llm", f"{what}: model busy ({resp.status_code}, no wait-time hint from the server); "
                                   f"retry {attempt + 1}/{len(waits)} in {wait:.0f}s")
            await sleep(wait)
            continue
        break   # a 200, a non-retryable error status, or retries exhausted -- nothing more to do against the primary

    if last_exc is None and resp is not None and resp.status_code == 200:
        model = str(payload.get("model", ""))
        _tag(resp, model, fell_back=False)
        usage.record(what, model, _usage_block(resp))
        return resp

    # The primary provider didn't come through. Try the backup provider once, if one is configured -- never
    # more than once, so a bad backup can't turn a quick failure into a long one.
    if fallback:
        why = f"({type(last_exc).__name__})" if last_exc else f"({resp.status_code})"                    # type: ignore[union-attr]
        joblog.warn("llm", f"{what}: {payload.get('model')} unavailable {why} after retries; trying backup "
                           f"provider {fallback.get('provider', 'backup')} ({fallback['model']})")
        fb_headers = _with_user_agent({"Authorization": f"Bearer {fallback['api_key']}"} if fallback.get("api_key") else {})
        fb_payload = {**payload, "model": fallback["model"]}
        fb_url = fallback["base_url"].rstrip("/") + "/chat/completions"
        provider = fallback.get("provider", "backup")
        try:
            fb_resp = await client.post(fb_url, headers=fb_headers, json=fb_payload)
        except httpx.TransportError as exc:
            joblog.warn("llm", f"{what}: backup provider {provider} also unreachable ({type(exc).__name__}); giving up")
        else:
            _tag(fb_resp, fallback["model"], fell_back=True, provider=provider)
            if fb_resp.status_code == 200:
                joblog.info("llm", f"{what}: backup provider {provider} ({fallback['model']}) answered")
                usage.record(f"{what} (fallback: {provider})", fallback["model"], _usage_block(fb_resp))
                return fb_resp
            fb_hint = _server_retry_hint(fb_resp)
            hint_note = f"; server asked to wait {fb_hint:.0f}s (not retried again -- only one backup attempt per call)" \
                if fb_hint is not None else "; no wait-time hint from the server"
            joblog.warn("llm", f"{what}: backup provider {provider} also failed ({fb_resp.status_code}){hint_note}; giving up")
            return fb_resp

    if last_exc is not None:
        raise last_exc
    return resp  # type: ignore[return-value]
