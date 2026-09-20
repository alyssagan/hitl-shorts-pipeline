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
from .writer import ScriptWriter

# MoneyPrinterTurbo rejects a script prompt longer than 2000 characters (400 "field required"),
# (tracked in docs/KNOWN_LIMITATIONS.md #1) so the source text we ground on has to leave room for the instructions and reviewer notes.
GROUNDING_CHARS = 1000


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
    """Script + scenes. The script comes from our own ScriptWriter when one is configured
    (longer, better grounded); otherwise from MoneyPrinterTurbo's script step."""
    actor = ai("moneyprinterturbo-script", model="(set in mpt-config.toml)")

    def __init__(self, client: MptClient, clips: ClipSource, voice_name: str = "", language: str = "",
                 generate_audio: bool = False, paragraphs: int = 3, writer: ScriptWriter | None = None):
        self.client = client
        self.writer = writer
        if writer:
            self.actor = ai("script-writer", model=writer.model)
        self.clips = clips
        self.voice_name = voice_name
        self.language = language
        self.generate_audio = generate_audio
        self.paragraphs = paragraphs
        self.last_trace: dict = {}

    def _grounding(self, job: Job) -> str:
        """Source text for the script prompt. The character budget is split evenly across the
        sources, so one long article can't crowd the others out entirely."""
        bodies = []
        for r in job.references:
            try:
                text = Path(r.path).read_text(encoding="utf-8")
            except OSError:
                continue
            bodies.append((r, text.split("\n\n", 1)[-1]))
        if not bodies:
            return ""
        share = max(200, GROUNDING_CHARS // len(bodies))
        parts = [f'From "{r.title}" ({r.url}):\n{body[:share]}' for r, body in bodies]
        return "\n\n".join(parts)

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

        if self.writer:
            script, self.last_trace = await self.writer.write(job, keywords)
        else:
            script = await self.client.script(job.subject, self.language, self.paragraphs, prompt)
        texts = split_scenes(script)
        if not texts:
            raise MptError("MoneyPrinterTurbo returned an empty script")

        if hasattr(clips, "total_scenes"):
            clips.total_scenes = len(texts)
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
