"""The 5 core content niches (requested directly, full "MASTER NICHE PROMPT STRATEGIES" spec) and what's
DIFFERENT about each one for THIS pipeline: which of its own sources fit the niche's aesthetic, and how
that biases (a) the keyword-writing prompt (pipeline/stages/keywords/llm.py) and (b) the per-asset
evaluation schema computed at vetting time (pipeline/vetting/niche.py). See docs/NICHES.md.

A niche is optional (Job.niche, pipeline/core/models.py). A job with none set behaves exactly as it did
before this module existed -- nothing here changes any existing job's behavior.

Two things this spec names -- Instagram and TikTok as SEARCH sources for Pet/Food -- are deliberately NOT
implemented as fetchable sources here: no adapter exists in pipeline/sources for either platform's search
API, and both would need paid/authenticated developer access this pipeline doesn't have configured (see
.env.example, and the standing instruction not to add paid calls without approval). A specific Instagram/
TikTok URL can still be pulled one at a time with the existing "Add links" yt-dlp downloader -- the same
path already used for YouTube/TikTok/X/Vimeo/Instagram/news links -- just not searched automatically the
way Pexels/Pixabay/Unsplash are. Don't promise a niche has automatically-searchable Instagram/TikTok
material; it only has whatever specific links a reviewer pastes in.
"""
from __future__ import annotations

from .core.models import Niche
from .sources.groups import ARCHIVE_SOURCES, STOCK_SOURCES

NICHE_LABELS: dict[str, str] = {
    "true_crime": "True Crime",
    "conspiracy": "Conspiracy",
    "science": "Science & Astronomy",
    "pet_product": "Pet Product",
    "food_bakery": "Food / Bakery",
}

NICHES: tuple[str, ...] = tuple(NICHE_LABELS)  # stable order, used wherever a list of choices is shown

# Which of THIS pipeline's own source adapters fit each niche's aesthetic (rewarded, aesthetic_fit=
# "Excellent") vs. clash with it (penalized, aesthetic_fit="Jarring") -- pipeline/vetting/niche.py's
# only input for computing aesthetic_fit. A source in neither set is "Acceptable": not wrong for the
# niche, just not specifically vouched for either way. Kept as data here (not logic) so a source group's
# membership only ever needs to change in pipeline/sources/groups.py.
NICHE_REWARD_SOURCES: dict[str, set[str]] = {
    # Gritty, dark, historical, atmospheric -- authentic grain, era accuracy, moody lighting.
    "true_crime": ARCHIVE_SOURCES,
    "conspiracy": ARCHIVE_SOURCES,
    # Precise, high-tech, clean, data-driven -- verified space captures, literal scientific taxonomy.
    "science": {"nasa", "smithsonian", "openverse"},
    # Vibrant, warm, macro, high-energy, sensory -- texture triggers, bright exposure, emotional joy.
    "pet_product": STOCK_SOURCES,
    "food_bakery": STOCK_SOURCES,
}
NICHE_PENALIZE_SOURCES: dict[str, set[str]] = {
    # Penalize bright, modern stock.
    "true_crime": STOCK_SOURCES,
    "conspiracy": STOCK_SOURCES,
    # Penalize corporate stock/sci-fi fluff (nasa is excluded -- it's rewarded above, not penalized here).
    "science": STOCK_SOURCES - {"nasa"},
    # Penalize dark or sterile clips -- the moody archival look that true_crime/conspiracy reward.
    "pet_product": ARCHIVE_SOURCES,
    "food_bakery": ARCHIVE_SOURCES,
}

# Appended to the keyword-writing LLM prompt (pipeline/stages/keywords/llm.py PROMPT's {niche_guidance})
# when a job has a niche set -- steers phrasing toward each niche's aesthetic without any new API call.
NICHE_KEYWORD_GUIDANCE: dict[str, str] = {
    "true_crime": (
        "- Niche: TRUE CRIME. Favor gritty, dark, historical, atmospheric phrasing for \"case\"/\"historical\" "
        "terms -- authentic period grain, era accuracy, moody lighting. Penalize anything that reads as bright, "
        "modern stock photography."
    ),
    "conspiracy": (
        "- Niche: CONSPIRACY. Same aesthetic as true crime -- gritty, dark, historical, atmospheric. Favor "
        "archival documents, period photographs and moody lighting over anything that reads as bright, modern "
        "stock."
    ),
    "science": (
        "- Niche: SCIENCE & ASTRONOMY. Favor precise, high-tech, clean, data-driven phrasing. Prefer verified "
        "space imagery and literal scientific subjects (a named phenomenon, instrument or body) over generic "
        "\"stock\" terms; avoid sci-fi-movie framing (\"futuristic\", \"alien invasion\") -- it matches nothing "
        "real in these libraries."
    ),
    "pet_product": (
        "- Niche: PET PRODUCT. Favor vibrant, warm, macro, high-energy, sensory phrasing for \"stock\" terms -- "
        "texture and emotion (\"soft fur close-up\", \"happy dog running outdoors\"), bright exposure. Penalize "
        "dark, sterile or clinical-looking shots."
    ),
    "food_bakery": (
        "- Niche: FOOD / BAKERY. Favor vibrant, warm, macro, high-energy, sensory phrasing for \"stock\" terms "
        "-- texture triggers (\"crust crackle close-up\", \"melted cheese pull\"), bright exposure. Penalize "
        "dark, sterile or clinical-looking shots."
    ),
}


def niche_label(niche: str | None) -> str:
    return NICHE_LABELS.get(niche or "", niche or "")
