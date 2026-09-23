"""Render stage: hand the approved, ordered scenes to MoneyPrinterTurbo.

Uses MoneyPrinterTurbo's existing `local` video source with the `sequential`
concat mode, which takes your clips in exactly the order supplied -- that is
what makes the human reorder/approve step meaningful.

Also forwards MoneyPrinterTurbo's own retention features -- caption style/animation,
background music, transitions -- which the pipeline used to leave at MoneyPrinterTurbo's
bare defaults (unstyled "sentence" captions, no animation, no music). All still images
already get MoneyPrinterTurbo's automatic slow zoom (vendor/MoneyPrinterTurbo
app/services/video.py::render_image_zoom_video) with no configuration needed here.

After a successful render, optionally asks MoneyPrinterTurbo to write platform-ready
title/caption/hashtags from the final script (its own /api/v1/social-metadata, see
mpt_client.py) for each configured platform -- best-effort, never fails the render --
and writes them to SOCIAL_POST.md in the project folder, paste-ready.

Any scene with a manual crop (Scene.crop, set at Gate 3) gets its clip baked into an
actually-cropped copy first -- see crop.py's module docstring for why that has to
happen here rather than being passed through to MoneyPrinterTurbo.
"""
from __future__ import annotations

from pathlib import Path

from ...core.models import Job
from ..base import RenderResult, StageContext
from ...core import joblog
from ..mpt_client import MptClient, MptError
from .crop import CropError, Runner as CropRunner, apply_scene_crop, run_subprocess as run_crop_subprocess

# Platform label used in SOCIAL_POST.md; keys must match MoneyPrinterTurbo's own
# SOCIAL_PLATFORMS (app/services/llm.py) -- an unknown key there just 400s, caught below.
PLATFORM_LABELS = {
    "tiktok": "TikTok", "youtube_shorts": "YouTube Shorts",
    "instagram_reels": "Instagram Reels", "facebook_reels": "Facebook Reels",
}


