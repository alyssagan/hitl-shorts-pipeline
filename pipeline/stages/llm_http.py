"""One POST to an OpenAI-compatible chat endpoint, with retries.

Free tiers (Gemini's especially) answer 429/503 "high demand, try later" now and then. That is temporary, so we wait and
retry a few times instead of failing the whole stage. Every retry is written to the project's activity log.

Every successful reply's `usage` block (prompt/completion/total tokens, if the endpoint sends one -- Gemini's
OpenAI-compatible endpoint always does) is also handed to core/usage.py, which is how every LLM call in the
pipeline ends up counted for tokens and $ cost without every call site having to know about it. See docs/LOGGING.md
"Tokens / cost".
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..core import joblog, usage

RETRY_STATUS = (429, 500, 502, 503, 504)
WAITS = (4.0, 10.0, 25.0, 45.0)


async def post_chat(client: httpx.AsyncClient, url: str, headers: dict[str, str], payload: dict[str, Any], *,
                    what: str = "LLM", waits: tuple[float, ...] | None = None) -> httpx.Response:
    waits = WAITS if waits is None else waits
    resp = None
    for attempt in range(len(waits) + 1):
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.TransportError as exc:
            if attempt >= len(waits):
                raise
            joblog.warn("llm", f"{what}: network error ({type(exc).__name__}); retry {attempt + 1}/{len(waits)} in {waits[attempt]:.0f}s")
            await asyncio.sleep(waits[attempt])
            continue
        joblog.debug("llm", f"{what}: {resp.status_code}", model=payload.get("model"))
        if resp.status_code in RETRY_STATUS and attempt < len(waits):
            joblog.warn("llm", f"{what}: model busy ({resp.status_code}); retry {attempt + 1}/{len(waits)} in {waits[attempt]:.0f}s")
            await asyncio.sleep(waits[attempt])
            continue
        if resp.status_code == 200:
            try:
                usage.record(what, str(payload.get("model", "")), resp.json().get("usage"))
            except Exception:  # noqa: BLE001 - a malformed/unparseable body must not break the actual call
                pass
        return resp
    return resp  # type: ignore[return-value]
