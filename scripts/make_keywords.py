#!/usr/bin/env python3
"""Generate a keyword file for a true-crime reel with a free-tier LLM (Gemini by default).

    python3 scripts/make_keywords.py "jack the ripper"
    python3 scripts/make_keywords.py "h h holmes" --n 30 --era "1890s Chicago"

Writes library/keywords/<topic>.txt, ready for:
    python3 scripts/poc.py "true crime jack the ripper" --keywords manual --keywords-file library/keywords/jack-the-ripper.txt ...

Free tier only: it uses GEMINI_API_KEY from your .env (Google AI Studio free key) and the model/endpoint in
config/pipeline.toml [keywords]. Standard library only. The key is never printed or written anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-3.6-flash"

PROMPT = """You write search keywords for a stock-footage and archive search, for a short true-crime video (a "reel") about: {topic}
{era_line}
The keywords will be typed into photo and video libraries: Wikipedia, Wikimedia Commons, Library of Congress, Internet Archive,
Smithsonian, Pexels. They must FIND REAL, PUBLIC-DOMAIN OR FREELY LICENSED IMAGES AND FOOTAGE that could be shown while the story is told.

Rules:
- Return {n} keywords in total, spread across the categories below.
- Every keyword is a specific search phrase of 2 to 5 words. NEVER a single word (single words match random things).
- Include the year, decade or place where it helps ("whitechapel 1888", "victorian london street").
- Think of what a camera could actually show: places and streets of the time, buildings, period newspapers and documents, maps,
  courtrooms, police stations, era clothing and transport, atmospheric b-roll (fog, gaslight, old streets).
- Prefer historical photographs, engravings, maps and documents over modern stock.
- Do NOT suggest graphic material: no corpses, crime-scene or autopsy photos, gore, or victims' bodies.
- Avoid the names of victims or living people. Names of investigators, suspects convicted in court, and well-known public figures
  are fine only when they help find archive material.
- Do not repeat yourself or offer near-duplicates.

Categories (use these exact keys): "places", "documents_and_press", "investigation_and_justice", "era_and_daily_life", "atmosphere_broll", "people_and_context"

