"""Sampling assets for human review so a future evaluation report can trust the sample it's built from
(docs/EVALUATION.md). The point isn't just picking assets -- it's TAGGING how each one was picked
(`sampled_as`), and writing that tag down BEFORE the label exists, so nobody can look back later and mistake a
"only the borderline/uncertain ones got a second look" sample for a random, representative one.

Three sampling kinds:
- `random_selected` / `random_rejected`: a plain random draw from what the machine selected/rejected -- the
  representative baseline a report should lean on for yield/missed-use numbers.
- `borderline`: extras near the relevance threshold, deliberately NOT random -- these surface likely mistakes
  fast, but including them in a "how good is this method overall" number would bias it, so the report and the
  SAMPLE_TAGS.jsonl record both keep this tag separate from the random draws.

Tags are written to `<job_dir>/SAMPLE_TAGS.jsonl`, append-only (same discipline as RELEVANCE_LABELS.jsonl and
decisions.jsonl -- a re-run never erases what an earlier run already picked).
"""
from __future__ import annotations

import json
import random as _random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.models import Asset, Job

SAMPLE_TAGS_FILENAME = "SAMPLE_TAGS.jsonl"


@dataclass
class SampleItem:
    asset_id: str
    title: str
    sampled_as: str                 # "random_selected" | "random_rejected" | "borderline"
    scoring_method: str
    method_version: str
    score: float | None
    machine_decision: str
    already_labeled: bool = False


def _decision(a: Asset) -> str:
    return a.vetting.relevance_decision if a.vetting else ""


def _score(a: Asset) -> float | None:
    return a.vetting.relevance if a.vetting else None


def _threshold(a: Asset) -> float | None:
    return a.vetting.relevance_threshold if a.vetting else None


def read_sample_tags(job_dir: Path) -> list[dict[str, Any]]:
    path = Path(job_dir) / SAMPLE_TAGS_FILENAME
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def already_sampled_ids(job_dir: Path) -> set[str]:
    """Asset ids this job has ever surfaced via a previous sampling run, regardless of whether they've been
    labeled yet -- used to avoid nagging you with the same suggestion every time you re-run the script."""
    return {r.get("asset_id") for r in read_sample_tags(job_dir) if r.get("asset_id")}


def pick_sample(job: Job, *, n_random_selected: int = 0, n_random_rejected: int = 0, n_borderline: int = 0,
                 seed: int | None = None, exclude_asset_ids: set[str] | None = None,
                 already_labeled_ids: set[str] | None = None, include_labeled: bool = False) -> tuple[list[SampleItem], int]:
    """Returns (items, seed_used). `exclude_asset_ids` (e.g. previously sampled) and, unless `include_labeled`,
    `already_labeled_ids` are removed from every pool before sampling, so repeated runs surface NEW candidates
    rather than the same ones, and (by default) don't waste a review click re-labeling something already labeled.
    Borderline extras are picked closest-to-threshold first from whichever pool(s) remain after the random draws
    are removed, so a borderline pick is never also counted as one of the random ones."""
    exclude = set(exclude_asset_ids or set())
    if not include_labeled:
        exclude |= set(already_labeled_ids or set())

    scored = [a for a in job.assets if a.vetting is not None and _decision(a) in ("relevant", "not_relevant") and a.id not in exclude]
    selected_pool = [a for a in scored if _decision(a) == "relevant"]
    rejected_pool = [a for a in scored if _decision(a) == "not_relevant"]

    rng_seed = seed if seed is not None else _random.SystemRandom().randrange(2**32)
    rng = _random.Random(rng_seed)

    chosen: dict[str, tuple[Asset, str]] = {}

    def draw(pool: list[Asset], n: int, tag: str) -> None:
        candidates = [a for a in pool if a.id not in chosen]
        rng.shuffle(candidates)
        for a in candidates[:n]:
            chosen[a.id] = (a, tag)

    draw(selected_pool, n_random_selected, "random_selected")
    draw(rejected_pool, n_random_rejected, "random_rejected")

    if n_borderline:
        remaining = [a for a in scored if a.id not in chosen and _score(a) is not None and _threshold(a) is not None]
        remaining.sort(key=lambda a: abs(_score(a) - _threshold(a)))
        for a in remaining[:n_borderline]:
            chosen[a.id] = (a, "borderline")

    already_labeled_ids = already_labeled_ids or set()
    items = [
        SampleItem(asset_id=a.id, title=a.title or a.id, sampled_as=tag,
                   scoring_method=a.vetting.scoring_method if a.vetting else "",
                   method_version=a.vetting.method_version if a.vetting else "",
                   score=_score(a), machine_decision=_decision(a), already_labeled=a.id in already_labeled_ids)
        for a, tag in chosen.values()
    ]
    items.sort(key=lambda it: (it.sampled_as, it.asset_id))
    return items, rng_seed


def write_sample_tags(job_dir: Path, job_id: str, items: list[SampleItem], *, seed: int, by: str, note: str = "") -> None:
    path = Path(job_dir) / SAMPLE_TAGS_FILENAME
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        for it in items:
            row = {"at": at, "job": job_id, "asset_id": it.asset_id, "sampled_as": it.sampled_as, "seed": seed,
                   "by": by, "note": note or None, "scoring_method": it.scoring_method, "method_version": it.method_version,
                   "score": it.score, "machine_decision": it.machine_decision}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
