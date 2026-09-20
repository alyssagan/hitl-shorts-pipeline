"""Name -> implementation lookup for each stage. This is what makes providers
selectable per job (`providers.keywords = "llm"`), and it is the one file to
touch when you add a new provider."""
from __future__ import annotations

import os
from typing import Any, Callable

from .base import KeywordStage, RenderStage, SceneStage
from .keywords.llm import LLMKeywordStage
from .keywords.manual import ManualKeywordStage
from .mpt_client import MptClient
from .render.mpt import MptRenderStage
from .scenes.clips import LocalFolderClipSource
from .scenes.mpt import MptSceneStage


class Registry:
    def __init__(self) -> None:
        self._keywords: dict[str, Callable[[], KeywordStage]] = {}
        self._scenes: dict[str, Callable[[], SceneStage]] = {}
        self._render: dict[str, Callable[[], RenderStage]] = {}

    def register_keywords(self, name: str, factory: Callable[[], KeywordStage]) -> None:
        self._keywords[name] = factory

    def register_scenes(self, name: str, factory: Callable[[], SceneStage]) -> None:
        self._scenes[name] = factory

    def register_render(self, name: str, factory: Callable[[], RenderStage]) -> None:
        self._render[name] = factory

    def keyword_stage(self, name: str) -> KeywordStage:
        return self._get(self._keywords, name, "keyword")()

    def scene_stage(self, name: str) -> SceneStage:
        return self._get(self._scenes, name, "scene")()

    def render_stage(self, name: str) -> RenderStage:
        return self._get(self._render, name, "render")()

    @staticmethod
    def _get(table: dict, name: str, kind: str):
        if name not in table:
            raise KeyError(f"unknown {kind} provider '{name}'. available: {sorted(table)}")
        return table[name]

    def available(self) -> dict[str, list[str]]:
        return {"keywords": sorted(self._keywords), "scenes": sorted(self._scenes), "render": sorted(self._render)}


def build_default_registry(settings: dict[str, Any]) -> Registry:
    """Wire the built-in providers from config/pipeline.toml + environment."""
    mpt_cfg = settings.get("mpt", {})
    kw_cfg = settings.get("keywords", {})
    lib_cfg = settings.get("library", {})

    def mpt() -> MptClient:
        return MptClient(
            base_url=os.getenv("MPT_BASE_URL", mpt_cfg.get("base_url", "http://localhost:8080")),
            api_key=os.getenv("MPT_API_KEY", mpt_cfg.get("api_key", "")),
        )

    reg = Registry()
    reg.register_keywords("manual", ManualKeywordStage)
    reg.register_keywords("llm", lambda: LLMKeywordStage(
        base_url=os.getenv("KEYWORD_LLM_BASE_URL", kw_cfg.get("base_url", "https://api.openai.com/v1")),
        api_key=os.getenv("KEYWORD_LLM_API_KEY", ""),
        model=os.getenv("KEYWORD_LLM_MODEL", kw_cfg.get("model", "gpt-4o-mini")),
        count=int(kw_cfg.get("count", 10)),
    ))
    reg.register_scenes("mpt", lambda: MptSceneStage(
        client=mpt(),
        clips=LocalFolderClipSource(lib_cfg.get("clips_dir", "library/clips")),
        voice_name=mpt_cfg.get("voice_name", ""),
        language=mpt_cfg.get("language", ""),
        generate_audio=bool(mpt_cfg.get("generate_scene_audio", False)),
        paragraphs=int(mpt_cfg.get("paragraphs", 3)),
    ))
    reg.register_render("mpt", lambda: MptRenderStage(
        client=mpt(),
        voice_name=mpt_cfg.get("voice_name", ""),
        language=mpt_cfg.get("language", ""),
        aspect=mpt_cfg.get("aspect", "9:16"),
        clip_seconds=int(mpt_cfg.get("clip_seconds", 5)),
        clip_root_host=os.getenv("CLIPS_DIR_HOST", lib_cfg.get("clips_dir_host", "")),
        clip_root_mpt=os.getenv("CLIPS_DIR_IN_MPT", lib_cfg.get("clips_dir_in_mpt", "")),
    ))
    return reg
