"""Scene stage: script + per-scene terms, clips and (optionally) narration
audio, using MoneyPrinterTurbo for the LLM/TTS work.

Reordered 2026-09-25 (docs/PIPELINE_STAGES.md): script writing and clip/audio matching used to
happen together, in one `run()`, after keywords/assets already existed. They are now two separate
methods, called at two different points in the pipeline:

  write_script()  -- SCRIPT_RUNNING (now near the FRONT: right after `start`). Pulls research text
                      straight from job.subject (no keyword needed for that -- Wikipedia only, same
                      as the old "research" query group always routed to), writes the narration
                      grounded on it, splits into scenes. No clip/audio yet.
  run()            -- SCENES_RUNNING (still near the back, after Gate 2 asset review). The script and
                      scenes already exist by now -- this only matches a clip (from approved assets,
                      or the local library) to each one, using the per-scene search term(s)
                      KEYWORDS_RUNNING generated from that scene's own narration, and optionally
                      renders audio. Never rewrites job.script or a scene's narration.
"""
from __future__ import annotations

import re

from pathlib import Path
from typing import Callable

from ...core import joblog
from ...core.decisions import ai
from ...core.models import Job, Scene, TextRef
from ...sources.base import LoggedHttp, SourceAdapter, SourceContext
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
                 generate_audio: bool = False, paragraphs: int = 3, writer: ScriptWriter | None = None,
                 research_source: Callable[[], SourceAdapter] | None = None, user_agent: str = "",
                 research_transport=None):
        self.client = client
        self.writer = writer
        if writer:
            self.actor = ai("script-writer", model=writer.model)
        self.clips = clips
        self.voice_name = voice_name
        self.language = language
        self.generate_audio = generate_audio
        self.paragraphs = paragraphs
        # A zero-arg factory for a fresh "wikipedia" SourceAdapter (Registry.source("wikipedia")'s
        # factory, resolved once at registry-build time) -- None when this job's providers.sources
        # never included "wikipedia", in which case write_script() below skips research entirely,
        # same as the old "research" query group did when nothing routed to it.
        self.research_source = research_source
        self.user_agent = user_agent
        # Same httpx transport SourcingStage uses (Registry.source_transport -- tests inject a mock
        # transport there; production leaves it None and httpx opens a real connection). Omitting this
        # here (as an earlier version of this method did) meant a real MptSceneStage's research fetch
        # always used a real network connection even under test, unlike every other source fetch.
        self.research_transport = research_transport
        self.last_trace: dict = {}

    def _grounding(self, references: list[TextRef]) -> str:
        """Source text for the script prompt. The character budget is split evenly across the
        sources, so one long article can't crowd the others out entirely."""
        bodies = []
        for r in references:
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

    async def _fetch_research(self, job: Job, ctx: StageContext) -> list[TextRef]:
        """Background text to ground the script on, pulled straight from job.subject -- before any
        keyword exists (keywords are now derived FROM the script, not the other way around). Never
        raises: grounding is a nicety, not a requirement -- the writer already writes from general
        knowledge (with a caveat baked into its prompt) when there's nothing to ground on."""
        if job.references or self.research_source is None or ctx.project_dir is None:
            return job.references
        try:
            adapter = self.research_source()
            src_dir = ctx.project_dir / "sources" / "wikipedia"
            (src_dir / "files").mkdir(parents=True, exist_ok=True)
            sctx = SourceContext(project_dir=ctx.project_dir, dir=src_dir, subject=job.subject,
                                 http=LoggedHttp(src_dir, "wikipedia", user_agent=self.user_agent,
                                                 transport=self.research_transport))
            res = await adapter.fetch([job.subject], sctx)
            joblog.info("script", f"research: found {len(res.references)} article(s) for '{job.subject}'")
            return res.references
        except Exception as exc:  # research is best-effort; a failed/misconfigured source should never block the script
            joblog.warn("script", f"research fetch failed, writing without grounding: {exc}")
            return []

    async def write_script(self, job: Job, ctx: StageContext) -> SceneResult:
        """NEW, front of the pipeline (SCRIPT_RUNNING). Narration + scene split only -- no clip_path/
        asset_id/audio yet; run() below fills those in once keywords/assets exist. Never mutates
        `job` directly (same convention every other stage follows) -- the freshly-fetched research
        text is returned on SceneResult.references for Orchestrator._apply_script to persist onto
        job.references, and is passed explicitly into writer.write() below rather than relying on
        job.references (which doesn't have it yet at this point)."""
        references = await self._fetch_research(job, ctx)
        grounding = self._grounding(references)
        prompt = ""
        if grounding:
            prompt += ("Base every factual claim ONLY on the source text below. Do not add facts that are not in it.\n"
                       + grounding)
        if job.script_feedback:
            prompt += " Reviewer notes on the previous draft: " + " | ".join(job.script_feedback)
        self.last_trace = {
            "script_prompt": prompt, "paragraphs": self.paragraphs,
            "grounded_on": [{"title": r.title, "url": r.url} for r in references] if grounding else [],
            "feedback_used": list(job.script_feedback),
        }
        if self.writer:
            script, self.last_trace = await self.writer.write(job, [], references)
        else:
            script = await self.client.script(job.subject, self.language, self.paragraphs, prompt)
        texts = split_scenes(script)
        if not texts:
            raise MptError("MoneyPrinterTurbo returned an empty script")
        scenes = [Scene(index=i, narration=text) for i, text in enumerate(texts)]
        return SceneResult(script=script, scenes=scenes, references=references)

    async def run(self, job: Job, ctx: StageContext) -> SceneResult:
        """SHRUNK (SCENES_RUNNING, near the back). job.script/job.scenes already exist by now
        (write_script() above, approved at Gate 1) -- this only matches a clip to each existing
        scene, using the per-scene search term(s) KEYWORDS_RUNNING generated, and optionally
        renders narration audio. Never rewrites job.script or a scene's narration."""
        clips = AssetClipSource(job.assets) if job.uses_sources else self.clips
        if hasattr(clips, "total_scenes"):
            clips.total_scenes = len(job.scenes)
        self.last_trace = {"clip_source": "approved assets" if job.uses_sources else "library folder",
                           "scenes": len(job.scenes)}
        used: set[str] = set()
        scenes: list[Scene] = []
        for scene in job.scenes:
            scene.clip_path = await clips.fetch(scene, ctx.assets_dir, used)
            if scene.clip_path:
                used.add(scene.clip_path)
                pick = getattr(clips, "last_pick", None)
                if pick:
                    scene.asset_id, scene.clip_reason = pick.asset_id, pick.reason
            if self.generate_audio and self.voice_name:
                scene.audio_path = await self._audio(job, scene, ctx)
            scenes.append(scene)
        return SceneResult(script=job.script, scenes=scenes)

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
