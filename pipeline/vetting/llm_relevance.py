"""Semantic relevance scoring: does an asset actually depict/relate to the topic, judged by a free-tier LLM,
instead of just sharing words with your keywords (pipeline/vetting/rules.py's `relevance()`, still used as the
fallback when this is disabled, unavailable, or a batch fails to parse -- "flag, never filter" applies here too:
a scoring failure never blocks a run, it just falls back to the word-match score).

Assets are sent in batches (title/description/tags/source/kind only -- never the image bytes, so this stays
free-tier and fast) with the job's subject and approved keywords, and the model returns a 0-100 score and a
one-line reason per asset. Batching keeps the call count small: ~240 assets / batch_size 25 = ~10 calls a run.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core import joblog
from ..core.models import Asset, Job
from ..stages.llm_http import post_chat

PROMPT = """You are checking whether photos/video clips actually relate to a specific true-story video, not just whether
they share vocabulary with it. Subject: {subject}
Keywords the researcher approved (what the video will cover): {keywords}

For each item below, judge how relevant it is to actually illustrating THIS subject and these keywords -- a real match in
meaning (the right place, period, event, document or person), not just shared words. A caption with no informative words
(blank, or just a file number) should score low: you cannot confirm it is relevant. A caption in a different language or
using synonyms for the same real thing should still score high if the meaning matches.

Score 0 to 100 (100 = clearly and specifically about this) and give a reason in under 12 words.

Items:
{items}

Answer with a JSON array only, no other text, one object per item, in the SAME ORDER:
[{{"id": "<id>", "score": 0-100, "why": "..."}}, ...]
"""


def _item_text(a: Asset) -> str:
    tags = a.meta.get("tags", "") if isinstance(a.meta, dict) else ""
    if isinstance(tags, list):
        tags = ", ".join(str(t) for t in tags)
    bits = [f"id={a.id}", f"kind={a.kind}", f"source={a.source}"]
    if a.title:
        bits.append(f"title=\"{a.title[:160]}\"")
    if a.description:
        bits.append(f"description=\"{a.description[:280]}\"")
    if tags:
        bits.append(f"tags=\"{str(tags)[:160]}\"")
    if not a.title and not a.description and not tags:
        bits.append("(no title, description or tags)")
    return " ".join(bits)


def _parse_batch(text: str, batch: list[Asset]) -> dict[str, tuple[float, str]]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in the model's reply")
    items = json.loads(m.group(0))
    by_id = {a.id for a in batch}
    out: dict[str, tuple[float, str]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        aid = str(it.get("id", ""))
        if aid not in by_id:
            continue
        try:
            score = max(0.0, min(100.0, float(it.get("score", 0)))) / 100.0
        except (TypeError, ValueError):
            continue
        out[aid] = (score, str(it.get("why", ""))[:200])
    return out


@dataclass
class LlmRelevanceScorer:
    base_url: str
    api_key: str
    model: str
    batch_size: int = 25
    timeout: float = 90
    transport: httpx.AsyncBaseTransport | None = None
    retry_waits: tuple[float, ...] | None = None
    method: str = field(init=False)

    def __post_init__(self) -> None:
        self.method = f"llm:{self.model}"

    async def score(self, job: Job, assets: list[Asset], keywords: list[str]) -> dict[str, tuple[float, str]]:
        """Returns {asset_id: (0..1 score, one-line reason)}. Assets a batch fails on are simply absent
        (the caller falls back to word-matching for those, per asset -- never a hard failure)."""
        if not assets or not keywords:
            return {}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        out: dict[str, tuple[float, str]] = {}
        batches = [assets[i:i + self.batch_size] for i in range(0, len(assets), self.batch_size)]
        joblog.info("relevance", f"scoring {len(assets)} asset(s) with {self.model} in {len(batches)} batch(es)")
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for n, batch in enumerate(batches, start=1):
                items = "\n".join(f"{i}. {_item_text(a)}" for i, a in enumerate(batch, start=1))
                prompt = PROMPT.format(subject=job.subject, keywords=", ".join(keywords), items=items)
                try:
                    resp = await post_chat(client, f"{self.base_url}/chat/completions", headers,
                                           {"model": self.model, "temperature": 0.0, "messages": [{"role": "user", "content": prompt}]},
                                           what=f"relevance batch {n}/{len(batches)}", waits=self.retry_waits)
                    if resp.status_code != 200:
                        raise ValueError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    text = resp.json()["choices"][0]["message"]["content"] or ""
                    got = _parse_batch(text, batch)
                except Exception as exc:  # noqa: BLE001 - one bad batch must not sink the others or the run
                    joblog.warn("relevance", f"batch {n}/{len(batches)} failed, falling back to keyword-match for its {len(batch)} asset(s): "
                                             f"{type(exc).__name__}: {exc}")
                    continue
                joblog.debug("relevance", f"batch {n}/{len(batches)}: scored {len(got)} of {len(batch)}")
                out.update(got)
        joblog.info("relevance", f"LLM scored {len(out)} of {len(assets)}; the rest use keyword-matching")
        return out
