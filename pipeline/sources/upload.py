"""Build an Asset from a file dropped straight onto a scene at Gate 3 (pipeline/api/app.py's scene_upload ->
pipeline/core/orchestrator.add_scene_asset) -- as opposed to a source adapter's own batch fetch(), this is a
one-off add outside a normal sourcing round.

No sidecar metadata is possible for a one-off upload, so -- exactly like pipeline/sources/folder.py's
un-sidecared files -- it is imported with no license, which vetting flags as high risk (unknown license)
until a human's note says it's fine to use. This is deliberate: dropping a file in doesn't skip the same
risk check every other asset gets, it just skips the license paperwork a scraped web asset would have.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..core.models import Asset
from .base import MIME_TO_EXT, safe_name

EXT_TO_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
               "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}


def asset_from_upload(data: bytes, filename: str, dest_dir: Path, project_dir: Path, note: str = "") -> Asset:
    """Named by a hash prefix (not a sequential index, unlike the batch sources) so two adds racing each
    other can never overwrite one another -- we already have the full bytes in hand, unlike a source that
    names its file before a subprocess/HTTP download has finished."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    mime = EXT_TO_MIME.get(ext)
    if not mime:
        raise ValueError(f"'{filename}': format '.{ext or '?'}' can't be used by the renderer "
                          f"(use one of: {', '.join(sorted(MIME_TO_EXT.values()))})")
    stem = safe_name(filename.rsplit(".", 1)[0]) if "." in filename else safe_name(filename)
    sha = hashlib.sha256(data).hexdigest()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{sha[:12]}-{stem}.{ext}"
    dest.write_bytes(data)
    return Asset(
        source="upload", kind="video" if mime.startswith("video") else "image",
        path=str(dest), rel_path=os.path.relpath(dest, project_dir),
        source_url=f"upload://{filename}", page_url="", title=filename, description=note,
        query="(dragged in)", author="", license="", license_url="", attribution="",
        mime=mime, sha256=sha, meta={"uploaded_filename": filename})
