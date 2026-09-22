"""Keyword/ranking stage backed by any OpenAI-compatible chat endpoint.

That covers OpenAI, Gemini and Anthropic through their OpenAI-compatible
endpoints, OpenRouter, and local servers such as Ollama (`http://localhost:11434/v1`)
-- so you pick the provider purely through config. NOTE: an LLM has no live
search-volume data, so `search_volume` / `difficulty` here are the model's
estimates and are flagged `estimated` in `meta`. Plug a real SEO data source in
as another KeywordStage when you have one (see README, "Adding a provider").
"""
from __future__ import annotations

import json
import re

import httpx

from ...core import joblog
from ...core.decisions import ai
from ..llm_http import post_chat
from ...core.models import Job, Keyword
from ..base import StageContext

PROMPT = """You are an SEO analyst for short-form vertical video.
Subject: {subject}
{feedback}
Return ONLY a JSON array of {n} objects, best first, each with:
  "term" (search phrase), "search_volume" (estimated monthly searches, integer),
  "difficulty" (0-100 estimate), "why" (one short sentence).
"""


class LLMKeywordStage:
    def __init__(self, base_url: str, api_key: str, model: str, count: int = 10, timeout: float = 120,
                 fallback: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.count = count
        self.timeout = timeout
        self.fallback = fallback   # config/pipeline.toml [llm_fallback], via registry.build_llm_fallback()
        self.actor = ai("keyword-llm", model=model)
        self.last_trace: dict = {}

    async def run(self, job: Job, ctx: StageContext) -> list[Keyword]:
        feedback = ""
        if job.keyword_feedback:
            notes = "\n".join(f"- {f}" for f in job.keyword_feedback)
            feedback = f"The reviewer rejected earlier suggestions. Their notes:\n{notes}\n"
        prompt = PROMPT.format(subject=job.subject, feedback=feedback, n=self.count)

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        joblog.info("keywords", f"asking {self.model} for {self.count} keywords about '{job.subject}'")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await post_chat(client, f"{self.base_url}/chat/completions", headers,
                                   {"model": self.model, "messages": [{"role": "user", "content": prompt}]},
                                   what="keyword model", fallback=self.fallback)
            resp.raise_for_status()
        # What actually answered -- the primary model unless post_chat() fell back to the backup provider.
        model_used = getattr(resp, "pipeline_model", self.model)
        endpoint_used = self.fallback["base_url"] if getattr(resp, "pipeline_fell_back", False) and self.fallback else self.base_url
        self.last_trace = {"prompt": prompt, "model": model_used, "endpoint": endpoint_used,
                           "feedback_used": list(job.keyword_feedback),
                           "note": "volume/difficulty are the model's estimates, not measured data"}
        if getattr(resp, "pipeline_fell_back", False):
            self.last_trace["fell_back_from"] = {"model": self.model, "endpoint": self.base_url}
            self.last_trace["provider"] = getattr(resp, "pipeline_provider", "backup")
            self.actor = ai("keyword-llm", model=model_used)   # the decision log's actor should name who actually answered
        text = resp.json()["choices"][0]["message"]["content"]
        kws = parse_keywords(text, source=f"llm:{model_used}")
        joblog.info("keywords", f"got {len(kws)} keywords", terms=[k.term for k in kws])
        return kws


def parse_keywords(text: str, source: str) -> list[Keyword]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError("keyword model did not return a JSON array")
    items = json.loads(match.group(0))
    out = []
    for rank, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not item.get("term"):
            continue
        out.append(
            Keyword(
                term=str(item["term"]).strip(),
                source=source,
                search_volume=_int(item.get("search_volume")),
                difficulty=_float(item.get("difficulty")),
                rank=rank,
                meta={"why": item.get("why", ""), "estimated": True},
            )
        )
    if not out:
        raise ValueError("keyword model returned no usable keywords")
    return out


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
