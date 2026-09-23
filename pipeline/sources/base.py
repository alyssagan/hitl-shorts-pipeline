"""Building blocks for asset sources ("scraping pipelines").

Every source gets its own folder inside the project:

    sources/<name>/requests.jsonl   every web request: URL, params, status, purpose
    sources/<name>/manifest.json    every kept file: URL, license, author, hash
    sources/<name>/files/           the downloaded files

To add a new source, subclass `HttpSource`, implement `search()` using
`ctx.http.get_json(...)` (which logs each request for you), and register it in
`pipeline/stages/registry.py`. Downloading, hashing, dedup, the folder layout,
the request log, vetting, human review and the decision log all come for free.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..core.models import Asset, TextRef

DEFAULT_USER_AGENT = "hitl-shorts-pipeline/0.1 (personal video research tool)"

# Formats the renderer (MoneyPrinterTurbo) can use. Anything else is skipped and logged.
MIME_TO_EXT = {
    "image/jpeg": "jpg", "image/png": "png",
    "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
}


_SECRET_PARAM = re.compile(r"([?&](?:key|api_key|apikey|wskey|client_id|access_token|token)=)[^&#]+", re.I)


def redact_url(url: str) -> str:
    """Hide API keys that some services require in the query string, before a URL is logged."""
    return _SECRET_PARAM.sub(r"\1***", url)


def cc_name(url: str) -> str:
    """Readable license name from a Creative Commons URL, e.g. 'CC BY-SA 4.0'. Empty if not recognised."""
    m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/([0-9.]+)", url or "", re.I)
    if m:
        return f"CC {m.group(1).upper()} {m.group(2)}"
    m = re.search(r"creativecommons\.org/publicdomain/(zero|mark)/([0-9.]+)", url or "", re.I)
    if m:
        return f"CC0 {m.group(2)}" if m.group(1).lower() == "zero" else f"Public Domain Mark {m.group(2)}"
    return ""


class SourceUnavailable(Exception):
    """The source can't run right now. Logged, not fatal -- the other sources still run. Prefer a specific
    subclass below when the reason is known (docs/SOURCE_ERRORS.md, #4): sourcing.py inspects the concrete
    type to record a distinct `outcome` (credentials_missing/rate_limited/network_error/parse_error/
    skipped_other) instead of one undifferentiated "skipped" bucket. Existing `except SourceUnavailable`
    handling still catches every subclass, so this is purely additive -- no existing adapter breaks by not
    using a subclass, it just gets the least specific ("skipped_other") classification."""


class CredentialsMissing(SourceUnavailable):
    """No (or invalid) API key/auth configured. Fixable by the person -- the message should name the
    exact env var. Never means "searched and found nothing"."""


class RateLimited(SourceUnavailable):
    """The provider itself said to slow down (HTTP 429, or a documented rate-limit response), not a
    "no results" response. `retry_after` is the provider's own Retry-After value in seconds, when given --
    callers should respect it rather than retrying immediately."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderError(SourceUnavailable):
    """A network failure or an unexpected HTTP status (5xx, timeout, connection reset) -- a technical
    failure of the provider or the connection to it, not a search that legitimately found nothing."""


class ResponseParseError(SourceUnavailable):
    """The provider answered (HTTP 200) but its response didn't match the shape this adapter expects --
    a parsing/schema mismatch (the provider changed its API, or this adapter's assumptions were wrong),
    never silently treated as "zero results"."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    return (s[:max_len].strip("-._")) or "file"


def strip_html(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").replace("&amp;", "&").replace("&quot;", '"')
                    .replace("&#039;", "'").replace("&nbsp;", " ").split())


def _parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP `Retry-After` header value into seconds. Handles the common integer-seconds form
    (e.g. "120") and the HTTP-date form (e.g. "Wed, 21 Oct 2026 07:28:00 GMT"), per RFC 7231 sec 7.1.3.
    Returns None if the header is absent, empty, or not parseable as either form -- callers should fall
    back to their own backoff in that case, never treat an unparseable header as "no wait needed"."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        seconds = float(value)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return delta if delta >= 0 else None
    except (TypeError, ValueError, IndexError):
        return None


class LoggedHttp:
    """HTTP client that records every request in the source's own log file, so
    you can always see exactly where each piece of data came from."""

    def __init__(self, source_dir: Path, source: str, headers: dict[str, str] | None = None,
                 user_agent: str = DEFAULT_USER_AGENT, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 60.0, retries: int = 2, backoff: float = 1.0):
        self.dir = Path(source_dir)
        self.source = source
        self.headers = {"User-Agent": user_agent, **(headers or {})}
        self.transport = transport
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.log_path = self.dir / "requests.jsonl"

    def _log(self, **entry: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        entry = {"at": _now(), "source": self.source, **entry}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        from ..core import joblog
        joblog.debug("http", f"{self.source} {entry.get('method', '')} {entry.get('status')}", url=str(entry.get("url", ""))[:200],
                     ms=entry.get("duration_ms"), bytes=entry.get("bytes"), purpose=entry.get("purpose"), error=entry.get("error"),
                     attempt=entry.get("attempt"))

    async def _send(self, url: str, params: dict[str, Any] | None, purpose: str, stream_to: Path | None):
        loop = asyncio.get_running_loop()
        for attempt in range(self.retries + 1):
            started = loop.time()
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers,
                                             transport=self.transport, follow_redirects=True) as c:
                    if stream_to is None:
                        resp = await c.get(url, params=params)
                        body = resp.content
                    else:
                        stream_to.parent.mkdir(parents=True, exist_ok=True)
                        h = hashlib.sha256()
                        size = 0
                        async with c.stream("GET", url, params=params) as resp:
                            if resp.status_code == 200:
                                with open(stream_to, "wb") as f:
                                    async for chunk in resp.aiter_bytes():
                                        f.write(chunk)
                                        h.update(chunk)
                                        size += len(chunk)
                        body = None
                ms = int((loop.time() - started) * 1000)
                full_url = redact_url(str(resp.request.url))
                retry_after = _parse_retry_after(resp.headers.get("retry-after"))
                self._log(method="GET", url=full_url, status=resp.status_code, purpose=purpose,
                          duration_ms=ms, bytes=len(body) if body is not None else size,
                          dest=str(stream_to) if stream_to else None,
                          auth_header_sent=any(k.lower() in ("authorization", "x-api-key") for k in self.headers),
                          attempt=attempt + 1, retry_after=retry_after)
                if resp.status_code in (429, 503) and attempt < self.retries:
                    # Respect a provider-given Retry-After (#4) rather than always guessing with backoff.
                    await asyncio.sleep(retry_after if retry_after is not None else self.backoff * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    if stream_to and stream_to.exists():
                        stream_to.unlink()
                    if resp.status_code == 429:
                        raise RateLimited(f"{self.source} rate-limited this request (HTTP 429) after "
                                          f"{attempt + 1} attempt(s): {url}", retry_after=retry_after)
                    if resp.status_code in (401, 403):
                        raise CredentialsMissing(f"{self.source} rejected the request's credentials (HTTP "
                                                 f"{resp.status_code}) -- check the API key is present and valid: {url}")
                    raise ProviderError(f"{self.source} returned HTTP {resp.status_code} for {url}")
                return resp, body, (h.hexdigest() if stream_to else None), (size if stream_to else None)
            except httpx.TransportError as exc:
                self._log(method="GET", url=url, status=None, purpose=purpose, error=str(exc), attempt=attempt + 1)
                if attempt >= self.retries:
                    raise ProviderError(f"network error reaching {self.source} after {attempt + 1} attempt(s): {exc}") from exc
                await asyncio.sleep(self.backoff * (attempt + 1))
        raise RuntimeError("unreachable")

    async def get_json(self, url: str, params: dict[str, Any] | None = None, purpose: str = "") -> Any:
        _resp, body, _, _ = await self._send(url, params, purpose, None)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ResponseParseError(f"{self.source} returned a response that isn't valid JSON: {exc}") from exc

    async def download(self, url: str, dest: Path, purpose: str = "") -> tuple[int, str]:
        _resp, _body, sha, size = await self._send(url, None, purpose, dest)
        return size or 0, sha or ""


@dataclass
class SourceContext:
    project_dir: Path
    dir: Path                      # this source's own folder
    http: LoggedHttp
    subject: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    known_urls: set[str] = field(default_factory=set)      # files already in the project
    known_hashes: set[str] = field(default_factory=set)
    page: int = 1                  # which page of results to ask for: 2 on the second "search again", and so on


@dataclass
class SourceResult:
    assets: list[Asset] = field(default_factory=list)
    references: list[TextRef] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)   # per-query notes for the decision log


class SourceAdapter(Protocol):
    name: str
    label: str

    async def fetch(self, queries: list[str], ctx: SourceContext) -> SourceResult:
        ...


@dataclass
class Candidate:
    """One downloadable item a source found."""
    url: str
    kind: str = "image"
    mime: str = ""
    title: str = ""
    description: str = ""
    page_url: str = ""
    author: str = ""
    license: str = ""
    license_url: str = ""
    attribution: str = ""
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class HttpSource:
    """Base class for sources that search a web API and download files.

    Subclasses set `name`/`label` and implement `search()`."""
    name = "source"
    label = "Source"
    per_query = 4            # photos kept per search
    videos_per_query = 2     # videos kept per search, counted separately so photos can't crowd them out

    def __init__(self, per_query: int | None = None, videos_per_query: int | None = None):
        if per_query:
            self.per_query = per_query
        if videos_per_query is not None:
            self.videos_per_query = videos_per_query

    async def search(self, query: str, ctx: SourceContext) -> list[Candidate]:
        raise NotImplementedError

    async def fetch(self, queries: list[str], ctx: SourceContext) -> SourceResult:
        result = SourceResult()
        seen_urls = set(ctx.known_urls)
        seen_hashes = set(ctx.known_hashes)
        for q in queries:
            ctx.page = 1 + int((ctx.settings.get("prior_searches") or {}).get(f"{self.name}|{q}", 0))
            # "kind" disambiguates what "found"/"kept" are counting when a report or log line shows
            # them next to a source name a reader may not immediately map to a unit -- "media" here
            # always means photos/video (see wikipedia.py for the "text" counterpart, counting articles).
            note: dict[str, Any] = {"source": self.name, "query": q, "page": ctx.page, "found": 0, "kept": 0, "skipped": [], "kind": "media"}
            try:
                cands = await self.search(q, ctx)
            except SourceUnavailable:
                raise
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                # These usually mean the provider's response didn't match what search() expects
                # (a changed field name, an unexpected null, a reshaped list) -- a parse problem,
                # not a legitimate "found nothing" (#4: never silently convert a technical error
                # into "no results").
                note["error"] = f"{type(exc).__name__}: {exc}"
                note["outcome"] = "parse_error"
                result.trace.append(note)
                continue
            except Exception as exc:  # one bad query must not sink the others
                note["error"] = f"{type(exc).__name__}: {exc}"
                note["outcome"] = "unknown_error"
                result.trace.append(note)
                continue
            note["found"] = len(cands)
            kept = 0
            kept_by_kind = {"image": 0, "video": 0}
            for c in cands:
                kind = "video" if c.kind == "video" else "image"
                if kept_by_kind[kind] >= (self.videos_per_query if kind == "video" else self.per_query):
                    continue
                ext = MIME_TO_EXT.get((c.mime or "").lower())
                if not ext:
                    note["skipped"].append({"url": c.url, "reason": f"format '{c.mime or 'unknown'}' can't be used by the renderer"})
                    continue
                if c.url in seen_urls:
                    note["skipped"].append({"url": c.url, "reason": "already in this project"})
                    continue
                fname = f"{len(ctx.known_urls) + len(result.assets) + 1:03d}-{safe_name(c.title or 'asset')}.{ext}"
                dest = ctx.dir / "files" / fname
                try:
                    _size, sha = await ctx.http.download(c.url, dest, purpose=f"download {c.kind} for query '{q}': {c.title or c.url}")
                except Exception as exc:
                    note["skipped"].append({"url": c.url, "reason": f"download failed: {exc}"})
                    continue
                if sha in seen_hashes:
                    dest.unlink(missing_ok=True)
                    note["skipped"].append({"url": c.url, "reason": "identical file already in this project"})
                    continue
                seen_urls.add(c.url)
                seen_hashes.add(sha)
                result.assets.append(Asset(
                    source=self.name, kind=c.kind if c.kind in ("image", "video") else "image",
                    path=str(dest), rel_path=os.path.relpath(dest, ctx.project_dir),
                    source_url=c.url, page_url=c.page_url, title=c.title, description=c.description,
                    query=q, author=c.author, license=c.license, license_url=c.license_url,
                    attribution=c.attribution, width=c.width, height=c.height, duration=c.duration,
                    mime=c.mime, sha256=sha, meta=c.meta,
                ))
                kept += 1
                kept_by_kind[kind] += 1
            note["kept"] = kept
            note["kept_photos"], note["kept_videos"] = kept_by_kind["image"], kept_by_kind["video"]
            # #4: distinguish a real zero-match search from one that found candidates but couldn't
            # keep any of them (wrong format, download failures, all duplicates) -- both are "kept: 0"
            # but they mean very different things to a person deciding whether to search again.
            note["outcome"] = "ok_kept" if kept else ("ok_zero_matches" if note["found"] == 0 else "ok_no_downloadable")
            result.trace.append(note)
        return result
