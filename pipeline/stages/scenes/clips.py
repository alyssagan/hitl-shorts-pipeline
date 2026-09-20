"""Where scene video clips come from. Implement `ClipSource` to add scrapers,
Pexels/Pixabay, or AI video generation; the scene stage does not care."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from ...core.models import Scene

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


class ClipSource(Protocol):
    async def fetch(self, scene: Scene, dest_dir: Path, used: set[str]) -> str | None:
        """Return a path to a clip for `scene`, or None if nothing suitable."""


class LocalFolderClipSource:
    """Serves clips from your own library folder (e.g. filled by your scraper).

    Matching: a file whose name contains any word of the scene's search terms
    wins; otherwise the next unused file in name order is used. Each clip is
    used once per job until the library runs out.
    """

    def __init__(self, library: str | Path):
        self.library = Path(library).resolve()

    async def fetch(self, scene: Scene, dest_dir: Path, used: set[str]) -> str | None:
        files = sorted(p for p in self.library.rglob("*") if p.suffix.lower() in VIDEO_EXTS)
        available = [p for p in files if str(p) not in used]
        if not available:
            return None
        words = {w for t in scene.search_terms for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2}
        for p in available:
            name_words = set(re.findall(r"[a-z0-9]+", p.stem.lower()))
            if words & name_words:
                return str(p)
        return str(available[0])
