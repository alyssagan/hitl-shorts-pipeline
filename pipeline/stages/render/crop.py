"""Bakes a human-chosen crop (Scene.crop) into an actual cropped file before MoneyPrinterTurbo ever
sees it.

MoneyPrinterTurbo always fits a clip to the target aspect ratio itself (vendor/MoneyPrinterTurbo
app/services/video.py::_fit_clip_to_canvas) -- but that crop is always centered, with no parameter
for choosing what part of the frame survives (vendor/MoneyPrinterTurbo app/models/schema.py::
MaterialInfo only has provider/url/duration/source_info, nothing crop-shaped). So when a reviewer
wants control over the framing -- MPT's auto-center-crop cut off a face, a caption, the actual
subject of the photo -- this pipeline crops its own copy of the clip first. MPT's later auto-fit
then finds the aspect already matches exactly and is a no-op (the "Exact aspect-ratio matches"
branch in _fit_clip_to_canvas).

Works on both stills and video clips with the same ffmpeg crop filter -- ffmpeg treats a single
image input as a one-frame stream, so the same command produces a cropped still or a cropped clip.
Uses the same injectable-runner pattern as pipeline/sources/urls.py::run_subprocess, so tests can
substitute a fake runner instead of actually invoking ffmpeg.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

from ...core.models import Asset, Scene

Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


class CropError(RuntimeError):
    pass


async def run_subprocess(args: list[str], timeout: float = 300) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"timed out after {timeout:.0f}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def aspect_ratio(aspect: str) -> float:
    """Parse "9:16" -> 0.5625 (width/height). Falls back to 9:16 on anything unparseable -- a
    cosmetic default is never worth failing a render over."""
    try:
        w_s, h_s = aspect.split(":")
        w, h = float(w_s), float(h_s)
        if w > 0 and h > 0:
            return w / h
    except Exception:
        pass
    return 9 / 16


def crop_box(source_width: int, source_height: int, target_aspect: float, *,
             center_x: float, center_y: float, zoom: float) -> tuple[int, int, int, int]:
    """The (x, y, w, h) window ffmpeg should cut out of a source_width x source_height frame so the
    result exactly matches target_aspect, positioned by a human's center_x/center_y/zoom instead of
    MPT's own always-centered auto-crop. zoom=1.0 is the smallest window that still covers the full
    target aspect (i.e. exactly MPT's own default framing, just made explicit); zoom>1 tightens the
    window, revealing less of the source. center_x/center_y are 0..1 fractions of the source frame
    (0.5, 0.5 = centered, MPT's own default). The window is always clamped to stay inside the source
    frame, so an off-center zoom near an edge just slides back in rather than sampling outside it."""
    zoom = max(zoom, 1.0)
    source_aspect = source_width / source_height
    if source_aspect > target_aspect:
        # source is relatively wider than the target -- height is the limiting dimension
        base_h = float(source_height)
        base_w = source_height * target_aspect
    else:
        base_w = float(source_width)
        base_h = source_width / target_aspect
    w = max(1, min(source_width, round(base_w / zoom)))
    h = max(1, min(source_height, round(base_h / zoom)))
    x = round(center_x * source_width - w / 2)
    y = round(center_y * source_height - h / 2)
    x = max(0, min(source_width - w, x))
    y = max(0, min(source_height - h, y))
    return x, y, w, h


async def apply_scene_crop(scene: Scene, asset: Asset | None, *, target_aspect: str, out_dir: Path,
                            runner: Runner = run_subprocess) -> str:
    """Writes a cropped copy of scene.clip_path per scene.crop into out_dir and returns its path.
    Falls back to scene.clip_path unchanged -- letting MPT's own automatic center-crop handle it --
    whenever there's nothing to act on: no crop set, no clip, the asset (or its width/height) isn't
    known, or the crop window turns out to cover the whole frame anyway. Never raises for any of
    those; only an actual ffmpeg failure raises CropError, which the render stage treats as
    best-effort and logs rather than failing the whole render over a crop."""
    if scene.crop is None or not scene.clip_path:
        return scene.clip_path
    if asset is None or not asset.width or not asset.height:
        return scene.clip_path
    x, y, w, h = crop_box(asset.width, asset.height, aspect_ratio(target_aspect),
                          center_x=scene.crop.center_x, center_y=scene.crop.center_y, zoom=scene.crop.zoom)
    if w >= asset.width and h >= asset.height:
        return scene.clip_path   # the window covers the whole source frame -- nothing to crop
    src = Path(scene.clip_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{scene.id}{src.suffix or ('.mp4' if asset.kind == 'video' else '.jpg')}"
    args = ["ffmpeg", "-y", "-i", str(src), "-vf", f"crop={w}:{h}:{x}:{y}"]
    if asset.kind == "video":
        args += ["-c:a", "copy"]
    args.append(str(dest))
    code, _out, err = await runner(args)
    if code != 0:
        raise CropError(f"ffmpeg crop failed for scene {scene.id}: {err.strip()[-500:]}")
    return str(dest)
