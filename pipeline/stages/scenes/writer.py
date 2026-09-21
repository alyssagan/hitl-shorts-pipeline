"""Script writer: our own longer, source-grounded voice-over script.

MoneyPrinterTurbo's script step accepts at most 2000 characters of instructions and
gives no control over length (docs/KNOWN_LIMITATIONS.md #1). This writer calls any
OpenAI-compatible chat endpoint directly (Gemini's free tier by default), so it can
be given much more source text and a target length. If no key is configured the scene
stage falls back to MoneyPrinterTurbo's own script step.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from ...core import joblog
from ...core.models import Job
from ..llm_http import post_chat
from ..mpt_client import MptError

WORDS_PER_SECOND = 2.6          # a typical narration pace, about 155 words a minute

PROMPT = """You write voice-over scripts for short vertical videos.
Subject: {subject}

Length: about {words} words (roughly {seconds} seconds spoken), in {paragraphs} short paragraphs
separated by a blank line. Each paragraph is one idea, one to three sentences. Open with a hook.
Write plain spoken sentences only: no headings, markdown, bullet points, emoji, stage directions,
scene numbers or timestamps.

Facts: use ONLY facts stated in the source text below. If the sources do not support a point,
leave it out, and do not invent numbers, dates or names. Never mention "the source" or "the text".
{keywords}{feedback}
SOURCE TEXT:
{sources}
"""


def clean_script(text: str) -> str:
    """Strip anything that would be read aloud wrongly: markdown, bullets, headings, brackets."""
    text = text.replace("*", "").replace("#", "").replace("`", "")
    text = re.sub(r"\[.*?\]|\(.*?\)", "", text)
    lines = [re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", ln).rstrip() for ln in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def word_count(text: str) -> int:
    return len(text.split())


class ScriptWriter:
    def __init__(self, base_url: str, api_key: str, model: str, target_words: int = 260,
                 grounding_chars: int = 12000, timeout: float = 180,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.target_words = target_words
        self.grounding_chars = grounding_chars
        self.timeout = timeout
        self.transport = transport
        self.retry_waits: tuple[float, ...] | None = None      # None = the defaults in llm_http

    def sources_text(self, job: Job) -> tuple[str, list[dict]]:
        """Source text for the prompt. The budget is split evenly across the articles so
        one long article cannot crowd the others out."""
        bodies = []
        for r in job.references:
            try:
                bodies.append((r, Path(r.path).read_text(encoding="utf-8").split("\n\n", 1)[-1]))
            except OSError:
                continue
        if not bodies:
            return "(no source text was pulled; write only widely known, safe-to-state facts)", []
        share = max(500, self.grounding_chars // len(bodies))
        parts = [f'--- "{r.title}" ({r.url}) ---\n{body[:share]}' for r, body in bodies]
        return "\n\n".join(parts), [{"title": r.title, "url": r.url, "chars_used": min(len(b), share)} for r, b in bodies]

    def build_prompt(self, job: Job, keywords: list[str]) -> tuple[str, list[dict]]:
        sources, used = self.sources_text(job)
        paragraphs = max(4, round(self.target_words / 40))
        kw = f"Work these approved keywords in naturally: {', '.join(keywords)}.\n" if keywords else ""
        fb = ("Reviewer notes on the previous draft (follow them): " + " | ".join(job.scene_feedback) + "\n"
              if job.scene_feedback else "")
        prompt = PROMPT.format(subject=job.subject, words=self.target_words,
                               seconds=round(self.target_words / WORDS_PER_SECOND),
                               paragraphs=paragraphs, keywords=kw, feedback=fb, sources=sources)
        return prompt, used

    async def write(self, job: Job, keywords: list[str]) -> tuple[str, dict]:
        prompt, used = self.build_prompt(job, keywords)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        joblog.info("script", f"asking {self.model} for about {self.target_words} words", grounded_on=len(used), prompt_chars=len(prompt))
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            resp = await post_chat(client, f"{self.base_url}/chat/completions", headers,
                                   {"model": self.model, "messages": [{"role": "user", "content": prompt}]}, what="script writer",
                                   waits=self.retry_waits)
        if resp.status_code != 200:
            try:
                detail = resp.json()
                detail = (detail[0] if isinstance(detail, list) else detail).get("error", detail)
                detail = detail.get("message", detail) if isinstance(detail, dict) else detail
            except (ValueError, AttributeError, IndexError):
                detail = resp.text[:300]
            raise MptError(f"script model {self.model} returned {resp.status_code}: {str(detail)[:300]}")
        try:
            text = resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError):
            raise MptError("script model returned an unexpected reply") from None
        script = clean_script(text)
        if not script:
            raise MptError("script model returned an empty script")
        n = word_count(script)
        joblog.info("script", f"got {n} words (about {round(n / WORDS_PER_SECOND)}s spoken)")
        trace = {"writer": "own", "model": self.model, "endpoint": self.base_url, "prompt": prompt,
                 "target_words": self.target_words, "words": n, "est_seconds": round(n / WORDS_PER_SECOND),
                 "grounded_on": used, "feedback_used": list(job.scene_feedback), "keywords": keywords}
        if n < self.target_words * 0.6:
            trace["warning"] = f"Script is well under the {self.target_words}-word target ({n} words)."
        return script, trace