class MptRenderStage:
    def __init__(self, client: MptClient, voice_name: str = "", language: str = "",
                 aspect: str = "9:16", clip_seconds: int = 5,
                 clip_root_host: str = "", clip_root_mpt: str = "",
                 path_map: list[tuple[str, str]] | None = None,
                 # Retention styling forwarded to MoneyPrinterTurbo's /api/v1/videos. Every one of these
                 # defaults to "don't override MoneyPrinterTurbo's own default" (empty string / 0 / None,
                 # the same sentinel convention `voice_name` already used below) -- config/pipeline.toml
                 # is where this pipeline's own recommended defaults actually live (pipeline/stages/registry.py).
                 subtitle_display_mode: str = "", subtitle_animation: str = "",
                 font_name: str = "", font_size: int = 0, text_fore_color: str = "",
                 stroke_color: str = "", stroke_width: float = 0.0, subtitle_position: str = "",
                 subtitle_background_enabled: bool | None = None, subtitle_background_color: str = "",
                 video_transition_mode: str = "", video_fit_mode: str = "",
                 voice_volume: float = 0.0, voice_rate: float = 0.0,
                 bgm_type: str = "", bgm_volume: float = 0.0, custom_bgm_file: str = "",
                 # Platform copy generation (mpt_client.py::MptClient.social_metadata). Empty = disabled.
                 social_platforms: tuple[str, ...] = (),
                 # crop.py's ffmpeg runner, injectable so tests never actually invoke ffmpeg.
                 crop_runner: CropRunner = run_crop_subprocess):
        self.client = client
        self.voice_name = voice_name
        self.language = language
        self.aspect = aspect
        self.clip_seconds = clip_seconds
        # When MoneyPrinterTurbo runs in Docker, clips are mounted at a different
        # path than on the host. Map host prefix -> container prefix.
        self.path_map = list(path_map or [])
        if clip_root_host:
            self.path_map.append((clip_root_host, clip_root_mpt))
        self.subtitle_display_mode = subtitle_display_mode
        self.subtitle_animation = subtitle_animation
        self.font_name = font_name
        self.font_size = font_size
        self.text_fore_color = text_fore_color
        self.stroke_color = stroke_color
        self.stroke_width = stroke_width
        self.subtitle_position = subtitle_position
        self.subtitle_background_enabled = subtitle_background_enabled
        self.subtitle_background_color = subtitle_background_color
        self.video_transition_mode = video_transition_mode
        self.video_fit_mode = video_fit_mode
        self.voice_volume = voice_volume
        self.voice_rate = voice_rate
        self.bgm_type = bgm_type
        self.bgm_volume = bgm_volume
        self.custom_bgm_file = custom_bgm_file
        self.social_platforms = tuple(social_platforms)
        self.crop_runner = crop_runner

    def _to_mpt_path(self, p: str) -> str:
        for host, mpt in self.path_map:
            if host and p.startswith(host):
                return mpt + p[len(host):]
        return p

    def _extra_params(self) -> dict:
        """Retention-styling fields, included only when set (see the sentinel note above)."""
        out: dict = {}
        for key, val in (
            ("subtitle_display_mode", self.subtitle_display_mode), ("subtitle_animation", self.subtitle_animation),
            ("font_name", self.font_name), ("text_fore_color", self.text_fore_color),
            ("stroke_color", self.stroke_color), ("subtitle_position", self.subtitle_position),
            ("subtitle_background_color", self.subtitle_background_color),
            ("video_transition_mode", self.video_transition_mode), ("video_fit_mode", self.video_fit_mode),
            ("bgm_type", self.bgm_type), ("custom_bgm_file", self.custom_bgm_file),
        ):
            if val:
                out[key] = val
        for key, val in (
            ("font_size", self.font_size), ("stroke_width", self.stroke_width),
            ("voice_volume", self.voice_volume), ("voice_rate", self.voice_rate), ("bgm_volume", self.bgm_volume),
        ):
            if val:
                out[key] = val
        if self.subtitle_background_enabled is not None:
            out["subtitle_background_enabled"] = self.subtitle_background_enabled
        return out

    async def _social_metadata(self, job: Job, script: str) -> dict[str, dict]:
        """Platform-ready title/caption/hashtags, one call per configured platform. Best-effort: a
        platform that errors is skipped (logged), never fails the render -- this is a bonus on top of
        a finished video, not something the video depends on."""
        out: dict[str, dict] = {}
        for platform in self.social_platforms:
            try:
                out[platform] = await self.client.social_metadata(job.subject, script, language=self.language, platform=platform)
            except Exception as exc:
                joblog.warn("render", f"social metadata for {platform} failed, skipping", error=str(exc))
        return out

    def _write_social_post(self, ctx: StageContext, social: dict[str, dict]) -> None:
        if not social or not ctx.project_dir:
            return
        lines = ["# Social post copy", "", "Paste-ready. Generated by MoneyPrinterTurbo from the final script.", ""]
        for platform, meta in social.items():
            label = PLATFORM_LABELS.get(platform, platform)
            hashtags = " ".join(meta.get("hashtags") or [])
            lines += [f"## {label}", "", f"**Title:** {meta.get('title', '')}", "",
                      f"**Caption:**  \n{meta.get('caption', '')}", "", f"**Hashtags:** {hashtags}", ""]
        (Path(ctx.project_dir) / "SOCIAL_POST.md").write_text("\n".join(lines), encoding="utf-8")

    async def _resolve_clip(self, scene, assets_by_id: dict, ctx: StageContext) -> str:
        """scene.clip_path, or a freshly-cropped copy of it if the reviewer set scene.crop (see
        crop.py's module docstring for why MPT itself can't be handed a crop instead). Best-effort:
        an ffmpeg failure is logged and falls back to the uncropped clip rather than failing the
        whole render over one scene's framing."""
        if scene.crop is None or not ctx.project_dir:
            return scene.clip_path
        try:
            return await apply_scene_crop(scene, assets_by_id.get(scene.asset_id), target_aspect=self.aspect,
                                          out_dir=Path(ctx.project_dir) / "cropped", runner=self.crop_runner)
        except CropError as exc:
            joblog.warn("render", f"scene {scene.id} crop failed, using the uncropped clip instead", error=str(exc))
            return scene.clip_path

    async def run(self, job: Job, ctx: StageContext) -> RenderResult:
        scenes = sorted(job.scenes, key=lambda s: s.index)
        assets_by_id = {a.id: a for a in job.assets}
        clips = [await self._resolve_clip(s, assets_by_id, ctx) for s in scenes if s.clip_path]
        if not clips:
            raise MptError("no scene has a clip; add clips to the library or edit scenes before rendering")

        script = "\n\n".join(s.narration for s in scenes)
        params = {
            "video_subject": job.subject,
            "video_script": script,
            "video_terms": [t for s in scenes for t in s.search_terms],
            "video_aspect": self.aspect,
            "video_source": "local",
            "video_concat_mode": "sequential",
            "video_clip_duration": self.clip_seconds,
            "video_language": self.language,
            "video_materials": [{"provider": "local", "url": self._to_mpt_path(c), "duration": 0} for c in clips],
            **self._extra_params(),
        }
        if self.voice_name:          # an empty voice_name would override MoneyPrinterTurbo's default and break TTS
            params["voice_name"] = self.voice_name
        joblog.info("render", f"sending {len(clips)} clip(s) and {len(script.split())} words to MoneyPrinterTurbo",
                    voice=self.voice_name or "(default)", aspect=self.aspect,
                    captions=self.subtitle_display_mode or "(default)", bgm=self.bgm_type or "(off)")
        task_id = await self.client.create_video(params)
        job.log("note", f"MoneyPrinterTurbo render task {task_id} started", task_id=task_id)
        done = await self.client.wait_task(task_id)
        videos = done.get("videos") or done.get("combined_videos") or []
        if not videos:
            raise MptError(f"task {task_id} finished but returned no video")
        dest = Path(ctx.assets_dir) / "final.mp4"
        await self.client.download(videos[0], dest)
        joblog.info("render", f"video saved ({dest.stat().st_size // 1024} KB)", file=str(dest))

        social = await self._social_metadata(job, script) if self.social_platforms else {}
        if social:
            self._write_social_post(ctx, social)
            joblog.info("render", f"social post copy written for {', '.join(social)}")
        return RenderResult(output_path=str(dest), social_metadata=social)
