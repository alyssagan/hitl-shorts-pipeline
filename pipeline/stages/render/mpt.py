"""Render stage: hand the approved, ordered scenes to MoneyPrinterTurbo.

Uses MoneyPrinterTurbo's existing `local` video source with the `sequential`
concat mode, which takes your clips in exactly the order supplied -- that is
what makes the human reorder/approve step meaningful.
"""
from __future__ import annotations

from pathlib import Path

from ...core.models import Job
from ..base import StageContext
from ..mpt_client import MptClient, MptError


class MptRenderStage:
    def __init__(self, client: MptClient, voice_name: str = "", language: str = "",
                 aspect: str = "9:16", clip_seconds: int = 5,
                 clip_root_host: str = "", clip_root_mpt: str = "",
                 path_map: list[tuple[str, str]] | None = None):
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

    def _to_mpt_path(self, p: str) -> str:
        for host, mpt in self.path_map:
            if host and p.startswith(host):
                return mpt + p[len(host):]
        return p

    async def run(self, job: Job, ctx: StageContext) -> str:
        scenes = sorted(job.scenes, key=lambda s: s.index)
        clips = [s.clip_path for s in scenes if s.clip_path]
        if not clips:
            raise MptError("no scene has a clip; add clips to the library or edit scenes before rendering")

        params = {
            "video_subject": job.subject,
            "video_script": "\n\n".join(s.narration for s in scenes),
            "video_terms": [t for s in scenes for t in s.search_terms],
            "video_aspect": self.aspect,
            "video_source": "local",
            "video_concat_mode": "sequential",
            "video_clip_duration": self.clip_seconds,
            "video_language": self.language,
            "video_materials": [{"provider": "local", "url": self._to_mpt_path(c), "duration": 0} for c in clips],
        }
        if self.voice_name:          # an empty voice_name would override MoneyPrinterTurbo's default and break TTS
            params["voice_name"] = self.voice_name
        task_id = await self.client.create_video(params)
        job.log("note", f"MoneyPrinterTurbo render task {task_id} started", task_id=task_id)
        done = await self.client.wait_task(task_id)
        videos = done.get("videos") or done.get("combined_videos") or []
        if not videos:
            raise MptError(f"task {task_id} finished but returned no video")
        dest = Path(ctx.assets_dir) / "final.mp4"
        await self.client.download(videos[0], dest)
        return str(dest)
