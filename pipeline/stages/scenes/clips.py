"""Where scene video clips come from. Implement `ClipSource` to add scrapers,
Pexels/Pixabay, or AI video generation; the scene stage does not care."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...core.models import Asset, Scene
from ...vetting.rules import STOPWORDS

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


@dataclass
class ClipPick:
    path: str
    reason: str
    asset_id: str | None = None


def _tokens(text: str) -> set[str]:
    # Same stopword list the relevance scorer uses (pipeline/vetting/rules.py), for the same reason: without
    # it, common words like "the"/"and"/"with" count as a "match" between any two pieces of text, which is
    # how a scene ends up paired with a photo that shares no actual subject with it -- a likely contributor
    # to "the photos don't fit the script."
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


class ClipSource(Protocol):
    last_pick: ClipPick | None

    async def fetch(self, scene: Scene, dest_dir: Path, used: set[str]) -> str | None:
        """Return a path to a clip for `scene`, or None if nothing suitable.
        Sets `last_pick` with the reason, so the choice can be logged."""


class LocalFolderClipSource:
    """Serves clips from your own library folder (e.g. filled by your scraper).

    Matching: a file whose name contains any word of the scene's search terms
    wins; otherwise the next unused file in name order is used. Each clip is
    used once per job until the library runs out.
    """

    def __init__(self, library: str | Path):
        self.library = Path(library).resolve()
        self.last_pick: ClipPick | None = None

    async def fetch(self, scene: Scene, dest_dir: Path, used: set[str]) -> str | None:
        files = sorted(p for p in self.library.rglob("*") if p.suffix.lower() in VIDEO_EXTS)
        available = [p for p in files if str(p) not in used]
        self.last_pick = None
        if not available:
            return None
        words = _tokens(" ".join(scene.search_terms))
        for p in available:
            hit = words & _tokens(p.stem)
            if hit:
                self.last_pick = ClipPick(str(p), f"file name matches scene search terms: {sorted(hit)}")
                return str(p)
        self.last_pick = ClipPick(str(available[0]), "no file name matched the scene; used the next unused file in name order")
        return str(available[0])


def _best_match_for_term(term: str, assets: list[Asset]) -> tuple[Asset | None, set[str]]:
    """The asset (if any) among `assets` whose title/description/query shares the most words with
    `term` ALONE, and which words matched -- pure, no state, no narration. Shared by
    AssetClipSource.fetch()'s ordered shot-list pass below and by scene_has_shot_list_coverage()
    (Orchestrator's Pass-2 coverage check, docs/ROADMAP.md "two-pass hybrid"), so both ask the exact
    same question: "does this one query, by itself, match anything in the pool?" """
    term_words = _tokens(term)
    best, best_hit = None, set()
    for a in assets:
        hit = term_words & _tokens(f"{a.title} {a.description} {a.query}")
        if len(hit) > len(best_hit):
            best, best_hit = a, hit
    return best, best_hit


def scene_has_shot_list_coverage(scene: Scene, assets: list[Asset]) -> bool:
    """True if at least one of scene.search_terms (the ordered shot list -- most specific first, then
    broader fallbacks) matches an approved, usable asset ON ITS OWN -- i.e. AssetClipSource.fetch()
    would NOT need to fall through to its generic combined-words/reuse fallback for this scene.

    Used by Orchestrator's Pass-2 coverage check, right before Gate 2 (assets_review) would otherwise
    hand off to Gate 3: a scene with no coverage here is a candidate for automatically searching its
    next, broader shot-list query before asking the reviewer to look at assets again (docs/ROADMAP.md
    "two-pass hybrid" -- Pass 1 only ever searches each scene's most specific query, to avoid searching
    every scene's broad fallback up front under real API rate-limit/cost constraints)."""
    usable = [a for a in assets if a.status == "approved" and (a.vetting is None or a.vetting.usable)]
    if not usable:
        return False
    return any(_best_match_for_term(t, usable)[0] is not None for t in (scene.search_terms or []))


def _hint_matches(a: Asset, scene: Scene, total: int = 0) -> bool:
    hint = str((a.meta or {}).get("position_hint", "")).strip().lower()
    if not hint:
        return False
    if hint.isdigit():
        return int(hint) == scene.index + 1
    return (hint in ("intro", "start", "opening") and scene.index == 0) or \
           (hint in ("end", "outro", "closing") and total > 0 and scene.index == total - 1)


class AssetClipSource:
    """Picks from the assets a human approved at the asset review stop. Nothing
    else can appear in the video. Matching uses words shared between the scene
    (its narration and search terms) and the asset's title, description and
    the search that found it -- with the same stopword list the relevance
    scorer uses, so a shared "the"/"and"/"with" doesn't count as a match.
    Each approved asset is used at most once PER PASS through the pool; if
    there are more scenes than approved assets, the pool is reused from the
    top, deliberately picking the best match again rather than leaving a
    scene with no clip at all (see the "REUSED" reason string below).

    Shot-list resilience (2026-09-26, docs/ROADMAP.md backlog item F): scene.search_terms is now an
    ORDERED list -- the scene's own keyword's most specific query first, then progressively broader
    fallback queries (Orchestrator._apply_keyword_terms_to_scenes, from Keyword.term/.alternatives).
    fetch() tries each query ALONE, in that order, before falling back to the original combined-words
    match -- so a scene prefers an asset that actually matches its own most specific ask over one that
    only turns up when every term is thrown into one bag. This changes nothing for a scene with a single
    search term (the ordered pass and the combined-words fallback compute the identical thing)."""

    def __init__(self, assets: list[Asset]):
        self.assets = [a for a in assets if a.status == "approved" and (a.vetting is None or a.vetting.usable)]
        self.last_pick: ClipPick | None = None
        self.total_scenes = 0          # set by the scene stage, so "end" can mean the last scene

    async def fetch(self, scene: Scene, dest_dir: Path, used: set[str]) -> str | None:
        available = [a for a in self.assets if a.path not in used]
        reused = False
        if not available:
            if not self.assets:
                self.last_pick = None
                return None
            # Every approved asset has already been used once elsewhere in this video. Leaving this
            # scene with no clip at all would silently drop it from what's sent to MoneyPrinterTurbo
            # (render/mpt.py only sends scenes that HAVE a clip_path) -- so the video would have fewer
            # clips than narration segments, and MoneyPrinterTurbo has to stretch or loop whatever clips
            # it does have to cover the gap: an unpredictable repeat with no connection to what's
            # actually being said at that point ("the photos just keep running in a circle"). Reusing
            # the single best-matching approved asset again instead is a deliberate, explained repeat --
            # approving more assets is what actually avoids it; see the reason string below.
            available = self.assets
            reused = True
        self.last_pick = None
        # A URL-list entry can say where it belongs: a scene number ("2") or "intro" / "end".
        hinted = [a for a in available if _hint_matches(a, scene, self.total_scenes)]
        if hinted:
            self.last_pick = ClipPick(hinted[0].path, f"you placed it here in your URL list (position '{hinted[0].meta.get('position_hint')}')", hinted[0].id)
            return hinted[0].path
        # Assets the user reserved for a specific position stay out of the general pool while
        # other assets remain, so they are still free for their own scene.
        available = [a for a in available if not str((a.meta or {}).get("position_hint", "")).strip()] or available

        def reason_for(reason: str) -> str:
            if reused:
                reason += " -- REUSED: every approved asset was already used once elsewhere; approve more to avoid repeats"
            return reason

        # Shot list, specific to broad: match on each query alone, in order, and stop at the first one
        # that hits ANY approved asset. Not narration -- this pass asks "does anything match THIS query",
        # not "does anything relate to the scene overall" (that's the combined-words fallback below).
        shot_list = scene.search_terms or [""]
        for rank, term in enumerate(shot_list):
            best, best_hit = _best_match_for_term(term, available)
            if best is not None:
                which = "its most specific query" if rank == 0 else f"fallback query {rank + 1} of {len(shot_list)}"
                self.last_pick = ClipPick(best.path, reason_for(f"approved asset matches the scene's {which} "
                                          f"('{term}'): {sorted(best_hit)}"), best.id)
                return best.path

        # Generic fallback: no single shot-list query matched anything on its own -- score every term
        # together with the narration (the original, pre-shot-list behavior), then the next-unused asset.
        scene_words = _tokens(scene.narration + " " + " ".join(scene.search_terms))
        best, best_hit = None, set()
        for a in available:
            hit = scene_words & _tokens(f"{a.title} {a.description} {a.query}")
            if len(hit) > len(best_hit):
                best, best_hit = a, hit
        if best is not None:
            reason = f"approved asset shares words with the scene: {sorted(best_hit)}"
        else:
            best = available[0]
            reason = "no word overlap with any approved asset; used the next unused approved asset"
        self.last_pick = ClipPick(best.path, reason_for(reason), best.id)
        return best.path