Answer with JSON only, no other text, in this shape:
{{"places": ["..."], "documents_and_press": ["..."], "investigation_and_justice": ["..."], "era_and_daily_life": ["..."], "atmosphere_broll": ["..."], "people_and_context": ["..."]}}
"""


def load_env(path: Path) -> None:
    """Read KEY=VALUE lines from .env into os.environ (without overriding anything already set)."""
    if not path.exists():
        return
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def read_keywords_config(path: Path) -> dict:
    """Tiny reader for the [keywords] section of pipeline.toml (works on any Python 3)."""
    out: dict = {}
    if not path.exists():
        return out
    inside = False
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("["):
            inside = s == "[keywords]"
        elif inside and "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            out[k.strip()] = v.split("#")[0].strip().strip("\"'")
    return out


def build_prompt(topic: str, n: int, era: str = "") -> str:
    return PROMPT.format(topic=topic, n=n, era_line=f"Setting or era: {era}" if era else "")


def parse_reply(text: str) -> dict[str, list[str]]:
    """Pull the JSON object out of the model's reply (it may wrap it in code fences)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("the model's reply had no JSON in it")
    data = json.loads(m.group(0))
    return {k: [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}


def clean(groups: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    """Strip quotes, drop single words and over-long phrases, drop repeats. Returns (kept, dropped)."""
    seen: set[str] = set()
    kept: dict[str, list[str]] = {}
    dropped: list[str] = []
    for cat, terms in groups.items():
        for t in terms:
            t = re.sub(r"\s+", " ", t.strip().strip("\"'`‘’“”")).strip()
            words = t.split()
            if not (2 <= len(words) <= 6) or t.lower() in seen:
                dropped.append(t)
                continue
            seen.add(t.lower())
            kept.setdefault(cat, []).append(t)
    return kept, dropped


def render(topic: str, groups: dict[str, list[str]]) -> str:
    lines = [f"# Keywords for: {topic}  (made by scripts/make_keywords.py with an LLM; edit freely)",
             "# One per line. # lines are ignored. Use with: poc.py --keywords manual --keywords-file <this file>", ""]
    for cat, terms in groups.items():
        lines.append(f"# {cat.replace('_', ' ')}")
        lines += terms
        lines.append("")
    return "\n".join(lines)


RETRY_WAITS = (4, 10, 25, 45)          # seconds; Google's free tier says 503/429 "usually temporary"


def call_llm(base: str, model: str, key: str, prompt: str, waits: tuple = RETRY_WAITS, sleep=time.sleep) -> tuple[str, dict]:
    """Returns (reply text, usage dict). This script runs before any job exists, so there's no decision log to
    write tokens/cost into (docs/LOGGING.md "Tokens / cost" covers per-job calls made once a job is running);
    the caller just prints this one call's usage so it isn't silently thrown away."""
    body = json.dumps({"model": model, "temperature": 0.7, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body, method="POST",
                                 headers={"content-type": "application/json", "authorization": f"Bearer {key}"})
    for attempt in range(len(waits) + 1):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                reply = json.loads(r.read())
                return reply["choices"][0]["message"]["content"], (reply.get("usage") or {})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < len(waits):
                print(f"  The free tier is busy ({e.code}). Retrying in {waits[attempt]}s... ({attempt + 1}/{len(waits)})")
                sleep(waits[attempt])
                continue
            if e.code in (429, 503):
                sys.exit(f"Still busy after {len(waits)} retries ({e.code}). This is Google's free tier being overloaded, not your setup. "
                         "Try again in a few minutes, or try another model with --model.")
            if e.code in (401, 403):
                sys.exit(f"The API key was refused ({e.code}). Check GEMINI_API_KEY in .env.")
            if e.code == 404:
                sys.exit(f"Model '{model}' not found (404). Set a current free model with --model. Details: {detail}")
            sys.exit(f"The LLM said no ({e.code}): {detail}")
        except urllib.error.URLError as e:
            if attempt < len(waits):
                sleep(waits[attempt])
                continue
            sys.exit(f"Can't reach the LLM endpoint ({e.reason}).")
    sys.exit("unreachable")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "keywords"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topic", help='the case or subject, e.g. "jack the ripper"')
    ap.add_argument("--n", type=int, default=24, help="how many keywords to ask for (default 24)")
    ap.add_argument("--era", default="", help='optional setting/era hint, e.g. "1890s Chicago"')
    ap.add_argument("--out", default="", help="output file (default library/keywords/<topic>.txt)")
    ap.add_argument("--model", default="", help="override the model from config/pipeline.toml")
    ap.add_argument("--print", dest="print_only", action="store_true", help="show the result, don't write a file")
    ap.add_argument("--show-prompt", action="store_true", help="print the prompt and exit (no API call)")
    args = ap.parse_args()

    prompt = build_prompt(args.topic, args.n, args.era)
    if args.show_prompt:
        print(prompt)
        return
    load_env(Path(".env"))
    cfg = read_keywords_config(Path("config/pipeline.toml"))
    base = cfg.get("base_url") or DEFAULT_BASE
    model = args.model or cfg.get("model") or DEFAULT_MODEL
    key = os.environ.get(cfg.get("api_key_env") or "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        sys.exit("No GEMINI_API_KEY found. Put your free Google AI Studio key in .env as GEMINI_API_KEY=... (never paste it in chat).")

    print(f"Asking {model} for {args.n} keywords about '{args.topic}'...")
    try:
        text_reply, usage = call_llm(base, model, key, prompt)
        groups, dropped = clean(parse_reply(text_reply))
    except (ValueError, KeyError) as e:
        sys.exit(f"Couldn't read the model's answer ({e}). Run it again.")
    if usage:
        pt, ct, tt = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), usage.get("total_tokens", 0)
        print(f"({model}: {tt} tokens -- {pt} in / {ct} out. Free tier, so $0; see [usage] in "
              "config/pipeline.toml if you ever point this at a paid model.)")
    if not groups:
        sys.exit("The model returned no usable keywords. Run it again.")
    text = render(args.topic, groups)
    total = sum(len(v) for v in groups.values())
    if dropped:
        print(f"(dropped {len(dropped)} unusable: single words, too long or repeats)")
    if args.print_only:
        print("\n" + text)
        return
    out = Path(args.out) if args.out else Path("library/keywords") / f"{slug(args.topic)}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {total} keywords to {out}. Read and edit it, then:")
    print(f'  python3 scripts/poc.py "true crime {args.topic}" --keywords manual --keywords-file {out} --sources wikipedia,commons,loc,archive --reviewer "Aly"')


if __name__ == "__main__":
    main()
