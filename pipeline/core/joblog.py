"""Step-by-step activity log for every project.

Each project gets `logs/pipeline.log`: one plain-text line per thing that happened, oldest first:

    2026-09-20T23:59:01Z INFO  sourcing  pulling from wikipedia  queries=['jack the ripper whitechapel 1888'] page=1
    2026-09-20T23:59:03Z INFO  sourcing  wikipedia done in 1.9s  assets=6 references=1
    2026-09-20T23:59:04Z WARN  sourcing  pexels skipped: PEXELS_API_KEY is not set

Levels: DEBUG (every HTTP request, every asset kept, every retry), INFO (each step, each decision), WARN (something odd
but the run continues), ERROR (a stage failed; includes the traceback). The same lines go to the container's own log
(`docker compose logs -f pipeline`); set LOG_LEVEL=DEBUG in .env to see DEBUG there too. The file always keeps DEBUG.

Stages call `log(level, stage, message, **details)`; they don't need to know where the file is. The orchestrator binds
the project folder around each stage run, so the line lands in the right project's file.
Secrets: any detail named key, api_key, *_key, token, secret, password or authorization is written as ***.
"""
from __future__ import annotations

import contextvars
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar("joblog_dir", default=None)
_lock = threading.Lock()
_std = logging.getLogger("pipeline")
_configured = False
_SECRET = re.compile(r"(^|_)(api_?key|key|token|secret|password|authorization)$", re.I)


def _configure_stdout() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    if not _std.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(message)s"))
        _std.addHandler(h)
    _std.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO))
    _std.propagate = False


def bind(project_dir: Path | None):
    """Direct `log()` calls in this task to a project's file. Returns a token for `unbind`."""
    return _dir.set(Path(project_dir) if project_dir else None)


def unbind(token) -> None:
    _dir.reset(token)


def _fmt(details: dict[str, Any]) -> str:
    parts = []
    for k, v in details.items():
        if v is None or v == "":
            continue
        v = "***" if _SECRET.search(k) else v
        s = str(v).replace("\n", " ")
        parts.append(f"{k}={s[:300]}")
    return "  ".join(parts)


def write(project_dir: Path | None, level: str, stage: str, message: str, **details: Any) -> str:
    level = level.upper()
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {level:<5} {stage:<9} {message}"
    extra = _fmt(details)
    if extra:
        line += "  " + extra
    _configure_stdout()
    _std.log({"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}.get(level, 20), line)
    if project_dir:
        try:
            path = Path(project_dir) / "logs" / "pipeline.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with _lock, open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass                              # logging must never break a run
    return line


def log(level: str, stage: str, message: str, **details: Any) -> None:
    write(_dir.get(), level, stage, message, **details)


def debug(stage: str, message: str, **d: Any) -> None: log("DEBUG", stage, message, **d)
def info(stage: str, message: str, **d: Any) -> None: log("INFO", stage, message, **d)
def warn(stage: str, message: str, **d: Any) -> None: log("WARN", stage, message, **d)
def error(stage: str, message: str, **d: Any) -> None: log("ERROR", stage, message, **d)


def read(project_dir: Path, *, after: int = 0, min_level: str = "INFO", tail: int | None = None) -> tuple[list[str], int]:
    """Lines at or above `min_level`, skipping the first `after` lines of the file. Returns (lines, next_cursor)."""
    path = Path(project_dir) / "logs" / "pipeline.log"
    if not path.exists():
        return [], 0
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    floor = LEVELS.get(min_level.upper(), 20)
    out = []
    for ln in all_lines[after:]:
        lvl = ln.split(" ", 2)[1] if ln.count(" ") >= 2 else "INFO"
        if LEVELS.get(lvl, 20) >= floor:
            out.append(ln)
    if tail:
        out = out[-tail:]
    return out, len(all_lines)
