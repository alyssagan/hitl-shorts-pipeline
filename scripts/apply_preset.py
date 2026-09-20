#!/usr/bin/env python3
"""Compose a MoneyPrinterTurbo config.toml from small presets.

    python scripts/apply_preset.py llm-openai voice-elevenlabs stock-pexels-pixabay

Reads   vendor/MoneyPrinterTurbo/config.example.toml   (keeps all its comments)
Overlays config/mpt-presets/<name>.toml in the order given (later wins)
Writes  config/mpt-config.toml                          (git-ignored; mounted into the container)

`${VAR}` inside a preset value is replaced from the environment / .env, so API
keys never live in a committed file. Missing variables are left empty and
reported.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def expand(value: Any, env: dict[str, str], missing: set[str]) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            name = m.group(1)
            if name in env:
                return env[name]
            missing.add(name)
            return ""
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, value)
    if isinstance(value, list):
        return [expand(v, env, missing) for v in value]
    return value


def toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(i) for i in v) + "]"
    raise TypeError(f"unsupported TOML value: {v!r}")


def overlay(base_text: str, overrides: dict[str, dict[str, Any]]) -> str:
    """Set `key = value` inside `[section]` of `base_text`, in place, keeping
    comments. Keys that do not exist yet are appended to the end of their
    section (or the section is created)."""
    lines = base_text.splitlines()
    for section, values in overrides.items():
        for key, value in values.items():
            lines = _set_key(lines, section, key, toml_value(value))
    return "\n".join(lines) + "\n"


def _set_key(lines: list[str], section: str, key: str, rendered: str) -> list[str]:
    header = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    current = None
    section_start = section_end = None
    for i, line in enumerate(lines):
        m = header.match(line)
        if m:
            if current == section and section_end is None:
                section_end = i
            current = m.group(1).strip()
            if current == section:
                section_start = i
        elif current == section and key_re.match(line):
            lines[i] = f"{key} = {rendered}"
            return lines
    if section_start is None:
        return lines + ["", f"[{section}]", f"{key} = {rendered}"]
    end = section_end if section_end is not None else len(lines)
    insert_at = end
    while insert_at > section_start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, f"{key} = {rendered}")
    return lines


def build(preset_names: list[str], base_path: Path, presets_dir: Path, env: dict[str, str]) -> tuple[str, set[str]]:
    text = base_path.read_text(encoding="utf-8")
    missing: set[str] = set()
    for name in preset_names:
        path = presets_dir / f"{name}.toml"
        if not path.exists():
            raise SystemExit(f"unknown preset '{name}'. available: {sorted(p.stem for p in presets_dir.glob('*.toml'))}")
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        overrides = {sec: {k: expand(v, env, missing) for k, v in vals.items()} for sec, vals in data.items()
                     if isinstance(vals, dict)}
        text = overlay(text, overrides)
    tomllib.loads(text)     # fail loudly if we produced invalid TOML
    return text, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("presets", nargs="+")
    ap.add_argument("--base", default=str(ROOT / "vendor/MoneyPrinterTurbo/config.example.toml"))
    ap.add_argument("--presets-dir", default=str(ROOT / "config/mpt-presets"))
    ap.add_argument("--out", default=str(ROOT / "config/mpt-config.toml"))
    args = ap.parse_args()

    env = {**load_env_file(ROOT / ".env"), **os.environ}
    text, missing = build(args.presets, Path(args.base), Path(args.presets_dir), env)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} from presets: {', '.join(args.presets)}")
    if missing:
        print(f"warning: not set in .env / environment (left empty): {', '.join(sorted(missing))}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
