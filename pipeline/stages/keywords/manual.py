"""Keyword stage that needs no network: uses seed keywords passed in the job
options (`options.seed_keywords`) or falls back to words from the subject.
Useful for offline runs and for tests."""
from __future__ import annotations

from ...core.models import Job, Keyword
from ..base import StageContext


class ManualKeywordStage:
    async def run(self, job: Job, ctx: StageContext) -> list[Keyword]:
        seeds = job.providers.options.get("seed_keywords")
        if not seeds:
            seeds = [job.subject] + [w for w in job.subject.split() if len(w) > 3]
        seen: set[str] = set()
        out: list[Keyword] = []
        for i, term in enumerate(seeds, start=1):
            key = term.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(Keyword(term=term.strip(), source="manual", rank=i))
        return out
