"""Scene stage: script + per-scene terms, clips and (optionally) narration
audio, using MoneyPrinterTurbo for the LLM/TTS work."""
from __future__ import annotations

import re

from ...core.models import Job, Scene
from ..base import SceneResult, StageContext
from ..mpt_client import MptClient, MptError
from .clips import ClipSource


def split_scenes(script: str, sentences_per_scene: int = 2) -> list[str]:
    """Paragraph breaks win; otherwise group sentences."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", script.strip()) if p.strip()]
    if len(paras) > 1:
        return paras
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script.strip()) if s.strip()]
    return [
        " ".join(sentences[i:i + sentences_per_scene])
        for i in range(0, len(sentences), sentences_per_scene)
    ]


class MptSceneStage:
    def __init__(self, client: MptClient, clips: ClipSource, voice_name: str = "", language: str = "",
                 generate_audio: bool = False, paragraphs: int = 3):
        self.client = client
        self.clips = clips
        self.voice_name = voice_name
        self.language = language
        self.generate_audio = generate_audio
        self.paragraphs = paragraphs

    async def run(self, job: Job, ctx: StageContext) -> SceneResult:
        keywords = [k.term for k in job.approved_keywords]
        prompt = f"Work these approved keywords in naturally: {', '.join(keywords)}."
        if job.scene_feedback:
            prompt += " Reviewer notes on the previous draft: " + " | ".join(job.scene_feedback)

        script = await self.client.script(job.subject, self.language, self.paragraphs, prompt)
        texts = split_scenes(script)
        if not texts:
            raise MptError("MoneyPrinterTurbo returned an empty script")

        used: set[str] = set()
        scenes: list[Scene] = []
        for i, text in enumerate(texts):
            scene = Scene(index=i, narration=text, search_terms=[keywords[i % len(keywords)]] if keywords else [])
            scene.clip_path = await self.clips.fetch(scene, ctx.assets_dir, used)
            if scene.clip_path:
                used.add(scene.clip_path)
            if self.generate_audio and self.voice_name:
                scene.audio_path = await self._audio(job, scene, ctx)
            scenes.append(scene)
        return SceneResult(script=script, scenes=scenes)

    async def _audio(self, job: Job, scene: Scene, ctx: StageContext) -> str | None:
        try:
            task_id = await self.client.create_audio(scene.narration, self.voice_name, self.language)
            await self.client.wait_task(task_id)
            dest = ctx.assets_dir / f"scene_{scene.index:02d}.mp3"
            await self.client.download(f"/tasks/{task_id}/audio.mp3", dest)
            return str(dest)
        except Exception as exc:  # audio preview is a nicety; do not fail the whole stage
            job.log("note", f"audio for scene {scene.index} failed: {exc}")
            return None
