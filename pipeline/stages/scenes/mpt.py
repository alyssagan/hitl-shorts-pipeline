"""Scene stage: script + per-scene terms, clips and (optionally) narration
audio, using MoneyPrinterTurbo for the LLM/TTS work."""
from __future__ import annotations

import re

from pathlib import Path

from ...core.decisions import ai
from ...core.models import Job, Scene
from ..base import SceneResult, StageContext
from ..mpt_client import MptClient, MptError
from .clips import AssetClipSource, ClipSource

GROUNDING_CHARS = 4000


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
    actor = ai("moneyprinterturbo-script", model="(set in mpt-config.toml)")

    def __init__(self, client: MptClient, clips: ClipSource, voice_name: str = "", language: str = "",
                 generate_audio: bool = False, paragraphs: int = 3):
        self.client = client
        self.clips = clips
        self.voice_name = voice_name
        self.language = language
        self.generate_audio = generate_audio
        self.paragraphs = paragraphs
        self.last_trace: dict = {}

    def _grounding(self, job: Job) -> str:
        parts = []
        for r in job.references:
            try:
                text = Path(r.path).read_text(encoding="utf-8")
            except OSError:
                continue
            body = text.split("\n\n", 1)[-1]
            parts.append(f'From "{r.title}" ({r.url}):\n{body}')
        return "\n\n".join(parts)[:GROUNDING_CHARS]

    async def run(self, job: Job, ctx: StageContext) -> SceneResult:
        keywords = [k.term for k in job.approved_keywords]
        prompt = f"Work these approved keywords in naturally: {', '.join(keywords)}."
        grounding = self._grounding(job)
        if grounding:
            prompt += ("\nBase every factual claim ONLY on the source text below. Do not add facts that are not in it.\n"
                       + grounding)
        if job.scene_feedback:
            prompt += " Reviewer notes on the previous draft: " + " | ".join(job.scene_feedback)
        clips = AssetClipSource(job.assets) if job.uses_sources else self.clips
        self.last_trace = {
            "script_prompt": prompt, "paragraphs": self.paragraphs, "keywords": keywords,
            "grounded_on": [{"title": r.title, "url": r.url} for r in job.references] if grounding else [],
            "clip_source": "approved assets" if job.uses_sources else "library folder",
            "feedback_used": list(job.scene_feedback),
        }

        script = await self.client.script(job.subject, self.language, self.paragraphs, prompt)
        texts = split_scenes(script)
        if not texts:
            raise MptError("MoneyPrinterTurbo returned an empty script")

        used: set[str] = set()
        scenes: list[Scene] = []
        for i, text in enumerate(texts):
            scene = Scene(index=i, narration=text, search_terms=[keywords[i % len(keywords)]] if keywords else [])
            scene.clip_path = await clips.fetch(scene, ctx.assets_dir, used)
            if scene.clip_path:
                used.add(scene.clip_path)
                pick = getattr(clips, "last_pick", None)
                if pick:
                    scene.asset_id, scene.clip_reason = pick.asset_id, pick.reason
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
