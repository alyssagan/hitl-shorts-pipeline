"""Which sources belong to which QueryGroup pool (pipeline/core/models.py's QueryGroup; the groups'
purpose and asset-category contract are documented there and in docs/SEARCH_PLANNING.md).

Shared by the keyword-writing prompt (pipeline/stages/keywords/llm.py -- so it asks the model for the
right KIND of phrase, and tells it which groups are even useful this round) and the sourcing router
(pipeline/stages/sourcing.py -- so a "case"-group term like "Corazon Amurao interview" is never sent to
a generic stock site, and a "stock"-group term like "empty hospital hallway" is never sent to an archive).
"""
from __future__ import annotations

from ..core.models import QueryGroup

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
