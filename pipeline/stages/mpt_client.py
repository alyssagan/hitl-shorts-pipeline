"""Thin async client for the MoneyPrinterTurbo REST API (default :8080).

Endpoints used (verified against MoneyPrinterTurbo main):
  POST /api/v1/scripts   -> {"data": {"video_script": str}}
  POST /api/v1/terms     -> {"data": {"video_terms": [str]}}
  POST /api/v1/audio     -> {"data": {"task_id": str}}
  POST /api/v1/videos    -> {"data": {"task_id": str}}
  GET  /api/v1/tasks/{id}-> {"data": {"state": 1|-1|4, "videos": [...], ...}}
  GET  /tasks/{id}/audio.mp3   (static file of a finished audio task)
  POST /api/v1/social-metadata -> {"data": {"title": str, "caption": str, "hashtags": [str, ...]}}
    Platform-ready title/caption/hashtags for the finished video, written by MoneyPrinterTurbo's own
    LLM prompt (a "short-video social media copywriter" role -- catchy title, caption ending in a call
    to action, platform-sized hashtag count). Supports "tiktok", "youtube_shorts", "instagram_reels",
    "facebook_reels" (vendor/MoneyPrinterTurbo app/services/llm.py::SOCIAL_PLATFORMS). Degrades to a
    heuristic fallback server-side rather than erroring outright if its own LLM call fails.
Task state codes: 1 = complete, -1 = failed, 4 = processing.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx

from ..core import joblog

STATE_COMPLETE = 1
STATE_FAILED = -1


MAX_SCRIPT_PROMPT = 2000     # MoneyPrinterTurbo's limit on video_script_prompt


class MptError(RuntimeError):
    pass


class MptClient:
    def __init__(self, base_url: str = "http://localhost:8080", api_key: str = "", timeout: float = 120,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"x-api-key": api_key} if api_key else {}
        self.timeout = timeout
        self.transport = transport   # tests inject httpx.MockTransport

    async def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, transport=self.transport) as c:
            resp = await c.request(method, f"{self.base_url}{path}", **kw)
        joblog.debug("mpt", f"{method} {path} -> {resp.status_code}", ms=int((time.monotonic() - t0) * 1000))
        try:
            body = resp.json()
        except ValueError:
            raise MptError(f"{path}: non-JSON response ({resp.status_code})") from None
        if resp.status_code != 200 or body.get("status") not in (200, None):
            raise MptError(f"{path}: {resp.status_code} {body.get('message', body)}")
        return body.get("data") or {}

    async def script(self, subject: str, language: str = "", paragraphs: int = 3, prompt: str = "") -> str:
        if len(prompt) > MAX_SCRIPT_PROMPT:      # MoneyPrinterTurbo answers 400 above this
            prompt = prompt[:MAX_SCRIPT_PROMPT]
        data = await self._request("POST", "/api/v1/scripts", json={
            "video_subject": subject,
            "video_language": language,
            "paragraph_number": paragraphs,
            "video_script_prompt": prompt,
        })
        text = data["video_script"]
        # MoneyPrinterTurbo reports an AI-provider failure (bad key, retired model, quota) as a normal
        # 200 response whose "script" is the error text. Treat that as the failure it is.
        if text.strip().lower().startswith(("error:", "error ")):
            raise MptError(f"MoneyPrinterTurbo could not write the script: {text.strip()[:300]}")
        return text

    async def terms(self, subject: str, script: str, amount: int = 5) -> list[str]:
        data = await self._request("POST", "/api/v1/terms", json={
            "video_subject": subject, "video_script": script, "amount": amount,
        })
        terms = data["video_terms"]
        if isinstance(terms, str) and terms.strip().lower().startswith("error"):
            raise MptError(f"MoneyPrinterTurbo could not generate search terms: {terms.strip()[:300]}")
        return list(terms)

    async def create_audio(self, script: str, voice_name: str, language: str = "") -> str:
        data = await self._request("POST", "/api/v1/audio", json={
            "video_script": script, "voice_name": voice_name, "video_language": language,
        })
        return data["task_id"]

    async def social_metadata(self, subject: str, script: str, language: str = "", platform: str = "tiktok") -> dict[str, Any]:
        data = await self._request("POST", "/api/v1/social-metadata", json={
            "video_subject": subject, "video_script": script,
            "language": language or "auto", "platform": platform,
        })
        return {"title": data.get("title", ""), "caption": data.get("caption", ""),
                "hashtags": list(data.get("hashtags") or [])}

    async def create_video(self, params: dict[str, Any]) -> str:
        data = await self._request("POST", "/api/v1/videos", json=params)
        return data["task_id"]

    async def wait_task(self, task_id: str, poll: float = 3.0, timeout: float = 3600) -> dict[str, Any]:
        waited = 0.0
        last = None
        while True:
            data = await self._request("GET", f"/api/v1/tasks/{task_id}")
            state = data.get("state")
            seen = (state, data.get("progress"))
            if seen != last:                     # only log when something changed
                joblog.info("mpt", f"task {task_id[:8]} state={state} progress={data.get('progress')}", waited_s=int(waited))
                last = seen
            if state == STATE_COMPLETE:
                return data
            if state == STATE_FAILED:
                raise MptError(f"MoneyPrinterTurbo task {task_id} failed: {data.get('error') or data.get('failed_stage') or 'unknown'}")
            if waited >= timeout:
                raise MptError(f"MoneyPrinterTurbo task {task_id} timed out after {timeout:.0f}s")
            await asyncio.sleep(poll)
            waited += poll

    async def download(self, url_or_path: str, dest: Path) -> Path:
        url = url_or_path if url_or_path.startswith("http") else f"{self.base_url}{url_or_path if url_or_path.startswith('/') else '/' + url_or_path}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=None, headers=self.headers, transport=self.transport) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
        return dest
