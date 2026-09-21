"""Keyword stage that needs no network: uses seed keywords passed in the job
options (`options.seed_keywords`) or falls back to words from the subject.
Useful for offline runs and for tests."""
from __future__ import annotations

from ...core.decisions import machine
from ...core.models import Job, Keyword
from ..base import StageContext


class ManualKeywordStage:
    actor = machine("manual-keywords", "1")
    last_trace = {"method": "seed keywords from the job options (e.g. --keywords-file), else the subject and the subject without filler words"}

    async def run(self, job: Job, ctx: StageContext) -> list[Keyword]:
        seeds = job.providers.options.get("seed_keywords")
        if not seeds:
            # No seeds: the subject itself, and the subject without filler words ("true crime jack the ripper" ->
            # "jack ripper"). Single words are NOT proposed: they match unrelated things.
            from ...vetting.rules import STOPWORDS
            core = " ".join(w for w in job.subject.split() if w.lower() not in STOPWORDS)
            seeds = [job.subject] + ([core] if core and len(core.split()) > 1 else [])
        seen: set[str] = set()
        out: list[Keyword] = []
        for i, term in enumerate(seeds, start=1):
            key = term.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(Keyword(term=term.strip(), source="manual", rank=i))
        return out
