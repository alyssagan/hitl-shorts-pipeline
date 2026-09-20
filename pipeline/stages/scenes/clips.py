"""Where scene video clips come from. Implement `ClipSource` to add scrapers,
Pexels/Pixabay, or AI video generation; the scene stage does not care."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...core.models import Asset, Scene

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


@dataclass
class ClipPick:
    path: str
    reason: str
    asset_id: str | None = None


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


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
    the search that found it."""

    def __init__(self, assets: list[Asset]):
        self.assets = [a for a in assets if a.status == "approved" and (a.vetting is None or a.vetting.usable)]
        self.last_pick: ClipPick | None = None
        self.total_scenes = 0          # set by the scene stage, so "end" can mean the last scene

    async def fetch(self, scene: Scene, dest_dir: Path, used: set[str]) -> str | None:
        available = [a for a in self.assets if a.path not in used]
        self.last_pick = None
        if not available:
            return None
        # A URL-list entry can say where it belongs: a scene number ("2") or "intro" / "end".
        hinted = [a for a in available if _hint_matches(a, scene, self.total_scenes)]
        if hinted:
            self.last_pick = ClipPick(hinted[0].path, f"you placed it here in your URL list (position '{hinted[0].meta.get('position_hint')}')", hinted[0].id)
            return hinted[0].path
        # Assets the user reserved for a specific position stay out of the general pool while
        # other assets remain, so they are still free for their own scene.
        available = [a for a in available if not str((a.meta or {}).get("position_hint", "")).strip()] or available
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
        self.last_pick = ClipPick(best.path, reason, best.id)
        return best.path
