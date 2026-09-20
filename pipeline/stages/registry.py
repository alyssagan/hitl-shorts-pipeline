"""Name -> implementation lookup for each stage. This is what makes providers
selectable per job (`providers.keywords = "llm"`), and it is the one file to
touch when you add a new provider."""
from __future__ import annotations

import os
from typing import Any, Callable

from .base import KeywordStage, RenderStage, SceneStage
from ..sources.base import DEFAULT_USER_AGENT, SourceAdapter
from ..sources.commons import CommonsSource
from ..sources.folder import FolderSource
from ..sources.internet_archive import InternetArchiveSource
from ..sources.loc import LibraryOfCongressSource
from ..sources.nasa import NasaSource
from ..sources.pexels import PexelsSource
from ..sources.pixabay import PixabaySource
from ..sources.smithsonian import SmithsonianSource
from ..sources.unsplash import UnsplashSource
from ..sources.urls import UrlListSource
from ..sources.wikipedia import WikipediaSource
from .keywords.llm import LLMKeywordStage
from .keywords.manual import ManualKeywordStage
from .mpt_client import MptClient
from .render.mpt import MptRenderStage
from .scenes.clips import LocalFolderClipSource
from .scenes.mpt import MptSceneStage
from .scenes.writer import ScriptWriter
from .sourcing import SourcingStage


class Registry:
    def __init__(self) -> None:
        self._keywords: dict[str, Callable[[], KeywordStage]] = {}
        self._scenes: dict[str, Callable[[], SceneStage]] = {}
        self._render: dict[str, Callable[[], RenderStage]] = {}
        self._sources: dict[str, Callable[[], SourceAdapter]] = {}
        self.user_agent = DEFAULT_USER_AGENT
        self.max_queries = 5
        self.source_transport = None      # tests inject an httpx mock transport

    def register_keywords(self, name: str, factory: Callable[[], KeywordStage]) -> None:
        self._keywords[name] = factory

    def register_scenes(self, name: str, factory: Callable[[], SceneStage]) -> None:
        self._scenes[name] = factory

    def register_render(self, name: str, factory: Callable[[], RenderStage]) -> None:
        self._render[name] = factory

    def register_source(self, name: str, factory: Callable[[], SourceAdapter]) -> None:
        self._sources[name] = factory

    def sourcing_stage(self) -> SourcingStage:
        return SourcingStage({n: f() for n, f in self._sources.items()}, user_agent=self.user_agent,
                             max_queries=self.max_queries, transport=self.source_transport)

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
        return {"keywords": sorted(self._keywords), "scenes": sorted(self._scenes), "render": sorted(self._render),
                "sources": sorted(self._sources)}


def parse_path_map(text: str) -> list[tuple[str, str]]:
    """PATH_MAP="/app/projects=/MoneyPrinterTurbo/storage/local_videos/projects;..." -> [(host, mpt), ...]
    Translates file paths as this service sees them into paths as MoneyPrinterTurbo sees them."""
    out = []
    for part in filter(None, (p.strip() for p in text.split(";"))):
        if "=" in part:
            host, mpt = part.split("=", 1)
            out.append((host.strip(), mpt.strip()))
    return out


