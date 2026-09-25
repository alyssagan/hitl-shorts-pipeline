"""Domain models for a human-in-the-loop short-video project (a "job")."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    return uuid.uuid4().hex[:12]


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:max_len].strip("-")) or "project"


class JobState(str, Enum):
    CREATED = "created"
    KEYWORDS_RUNNING = "keywords_running"   # machine: keyword / SEO / ranking analysis
    KEYWORDS_REVIEW = "keywords_review"     # HUMAN GATE 1: approve keywords
    SOURCING_RUNNING = "sourcing_running"   # machine: pull assets from Wikipedia, Pexels, ...
    VETTING_RUNNING = "vetting_running"     # machine: license + content risk check, with reasons
    ASSETS_REVIEW = "assets_review"         # HUMAN GATE 2: approve each asset
    SCENES_RUNNING = "scenes_running"       # machine: script + audio + scenes
    SCENES_REVIEW = "scenes_review"         # HUMAN GATE 3: approve scenes
    RENDERING = "rendering"                 # machine: final render
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUNNING_STATES = {
    JobState.KEYWORDS_RUNNING, JobState.SOURCING_RUNNING, JobState.VETTING_RUNNING,
    JobState.SCENES_RUNNING, JobState.RENDERING,
}
REVIEW_STATES = {JobState.KEYWORDS_REVIEW, JobState.ASSETS_REVIEW, JobState.SCENES_REVIEW}
TERMINAL_STATES = {JobState.COMPLETED, JobState.CANCELLED}


QueryGroup = Literal["research", "case", "historical", "stock"]
# What each group is FOR (see docs/SEARCH_PLANNING.md):
#   research   background for narration -- routed only to text/reference sources (Wikipedia), never
#              treated as an image/video search and never produces an Asset, only TextRefs.
#   case       verified names, aliases, addresses, institutions, dates, interviews, proceedings --
#              routed to archive-capable sources; produces Assets with category="unverified_case_candidate"
#              (never auto-promoted to "verified_case" -- that always needs an explicit human decision).
#   historical relevant places, periods, architecture, everyday life -- same source pool as "case", but
#              produces Assets with category="historical_context" since it isn't claiming to show the
#              actual people/events, just the era.
#   stock      generic description of what should appear onscreen -- routed only to generic stock sites;
#              produces Assets with category="illustrative_stock".


class Keyword(BaseModel):
    id: str = Field(default_factory=_id)
    term: str
    source: str = "unknown"                 # which provider produced it
    search_volume: int | None = None
    difficulty: float | None = None         # 0-100, provider defined
    rank: int | None = None                 # ranking position, if the provider has one
    meta: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False

    # --- structured search plan (docs/SEARCH_PLANNING.md) -------------------------------------------
    # Populated by the LLM keyword stage's structured plan output, or left at defaults for `manual`/
    # reused-library keywords (which never claimed to be more than a search phrase to begin with).
    # `group` decides which sources this term is even sent to (see QueryGroup above) and what category
    # an asset found by it gets stamped with -- it is the single field the new sourcing routing reads.
    group: QueryGroup = "historical"
    visual_needed: str = ""                 # the actual visual this term is trying to find, in plain words
    scene_ref: str = ""                     # which planned scene/beat this supports, if known
    entity: str = ""                        # target person/place/object/event, if this is entity-specific
    aliases: list[str] = Field(default_factory=list)     # verified alternate names/spellings for `entity`
    dates: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    essential: bool = True                  # False = a nice-to-have/optional detail, not a hard requirement
    alternatives: list[str] = Field(default_factory=list)  # other phrasings to try if this one comes up empty


Niche = Literal["true_crime", "conspiracy", "science", "pet_product", "food_bakery"]
# The 5 core content niches (requested directly, full "MASTER NICHE PROMPT STRATEGIES" spec). Optional on
# Job -- None (the default) means a job behaves exactly as it did before this existed: no aesthetic bias in
# keyword phrasing (pipeline/stages/keywords/llm.py), no niche_evaluation on any asset's Vetting. The actual
# per-niche source/aesthetic mappings live in pipeline/niches.py, not here, so this module doesn't need to
# import pipeline.sources.

ScriptStyle = Literal["true_crime_mystery", "stem_science", "dtc_marketing", "math_cs"]
# 4 scriptwriter "voices" (requested directly, 4 niche-tuned scriptwriting personas), each with its own
# system prompt and word-count target -- pipeline/stages/scenes/script_styles.py has the full text. This is
# independent of `Niche` above: Niche biases keyword phrasing and asset aesthetic scoring; ScriptStyle
# controls the script's own voice/structure/length. Optional on Job -- None (the default) means the
# scriptwriter uses its original source-grounded prompt, unchanged from before this existed.

Severity = Literal["info", "low", "medium", "high"]


class Flag(BaseModel):
    """One reason the machine thinks an asset needs a closer look."""
    rule: str                               # stable id, e.g. LIC_NC
    severity: Severity
    message: str                            # plain-English why
    evidence: str = ""                      # the exact text/field that triggered it


class ScoreContribution(BaseModel):
    """One scorer's actual output for this asset in the round that produced `Vetting.relevance` (docs/
    SCORING_CHANGELOG.md). More than one can exist for the same asset: TF-IDF runs on every pending asset first,
    and the LLM is then asked about the ones that are borderline -- when that happens both scores are real and
    both are kept here, even though only one (`used_for_decision`) is authoritative for `Vetting.relevance`."""
    scoring_method: str                     # "tfidf" | "llm-semantic" | "keyword-match"
    method_version: str                     # e.g. "tfidf-v1" -- docs/SCORING_CHANGELOG.md has what each one did
    score: float | None = None
    why: str = ""
    used_for_decision: bool = False         # True on whichever contribution's score became `Vetting.relevance`


AestheticFit = Literal["Excellent", "Acceptable", "Jarring"]
NicheEvalAction = Literal["Approved", "Flagged for Review", "Rejected"]


class NicheEvaluation(BaseModel):
    """Stage 3's "Evaluation Engine Output Schema" (requested directly, verbatim field names/shapes),
    computed once per vetting round for every asset once a niche is set on the job (pipeline/vetting/
    niche.py). It never re-judges risk or relevance itself -- it only re-expresses what vet_asset() already
    decided (Vetting.relevance/risk/flags) through the niche's aesthetic lens (pipeline/niches.py).
    `action` is a SUGGESTED label for this schema ONLY: it is never applied to Asset.status. Gate 2 still
    requires an explicit human Approve/Reject on every asset, exactly as before (see rules.py's own
    docstring: "Nothing here approves or rejects an asset. Every asset still goes to a human.")."""
    niche_evaluated: str = ""               # the niche's display label, e.g. "True Crime"
    relevance_score: int | None = None      # 1-10 (derived from Vetting.relevance*10, floor of 1 once scored);
                                             # None = not yet relevance-scored this round (never 0 -- see niche.py)
    aesthetic_fit: AestheticFit | None = None
    risk_assessment: str = ""               # Vetting.summary, carried over verbatim -- this module never re-risks
    reasoning: str = ""                     # one-line plain-English justification
    action: NicheEvalAction | None = None
    method: str = ""                        # this evaluation formula's own version, e.g. "niche-eval-v1" --
                                             # not part of the requested schema; left out of any exported report


class Vetting(BaseModel):
    risk: Literal["low", "medium", "high"] = "low"
    flags: list[Flag] = Field(default_factory=list)
    method: str = "rules-v1"                # the RISK-RULES engine's version (docs/VETTING.md) -- unrelated to
                                             # relevance scoring despite the similar name; see scoring_method/
                                             # method_version below for that.
    summary: str = ""                       # how the risk level was derived
    usable: bool = True                     # False when the renderer cannot use the file
    relevance_why: str = ""                 # plain-English reason for the score
    relevance: float | None = None          # 0..1 share of a keyword's words found in the asset's text; None = no topic given
    scoring_method: str = ""                # which contribution is authoritative for `relevance`:
                                             # "tfidf" | "llm-semantic" | "keyword-match" | "" (not scored)
    method_version: str = ""                # that method's version, e.g. "tfidf-v1" (docs/SCORING_CHANGELOG.md)
    scoring_fallback_note: str = ""         # set only for a keyword-match fallback, e.g. "LLM unavailable/failed
                                             # for this item" -- explains WHY it fell back, not a formatting suffix
    relevance_threshold: float | None = None   # the threshold this asset was actually judged against at scoring
                                             # time (snapshotted -- never recalculate a historical decision against
                                             # today's config; see docs/EVALUATION.md)
    relevance_decision: Literal["relevant", "not_relevant", ""] = ""   # derived from relevance vs. relevance_threshold
                                             # AT THE TIME OF SCORING; "" only when relevance is None (not scored)
    contribution_note: str = ""             # plain-English: how the contribution(s) below produced the final decision
    contributions: list[ScoreContribution] = Field(default_factory=list)
    niche_evaluation: NicheEvaluation | None = None   # set only when Job.niche is set -- see NicheEvaluation above


AssetCategory = Literal["verified_case", "unverified_case_candidate", "historical_context", "illustrative_stock", "reconstruction"]
IdentityStatus = Literal["unverified", "verified", "disputed"]
# What the available evidence suggests about REUSE RIGHTS -- never a definitive legal determination, and
# never auto-derived from "found no rights_advisory" alone (docs/RIGHTS.md). "public_domain" and "cc0" are
# kept separate on purpose: CC0 is an explicit dedication by the rights holder, public-domain-by-law/expiry
# (LOC "no known restrictions", NASA, a US government work) is a different kind of claim with different
# evidence behind it, and #8 explicitly asks these stay distinguishable rather than being merged.
RightsStatus = Literal["public_domain", "cc0", "open_license", "paid_license", "unresolved"]
ImportMethod = Literal["search", "manual_url", "local_folder", "scene_upload", "search_pick"]


class Asset(BaseModel):
    id: str = Field(default_factory=_id)
    source: str                             # adapter name: wikipedia_commons, pexels, ...
    kind: Literal["image", "video"] = "image"
    path: str                               # absolute path of the downloaded file
    rel_path: str = ""                      # path relative to the project folder
    source_url: str = ""                    # where the file bytes came from
    page_url: str = ""                      # human-readable page for the asset
    title: str = ""
    description: str = ""
    query: str = ""                         # search that found it
    author: str = ""                        # creator/photographer/agency/uploader, whichever the source gives
    license: str = ""                       # the reuse claim AS STATED by the source -- never edited to match
    license_url: str = ""                   # a later rights determination; see rights_* below for that.
    attribution: str = ""                   # ready-to-paste credit line
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    mime: str = ""
    sha256: str = ""
    fetched_at: str = Field(default_factory=_now)       # when THIS PIPELINE retrieved the file -- not when
                                             # the photo/video was made or the event happened; see media_date/
                                             # event_date below, which are about the real world, not retrieval.
    meta: dict[str, Any] = Field(default_factory=dict)   # raw source metadata
    vetting: Vetting | None = None
    status: Literal["pending", "approved", "rejected"] = "pending"
    decision_note: str = ""                 # the approve/reject decision's own note -- independent of
                                             # identity_notes/rights_notes below (#8: these axes never collapse)
    reviewer: str = ""
    reviewed_at: str = ""

    # --- provenance (#9, #10) ------------------------------------------------------------------------
    import_method: ImportMethod = "search"  # how this asset entered the job: a keyword search, a pasted
                                             # URL at Gate 2, one specific result a person picked out of
                                             # Gate 2's on-demand "Find more" search (source_search_view /
                                             # assets_add_candidate -- unlike a normal batch-searched asset,
                                             # this one was chosen by a human before it was even downloaded),
                                             # an already-downloaded file from library/scraped (the `folder`
                                             # source), or a Gate-3 scene upload/drop.
    owner_submitted: bool = False           # you explicitly marked this as your own footage/photo (#10) --
                                             # never inferred, always an explicit action.
    owner_note: str = ""                    # your note on why/how this is your own material, if owner_submitted.

    # --- case connection: separate from relevance and separate from rights (#7, #8) ------------------
    # `category` is never set to "verified_case" by any automated process -- sourcing/vetting/relevance
    # scoring may propose "unverified_case_candidate" (from a `case`-group search, docs/SEARCH_PLANNING.md)
    # or "historical_context"/"illustrative_stock" (from their matching search groups); moving something to
    # "verified_case" or "reconstruction" is always an explicit human action (see identity_status below).
    # None = not yet categorized at all (legacy assets, or a source/path that doesn't feed the new routing).
    category: AssetCategory | None = None
    depicts: str = ""                       # who/what this item is CLAIMED to show
    case_connection: str = ""               # what specifically connects it to the case
    identity_evidence: str = ""             # the source's own caption/record text supporting that connection
    identity_status: IdentityStatus = "unverified"   # a keyword match or AI similarity score NEVER moves this
                                             # by itself (#7) -- only an explicit reviewer action does.
    identity_reviewer: str = ""
    identity_reviewed_at: str = ""
    identity_notes: str = ""
    media_date: str = ""                    # when the photo/video was actually made, if known
    event_date: str = ""                    # when the depicted event happened, if known and different --
                                             # e.g. a later interview about an earlier event (#7): keep both.

    # --- rights: independent of identity and of your use/reject selection (#8) -----------------------
    # rights_status is derived, when it is set at all, from the license/license_url the source itself
    # reports (see classify_rights_status() in pipeline/vetting/rules.py) -- it is evidence, not a legal
    # conclusion. rights_reviewer/rights_reviewed_at are the ONLY way this becomes a human-signed-off
    # determination; approving an asset for use (Asset.status) never touches any rights_* field.
    rights_status: RightsStatus = "unresolved"
    rights_evidence: str = ""               # the exact text/record that supports rights_status (a rights
                                             # advisory string, a license URL, a note about a permission email)
    rights_reviewer: str = ""
    rights_reviewed_at: str = ""
    rights_notes: str = ""


class TextRef(BaseModel):
    """Research material for narration (e.g. a Wikipedia article) -- NOT a visual asset (#2). Always
    reported and counted separately from Asset; never appears in the asset review gate."""
    id: str = Field(default_factory=_id)
    source: str
    title: str
    url: str                                # kept even across rounds/dedup -- see wikipedia.py
    path: str
    rel_path: str = ""
    license: str = ""
    license_url: str = ""
    query: str = ""
    group: QueryGroup = "research"
    chars: int = 0
    retrieved_at: str = Field(default_factory=_now)


class SceneCrop(BaseModel):
    """A human override for the framing MoneyPrinterTurbo would otherwise pick on its own: MPT always
    center-crops a clip to the target aspect ratio (vendor/MoneyPrinterTurbo app/services/video.py::
    _fit_clip_to_canvas) with no parameter for choosing what part of the frame survives -- so when
    that auto-crop would cut off something that matters, a reviewer sets this instead. Not applied
    live; the render stage bakes an actual cropped file from it just before handing the clip to MPT
    (pipeline/stages/render/crop.py)."""
    center_x: float = 0.5     # 0..1, fraction of the source frame, left..right; 0.5 = MPT's own centering
    center_y: float = 0.5     # 0..1, fraction of the source frame, top..bottom
    zoom: float = 1.0         # >=1.0; 1.0 = the widest window that still fills the target aspect
    updated_by: str = ""
    updated_at: str = Field(default_factory=_now)


class Scene(BaseModel):
    id: str = Field(default_factory=_id)
    index: int
    narration: str
    search_terms: list[str] = Field(default_factory=list)
    clip_path: str | None = None            # video/image asset for this scene
    asset_id: str | None = None             # which sourced asset, if any
    clip_reason: str = ""                   # why this clip was chosen
    audio_path: str | None = None           # narration audio for this scene
    duration: float | None = None
    approved: bool = False
    note: str = ""                          # reviewer's own note on this scene -- never read aloud, never
                                             # sent to MoneyPrinterTurbo; editable at Gate 3 (docs/REVIEW_UI.md)
    crop: SceneCrop | None = None           # manual framing override for this scene's clip_path, or None
                                             # for MoneyPrinterTurbo's own automatic center-crop


class Event(BaseModel):
    at: str = Field(default_factory=_now)
    kind: str                               # "transition", "note", "error"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


FactKind = Literal["person", "alias", "date", "location", "address", "institution", "event", "other"]
FactStatus = Literal["confirmed", "unresolved", "conflicting"]


class CaseFact(BaseModel):
    """One atomic fact for the case reference sheet (#6: canonical name, people/aliases, dates/locations,
    addresses/institutions/events, source links, unresolved/conflicting facts). Always human-entered or
    human-confirmed -- nothing in sourcing, vetting or relevance scoring ever creates or edits one of these.
    A search finding something that LOOKS like it matches a fact is not confirmation of that fact (#6);
    the reverse direction is what identity_evidence/case_connection on Asset are for."""
    id: str = Field(default_factory=_id)
    kind: FactKind = "other"
    text: str                               # the fact itself, e.g. "Corazon Amurao" or "July 14, 1966"
    detail: str = ""                        # optional context, e.g. "night-shift student nurse, sole survivor"
    source_links: list[str] = Field(default_factory=list)
    status: FactStatus = "confirmed"
    conflict_note: str = ""                 # what the conflict/uncertainty actually is, required in spirit
                                             # (not enforced) whenever status != "confirmed"
    added_by: str = ""
    added_at: str = Field(default_factory=_now)
    updated_by: str = ""
    updated_at: str = ""


class CaseReferenceSheet(BaseModel):
    """Ground truth for the case (#6), built and edited by a human through the orchestrator's
    case-reference API. Nothing here is ever written by an automated process."""
    canonical_name: str = ""                # the case's canonical name, e.g. "The Dyatlov Pass Incident"
    facts: list[CaseFact] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)


ChecklistStatus = Literal["needed", "candidates_found", "fulfilled", "not_available", "skipped"]


class VisualChecklistItem(BaseModel):
    """One thing the video still needs a visual for (#6: editable generated visual checklist). A fresh
    round can be seeded from approved keywords' visual_needed/entity text as a DRAFT starting point, but
    every field here is human-editable afterward, and nothing automated ever marks one "fulfilled" or
    "not_available" -- finding search candidates for it only moves it to "candidates_found" (#6: never
    treat a search result as confirmation that a need is met); moving to "fulfilled" is always explicit."""
    id: str = Field(default_factory=_id)
    label: str                              # what's needed on screen, e.g. "a period photo of Corazon Amurao"
    linked_keyword_term: str = ""           # which approved keyword's term generated this, if any
    group: QueryGroup = "historical"
    status: ChecklistStatus = "needed"
    note: str = ""
    asset_id: str = ""                      # which approved Asset fulfills this, once a human says so
    added_by: str = ""
    added_at: str = Field(default_factory=_now)
    updated_by: str = ""
    updated_at: str = ""


class ProviderChoice(BaseModel):
    """Which pluggable implementation each stage should use. Names map to the
    registries in pipeline/stages/registry.py."""
    keywords: str = "llm"
    scenes: str = "mpt"
    render: str = "mpt"
    # Asset sources to pull from after keywords are approved, e.g.
    # ["wikipedia", "commons", "pexels"]. Empty = skip sourcing and use library/clips.
    sources: list[str] = Field(default_factory=list)
    # Free-form per-job overrides passed to the stage (e.g. language, aspect).
    options: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    id: str = Field(default_factory=_id)
    subject: str
    slug: str = ""
    state: JobState = JobState.CREATED
    providers: ProviderChoice = Field(default_factory=ProviderChoice)
    niche: Niche | None = None              # one of the 5 core content niches (requested directly), or None
                                             # for a job that behaves exactly as it did before this existed
                                             # (no aesthetic bias in keyword phrasing, no niche_evaluation on
                                             # any asset). See pipeline/niches.py for what each niche means.
    script_style: ScriptStyle | None = None  # one of 4 scriptwriter voices (requested directly), or None for
                                             # the writer's original source-grounded prompt. Independent of
                                             # `niche` above -- see pipeline/stages/scenes/script_styles.py.

    keywords: list[Keyword] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    references: list[TextRef] = Field(default_factory=list)
    # Per-source search notes: what was searched, found, kept, skipped (with reasons) or failed.
    source_notes: list[dict[str, Any]] = Field(default_factory=list)
    script: str = ""
    scenes: list[Scene] = Field(default_factory=list)
    output_path: str | None = None
    # {platform: {"title": ..., "caption": ..., "hashtags": [...]}}, written by the render stage
    # from the final script -- see SOCIAL_POST.md in the project folder for the paste-ready version.
    social_metadata: dict[str, Any] = Field(default_factory=dict)

    # Human feedback given when rejecting a gate; fed into the re-run.
    keyword_feedback: list[str] = Field(default_factory=list)
    asset_feedback: list[str] = Field(default_factory=list)
    scene_feedback: list[str] = Field(default_factory=list)

    # Case ground truth (#6) -- edited only through Orchestrator's case-reference/visual-checklist API,
    # never written by sourcing/vetting/scoring. Available and editable at any point in the job's life,
    # not gated behind a particular state -- it's reference material about the case, not pipeline output.
    case_reference: CaseReferenceSheet = Field(default_factory=CaseReferenceSheet)
    visual_checklist: list[VisualChecklistItem] = Field(default_factory=list)

    error: str | None = None
    failed_from: JobState | None = None     # running state to resume on retry
    events: list[Event] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def model_post_init(self, _ctx: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.subject)

    # ---- convenience -------------------------------------------------
    @property
    def approved_keywords(self) -> list[Keyword]:
        return [k for k in self.keywords if k.approved]

    @property
    def approved_assets(self) -> list[Asset]:
        return [a for a in self.assets if a.status == "approved"]

    @property
    def uses_sources(self) -> bool:
        return bool(self.providers.sources)

    def log(self, kind: str, message: str, **data: Any) -> None:
        self.events.append(Event(kind=kind, message=message, data=data))
        self.updated_at = _now()
