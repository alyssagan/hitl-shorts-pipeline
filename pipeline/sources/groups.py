"""Which sources belong to which QueryGroup pool (pipeline/core/models.py's QueryGroup; the groups'
purpose and asset-category contract are documented there and in docs/SEARCH_PLANNING.md).

Shared by the keyword-writing prompt (pipeline/stages/keywords/llm.py -- so it asks the model for the
right KIND of phrase, and tells it which groups are even useful this round) and the sourcing router
(pipeline/stages/sourcing.py -- so a "case"-group term like "Corazon Amurao interview" is never sent to
a generic stock site, and a "stock"-group term like "empty hospital hallway" is never sent to an archive).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from ..core.models import QueryGroup

# The prose (description/example) for each group, read from config/query_groups.toml -- the one readable,
# editable place both the keyword-writing prompt (pipeline/stages/keywords/llm.py) and a human skimming
# this file are meant to use for what a group MEANS. Doesn't affect GROUP_POOLS below (the actual routing
# table): that stays tested Python, this is only the wording built into the LLM's prompt.
_QUERY_GROUPS_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "query_groups.toml"
# Baked-in fallback, byte-for-byte the same wording this file always had before query_groups.toml existed
# -- a missing/unreadable/incomplete config file never breaks a job, it just means the wording can't be
# tuned without touching Python until the file is fixed (#1: preserve existing behavior).
_FALLBACK_GROUP_DEFINITIONS: dict[str, dict[str, str]] = {
    "research": {
        "description": "background reading for the narration itself, not a visual search. Sent only to Wikipedia.",
        "example": "Corazon Amurao Dyatlov",
        "example_why": 'a real full name + event, so an article can be found -- not a generic topic phrase like "the case explained"',
    },
    "case": {
        "description": "names a SPECIFIC real, verifiable person, place, document or moment tied directly to this "
                        "case: an actual name, a specific address, an interview, a court proceeding.",
        "example": "Corazon Amurao interview",
        "example_why": "a specific person and moment -- not a guarantee a matching photo exists, just an honest, specific search",
    },
    "historical": {
        "description": "the general era, place or everyday life the case happened in, WITHOUT claiming to show the "
                        "specific people or event.",
        "example": "Chicago residential streets 1960s",
        "example_why": "the era/place, not a specific person or moment",
    },
    "stock": {
        "description": "a generic, timeless visual description with no connection to this specific case -- a shot "
                        "that could illustrate many different stories.",
        "example": "empty hospital hallway",
        "example_why": "",
    },
}


def load_group_definitions(path: Path = _QUERY_GROUPS_CONFIG) -> dict[str, dict[str, str]]:
    """research/case/historical/stock's description+example+example_why, read from `path` (config/
    query_groups.toml by default). Any group missing from the file, or the file itself missing/unreadable/
    malformed, falls back to _FALLBACK_GROUP_DEFINITIONS for that group -- a partial or broken config file
    degrades gracefully rather than crashing a job or silently dropping a group from the prompt."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    out: dict[str, dict[str, str]] = {}
    for name, fallback in _FALLBACK_GROUP_DEFINITIONS.items():
        row = data.get(name)
        out[name] = {**fallback, **row} if isinstance(row, dict) else dict(fallback)
    return out


# Loaded once at import time (same timing as config/pipeline.toml's load_settings(), called once at app
# startup) -- a running process doesn't hot-reload an edit to query_groups.toml; restart to pick one up.
GROUP_DEFINITIONS: dict[str, dict[str, str]] = load_group_definitions()

# Historical/archival libraries: dated, specific material. Openverse and Flickr are broad aggregators (they
# also carry modern content), but the reason to include them here is their historical/archival material --
# Flickr Commons and Openverse's museum/library sources in particular.
ARCHIVE_SOURCES = {"wikipedia", "commons", "archive", "loc", "smithsonian", "chronicling_america", "dpla",
                    "europeana", "openverse", "flickr"}
# Modern, generic stock photo/video sites, plus NASA's science/space imagery: nothing era- or event-specific.
STOCK_SOURCES = {"pexels", "pixabay", "unsplash", "nasa"}
# Sources that don't take a query at all -- `folder` imports its whole configured folder, `urls` pulls
# whatever URL list the job was given -- so QueryGroup routing doesn't apply to them; they run the same
# way regardless of which groups the job's approved keywords use.
QUERYLESS_SOURCES = {"folder", "urls"}

# QueryGroup -> which of the job's chosen sources a term in that group is actually sent to.
#   research:        Wikipedia only -- it's text, never an image/video search (WikipediaSource produces
#                     TextRefs, not Assets; a case/historical/stock term routed there would find nothing
#                     to keep even if the article existed).
#   case/historical:  every archive-capable source EXCEPT Wikipedia.
#   stock:            generic stock sites only.
GROUP_POOLS: dict[QueryGroup, set[str]] = {
    "research": {"wikipedia"},
    "case": ARCHIVE_SOURCES - {"wikipedia"},
    "historical": ARCHIVE_SOURCES - {"wikipedia"},
    "stock": STOCK_SOURCES,
}

# What an Asset found by a term in this group gets stamped with (#7: never "verified_case" -- moving
# something there is always an explicit human decision, whatever search found it). "research" isn't here
# because it never produces an Asset, only TextRefs (see WikipediaSource / TextRef.group).
GROUP_ASSET_CATEGORY = {
    "case": "unverified_case_candidate",
    "historical": "historical_context",
    "stock": "illustrative_stock",
}


def sources_for_group(group: QueryGroup, job_sources: set[str]) -> set[str]:
    """Which of this job's configured sources a term in `group` should actually be searched against."""
    return GROUP_POOLS.get(group, set()) & job_sources


def useful_groups(job_sources: set[str]) -> list[QueryGroup]:
    """Which QueryGroups have at least one of this job's configured sources able to use them --
    for telling the keyword-writing prompt which groups are worth suggesting terms in at all."""
    out: list[QueryGroup] = []
    if "wikipedia" in job_sources:
        out.append("research")
    if job_sources & (ARCHIVE_SOURCES - {"wikipedia"}):
        out.append("case")
        out.append("historical")
    if job_sources & STOCK_SOURCES:
        out.append("stock")
    return out