def build_default_registry(settings: dict[str, Any]) -> Registry:
    """Wire the built-in providers from config/pipeline.toml + environment."""
    mpt_cfg = settings.get("mpt", {})
    kw_cfg = settings.get("keywords", {})
    lib_cfg = settings.get("library", {})
    src_cfg = settings.get("sources", {})

    def mpt() -> MptClient:
        return MptClient(
            base_url=os.getenv("MPT_BASE_URL", mpt_cfg.get("base_url", "http://localhost:8080")),
            api_key=os.getenv("MPT_API_KEY", mpt_cfg.get("api_key", "")),
        )

    reg = Registry()
    reg.user_agent = os.getenv("SOURCES_USER_AGENT", src_cfg.get("user_agent", DEFAULT_USER_AGENT))
    reg.max_queries = int(src_cfg.get("max_queries", 5))
    per_query = int(src_cfg.get("per_query", 4))
    vids = int(src_cfg.get("videos_per_query", 2))
    reg.register_source("wikipedia", lambda: WikipediaSource(max_articles=int(src_cfg.get("wikipedia_articles", 2))))
    reg.register_source("commons", lambda: CommonsSource(per_query=per_query))
    reg.register_source("pexels", lambda: PexelsSource(per_query=per_query, videos_per_query=vids, videos=bool(src_cfg.get("pexels_videos", True))))
    reg.register_source("pixabay", lambda: PixabaySource(per_query=per_query, videos_per_query=vids, videos=bool(src_cfg.get("pixabay_videos", True))))
    reg.register_source("unsplash", lambda: UnsplashSource(per_query=per_query))
    reg.register_source("nasa", lambda: NasaSource(per_query=per_query, videos_per_query=vids, videos=bool(src_cfg.get("nasa_videos", True))))
    reg.register_source("archive", lambda: InternetArchiveSource(
        per_query=per_query, videos_per_query=vids, require_license=bool(src_cfg.get("archive_require_license", True)),
        max_mb=int(src_cfg.get("archive_max_mb", 60))))
    reg.register_source("loc", lambda: LibraryOfCongressSource(per_query=per_query, videos_per_query=vids))
    reg.register_source("smithsonian", lambda: SmithsonianSource(per_query=per_query))
    reg.register_source("urls", lambda: UrlListSource(
        max_mb=int(src_cfg.get("url_max_mb", 200)), max_height=int(src_cfg.get("url_max_height", 1080))))
    reg.register_source("folder", lambda: FolderSource(src_cfg.get("folder_path", "library/scraped")))
    reg.register_keywords("manual", ManualKeywordStage)
    reg.register_keywords("llm", lambda: LLMKeywordStage(
        base_url=os.getenv("KEYWORD_LLM_BASE_URL", kw_cfg.get("base_url", "https://api.openai.com/v1")),
        api_key=os.getenv("KEYWORD_LLM_API_KEY", ""),
        model=os.getenv("KEYWORD_LLM_MODEL", kw_cfg.get("model", "gpt-4o-mini")),
        count=int(kw_cfg.get("count", 10)),
    ))
    script_cfg = settings.get("script", {})

    def script_writer():
        """Our own longer script writer; needs a key. Without one, MoneyPrinterTurbo writes the script."""
        if script_cfg.get("provider", "llm") != "llm":
            return None
        key = os.getenv(script_cfg.get("api_key_env", "GEMINI_API_KEY"), "")
        if not key:
            return None
        return ScriptWriter(
            base_url=os.getenv("SCRIPT_LLM_BASE_URL", script_cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai")),
            api_key=key,
            model=os.getenv("SCRIPT_LLM_MODEL", script_cfg.get("model", "gemini-3.6-flash")),
            target_words=int(script_cfg.get("target_words", 260)),
            grounding_chars=int(script_cfg.get("grounding_chars", 12000)),
        )

    reg.register_scenes("mpt", lambda: MptSceneStage(
        client=mpt(),
        clips=LocalFolderClipSource(lib_cfg.get("clips_dir", "library/clips")),
        voice_name=mpt_cfg.get("voice_name", ""),
        language=mpt_cfg.get("language", ""),
        generate_audio=bool(mpt_cfg.get("generate_scene_audio", False)),
        paragraphs=int(mpt_cfg.get("paragraphs", 3)),
        writer=script_writer(),
    ))
    reg.register_render("mpt", lambda: MptRenderStage(
        client=mpt(),
        voice_name=mpt_cfg.get("voice_name", ""),
        language=mpt_cfg.get("language", ""),
        aspect=mpt_cfg.get("aspect", "9:16"),
        clip_seconds=int(mpt_cfg.get("clip_seconds", 5)),
        clip_root_host=os.getenv("CLIPS_DIR_HOST", lib_cfg.get("clips_dir_host", "")),
        clip_root_mpt=os.getenv("CLIPS_DIR_IN_MPT", lib_cfg.get("clips_dir_in_mpt", "")),
        path_map=parse_path_map(os.getenv("PATH_MAP", "")),
    ))
    return reg
