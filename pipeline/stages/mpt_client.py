"""Thin async client for the MoneyPrinterTurbo REST API (default :8080).

Endpoints used (verified against MoneyPrinterTurbo main):
  POST /api/v1/scripts   -> {"data": {"video_script": str}}
  POST /api/v1/terms     -> {"data": {"video_terms": [str]}}
  POST /api/v1/audio     -> {"data": {"task_id": str}}
  POST /api/v1/videos    -> {"data": {"task_id": str}}
  GET  /api/v1/tasks/{id}-> {"data": {"state": 1|-1|4, "videos": [...], ...}}
  GET  /tasks/{id}/audio.mp3   (static file of a finished audio task)
Task state codes: 1 = complete, -1 = failed, 4 = processing.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

STATE_COMPLETE = 1
STATE_FAILED = -1


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
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, transport=self.transport) as c:
            resp = await c.request(method, f"{self.base_url}{path}", **kw)
        try:
            body = resp.json()
        except ValueError:
            raise MptError(f"{path}: non-JSON response ({resp.status_code})") from None
        if resp.status_code != 200 or body.get("status") not in (200, None):
            raise MptError(f"{path}: {resp.status_code} {body.get('message', body)}")
        return body.get("data") or {}

    async def script(self, subject: str, language: str = "", paragraphs: int = 3, prompt: str = "") -> str:
        data = await self._request("POST", "/api/v1/scripts", json={
            "video_subject": subject,
            "video_language": language,
            "paragraph_number": paragraphs,
            "video_script_prompt": prompt,
        })
        return data["video_script"]

    async def terms(self, subject: str, script: str, amount: int = 5) -> list[str]:
        data = await self._request("POST", "/api/v1/terms", json={
            "video_subject": subject, "video_script": script, "amount": amount,
        })
        return list(data["video_terms"])

    async def create_audio(self, script: str, voice_name: str, language: str = "") -> str:
        data = await self._request("POST", "/api/v1/audio", json={
            "video_script": script, "voice_name": voice_name, "video_language": language,
        })
        return data["task_id"]

    async def create_video(self, params: dict[str, Any]) -> str:
        data = await self._request("POST", "/api/v1/videos", json=params)
        return data["task_id"]

    async def wait_task(self, task_id: str, poll: float = 3.0, timeout: float = 3600) -> dict[str, Any]:
        waited = 0.0
        while True:
            data = await self._request("GET", f"/api/v1/tasks/{task_id}")
            state = data.get("state")
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
