#!/usr/bin/env python3
"""Walk one video through the pipeline from the terminal.

    python3 scripts/poc.py "3 surprising facts about octopuses"

It talks to the pipeline API (default http://localhost:8000), pauses at every
approval gate to ask you what to do, and saves the finished video to
output/<job id>.mp4. Standard library only -- nothing to install.

Everything for the video lives in projects/<name>-<id>/ (sources, logs, the
decision log DECISIONS.md, credits). Use --sources "" to skip web sources and
use only your own clips in library/clips.
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

# Where the pipeline copies an approved keyword set once its keywords are approved -- "Gate 1½", human or
# auto (config/pipeline.toml's [library]
# keywords_dir, same value -- duplicated here since poc.py is deliberately standard-library only, same
# reasoning as scripts/make_keywords.py's own duplicated constants), and where this script parks an
# editable draft when --keywords manual has no --keywords-file to read. See docs/RUNNING.md "Keyword files,
# always".
LIBRARY_KEYWORDS_DIR = Path("library/keywords")

# The job's states, in pipeline order (pipeline/core/models.py's JobState, docs/PIPELINE_STAGES.md) -- used
# by main()'s at() to skip a gate the job (e.g. a --resume'd one) has already passed. Matches the 2026-09-25
# reorder: script is written and approved first, keywords are derived from it and auto-approved by default
# (so keywords_review is usually skipped right over -- at() still handles that fine, since by the time it
# polls, the live state is already past it). A state missing from this list is treated by at() as "already
# past everything" -- which is exactly how the reorder itself first shipped with a broken, pre-reorder copy
# of this list: script_running/script_review were missing, so every gate below got silently skipped for a
# job sitting at the new Gate 1, and main() ran straight to `wait_for(..., {"completed"}, ...)` and hung
# forever waiting for a state the job would never reach without a human approving the script first. Keep
# this in sync with pipeline/core/models.py's JobState order -- tests/test_poc_state_order.py checks it.
JOB_STATE_ORDER = ["created", "script_running", "script_review", "keywords_running", "keywords_review",
                    "sourcing_running", "vetting_running", "assets_review", "scenes_running", "scenes_review",
                    "rendering", "completed"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            msg = json.loads(e.read() or b"{}").get("error", e.reason)
            sys.exit(f"\nThe pipeline said no ({e.code}): {msg}")
        except urllib.error.URLError as e:
            sys.exit(f"\nCan't reach the pipeline at {self.base} ({e.reason}).\n"
                     "Is it running?  Try:  docker compose ps")

    def download(self, path: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(self.base + path, timeout=600) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)


LOG = {"cursor": {}, "level": "INFO", "show": True}

# Level -> ANSI color, for format_log_line below. Dim for DEBUG (least important), cyan for INFO (the
# normal case, so it doesn't fight for attention), yellow/red for WARN/ERROR so those jump out of a long
# stream without having to read every word. Never applied to what's written to logs/pipeline.log itself
# (pipeline/core/joblog.py) or to the container's own stdout (`docker compose logs`) -- docs/LOGGING.md's
# documented plain-text format (for grepping, tailing, etc.) is untouched; this only reformats what THIS
# script echoes to your terminal while a job runs.
_LEVEL_COLOR = {"DEBUG": "\033[2m", "INFO": "\033[36m", "WARN": "\033[33m", "ERROR": "\033[31m"}
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _use_color() -> bool:
    # Standard conventions: no color when piped/redirected (isatty() is False), and NO_COLOR opts out
    # even in a real terminal (https://no-color.org). FORCE_COLOR overrides both, for a terminal that
    # reports isatty() wrong (some CI runners, some wrapped terminals).
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def format_log_line(raw: str, color: bool | None = None) -> str:
    """One raw `logs/pipeline.log` line (docs/LOGGING.md's format: `TIMESTAMP LEVEL  STAGE  message  k=v ...`),
    reformatted for a live terminal stream: drops the date off the timestamp (during a live run it's always
    "now"), and -- unless piped or NO_COLOR -- colors the level and bolds the stage so a WARN or ERROR in a
    long stream of INFO lines doesn't need to be read word-by-word to spot. A line that doesn't match the
    expected shape (fewer than 4 whitespace-separated parts) is returned unchanged rather than mangled."""
    if color is None:
        color = _use_color()
    parts = raw.split(maxsplit=3)
    if len(parts) < 4:
        return raw
    ts, level, stage, rest = parts
    if level not in _LEVEL_COLOR or not (len(ts) >= 20 and ts[10] == "T" and ts.endswith("Z")):
        return raw          # doesn't look like one of our lines -- leave it exactly as-is
    time_only = ts[11:19]
    if not color:
        return f"{time_only} {level:<5} {stage:<9} {rest}"
    level_color = _LEVEL_COLOR.get(level, "")
    return f"{_DIM}{time_only}{_RESET} {level_color}{level:<5}{_RESET} {_BOLD}{stage:<9}{_RESET} {rest}"


def show_log(api: Api, job_id: str) -> None:
    """Print the new activity-log lines (what the pipeline is doing right now)."""
    if not LOG["show"]:
        return
    try:
        r = api.call("GET", f"/jobs/{job_id}/log?format=json&level={LOG['level']}&after={LOG['cursor'].get(job_id, 0)}")
    except SystemExit:
        return
    for ln in r.get("lines", []):
        print("\r" + " " * 70 + "\r  | " + format_log_line(ln))
    LOG["cursor"][job_id] = r.get("next", 0)


def print_timing(api: Api, job_id: str) -> None:
    """Provenance (docs/LOGGING.md): how long each part took and how long the job sat waiting on you."""
    try:
        t = api.call("GET", f"/jobs/{job_id}/timing")
    except SystemExit:
        return
    print(f"\n  Timing: {t['total_wall_seconds']:.0f}s total, {t['time_waiting_on_you_seconds']:.0f}s of that waiting on you")
    for stage, secs in sorted(t["time_per_stage_seconds"].items(), key=lambda kv: -kv[1]):
        n = t.get("stage_run_counts", {}).get(stage, 1)
        print(f"    {stage}: {secs:.0f}s" + (f" ({n} runs)" if n > 1 else ""))
    print(f"  Full detail: projects/.../DECISIONS.md  or  curl {api.base}/jobs/{job_id}/timing")


def print_usage(api: Api, job_id: str) -> None:
    """Provenance (docs/LOGGING.md): every LLM call this job made, its tokens, and an estimated $ cost
    (free-tier models are $0 -- see [usage] in config/pipeline.toml to price a paid model)."""
    try:
        u = api.call("GET", f"/jobs/{job_id}/usage")
    except SystemExit:
        return
    if not u["calls"]:
        return
    print(f"\n  LLM usage: {u['calls']} call(s), {u['total_tokens']:,} tokens" +
          (f", ~${u['cost_usd']:.4f}" if u["cost_usd"] else " (free tier: $0)"))
    for model, m in sorted(u["by_model"].items(), key=lambda kv: -kv[1]["total_tokens"]):
        quota = u.get("free_quota", {}).get(model) or {}
        q = f", free tier: {quota['requests_per_minute']}/min {quota['requests_per_day']}/day" if quota else ""
        print(f"    {model}: {m['calls']} call(s), {m['total_tokens']:,} tokens ({m['prompt_tokens']:,} in / "
              f"{m['completion_tokens']:,} out){q}")
    print(f"  Full detail: projects/.../DECISIONS.md  or  curl {api.base}/jobs/{job_id}/usage")


def wait_for(api: Api, job_id: str, states: set[str], what: str, every: float = 3.0) -> dict:
    """Poll until the job reaches one of `states`. Exits with the reason if it fails."""
    started = time.time()
    while True:
        job = api.call("GET", f"/jobs/{job_id}")
        show_log(api, job_id)
        if job["state"] in states:
            print()
            return job
        if job["state"] in ("failed", "cancelled"):
            print()
            print_timing(api, job_id)
            print_usage(api, job_id)
            sys.exit(f"\nThe job {job['state']}: {job.get('error') or 'no details'}\n"
                     "Fix the problem, then retry with:\n"
                     f"  curl -X POST {api.base}/jobs/{job_id}/retry")
        print(f"\r  {what}... {int(time.time() - started)}s", end="", flush=True)
        time.sleep(every)


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        sys.exit("\nNo input available; run this in an interactive terminal.")


def load_keywords_file(path: Path, max_queries: int | None) -> tuple[list[str], dict]:
    """Reads a plain keywords file (one search phrase per line, # comments) -- the format --keywords-file has
    always accepted, and scripts/make_keywords.py writes. Returns (terms, provenance); provenance carries the
    file path and, if a .meta.json sidecar sits next to it, which API/model/tokens produced it (docs/LOGGING.md
    "Where a keywords file came from")."""
    terms = [ln.strip().strip("\"'") for ln in path.expanduser().read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    print(f"Read {len(terms)} keyword(s) from {path}")
    if max_queries is not None and max_queries > 0:
        rounds = -(-len(terms) // max_queries)          # ceil
        print(f"  Searching {max_queries} keyword(s) per round -> {rounds} round(s) to cover all of them "
              f"(type 'more' at the asset prompt for each next round)")
    provenance = {"file": str(path)}
    meta_path = path.expanduser().with_suffix(".meta.json")
    if meta_path.exists():
        try:
            provenance.update(json.loads(meta_path.read_text(encoding="utf-8")))
            usage_bit = f", {provenance['usage']['total_tokens']} tokens" if provenance.get("usage") else ""
            print(f"  Provenance: {provenance.get('provider', '?')}/{provenance.get('model', '?')}{usage_bit} ({meta_path})")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  (couldn't read provenance sidecar {meta_path}: {e})")
    return terms, provenance


def library_sets_for(subject: str) -> list[dict]:
    """Every approved-keywords set the pipeline has saved for this exact subject (library/keywords/<slug>/
    <job id>.json -- written automatically once a job's keywords are approved (Gate 1½), see docs/RUNNING.md "Keyword
    files, always"), newest first. An unreadable entry is skipped with a note rather than crashing the run."""
    out = []
    for p in sorted((LIBRARY_KEYWORDS_DIR / slug(subject)).glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  (skipping unreadable saved set {p}: {e})")
    return sorted(out, key=lambda d: d.get("approved_at", ""), reverse=True)


def choose_library_set(subject: str) -> tuple[list[str], dict] | None:
    """Offers previously-approved keyword sets for this subject, if any, so a later video on the same topic
    doesn't need a fresh LLM call (or fresh typing) to get back to the same keywords. Only ever READS the
    saved file -- picking one just copies its terms into THIS job, which gets its own fresh
    keywords_proposed.json/keywords_approved.json once it goes through keyword approval (Gate 1½) again; nothing already saved
    is modified. Returns None (generate fresh instead) if there's nothing to offer, or the person skips."""
    sets = library_sets_for(subject)
    if not sets:
        return None
    print(f"\nFound {len(sets)} saved keyword set(s) for '{subject}':")
    for i, s in enumerate(sets, 1):
        terms = s.get("keywords") or []
        preview = ", ".join(terms[:6]) + (", ..." if len(terms) > 6 else "")
        print(f"  {i}. {s.get('approved_at', '?')} -- job {s.get('job_id', '?')}, {len(terms)} keyword(s): {preview}")
    ans = ask(f"Reuse one of these (1-{len(sets)}), or press Enter to generate fresh keywords instead: ")
    if not ans.strip():
        return None
    try:
        chosen = sets[int(ans.strip()) - 1]
    except (ValueError, IndexError):
        print("  That didn't match one of the numbers above -- generating fresh keywords instead.")
        return None
    terms = list(chosen.get("keywords") or [])
    provenance = {"reused_from_job": chosen.get("job_id"),
                  "reused_from_file": str(LIBRARY_KEYWORDS_DIR / slug(subject) / f"{chosen.get('job_id')}.json"),
                  "originally_approved_at": chosen.get("approved_at")}
    print(f"  Reusing {len(terms)} keyword(s) from job {chosen.get('job_id')} -- you can still add, drop or reject them at keyword approval (Gate 1½, if it's not auto-approved for this job).")
    return terms, provenance


def edit_keywords_draft(subject: str) -> tuple[list[str], dict | None]:
    """--keywords manual with no --keywords-file and nothing to reuse: rather than silently guessing keywords
    from the subject, write an empty, editable keywords file and wait for you to fill it in -- still no LLM
    call, just a file instead of a blind guess. Leaving it empty (pressing Enter with no edits) falls back to
    that old subject-derived guess, same as --keywords manual alone always did."""
    (LIBRARY_KEYWORDS_DIR / "_drafts").mkdir(parents=True, exist_ok=True)
    path = LIBRARY_KEYWORDS_DIR / "_drafts" / f"{slug(subject)}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    path.write_text(f"# Keywords for: {subject}  (one search phrase per line, # lines are ignored)\n"
                     "# e.g. victorian london street, whitechapel 1888, period newspaper front page\n\n",
                     encoding="utf-8")
    print(f"\n--keywords manual makes no LLM call, and no --keywords-file was given -- an editable keywords "
          f"file is waiting for you instead, at:\n  {path}\nOpen it, add one search phrase per line, save it, "
          "then come back here.")
    ask("Press Enter once you've saved your edits (or right away to skip and fall back to the subject itself): ")
    terms = [ln.strip().strip("\"'") for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    if not terms:
        print("  No keywords found in the file -- falling back to the subject itself, same as before.")
        return [], None
    print(f"  Read {len(terms)} keyword(s) from {path}")
    return terms, {"file": str(path), "manual_edit": True}


def resolve_keywords_source(subject: str, keywords_arg: str) -> tuple[str, list[str], dict | None]:
    """Called when no --keywords-file was given explicitly. Checks the reusable library for this subject
    first, regardless of --keywords: reusing a saved set always means `manual` (the terms are already
    decided, no LLM call needed). If nothing's offered or it's skipped, falls through to --keywords as given:
    `llm` makes its own call as always; `manual` opens an editable file (edit_keywords_draft) instead of
    silently guessing from the subject."""
    reused = choose_library_set(subject)
    if reused is not None:
        terms, provenance = reused
        return "manual", terms, provenance
    if keywords_arg == "manual":
        terms, provenance = edit_keywords_draft(subject)
        return "manual", terms, provenance
    return keywords_arg, [], None


def edit_script_draft(job: dict) -> str:
    """Same idea as edit_keywords_draft() above: rather than trying to do a real multi-line editor in the
    terminal, write the current script to a plain file next to the job's other files and let you edit it
    in whatever editor you already have open. Returns whatever's in the file when you come back (which may
    be unchanged, if you just want to approve as written)."""
    path = Path("projects") / f"{job['slug']}-{job['id']}" / "script_draft.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(job["script"], encoding="utf-8")
    print(f"\nOpen {path}, edit the narration, save it, then come back here.")
    ask("Press Enter once you've saved your edits (or right away to approve it as written): ")
    return path.read_text(encoding="utf-8")


def script_gate(api: Api, job: dict, reviewer: str = "") -> dict:
    """Gate 1 (docs/PIPELINE_STAGES.md, 2026-09-25 reorder): the freshly-written narration, before anything
    else -- no keywords, no sourcing, no scenes with clips yet. Approving with an edited draft re-splits it
    into fresh scenes (Orchestrator.approve_script); there's nothing scene-specific to lose at this point."""
    while True:
        print("\n=== GATE 1: script ===  (why: keywords, sourcing, and scenes are all built from this narration)\n")
        print(job["script"])
        words = len(job["script"].split())
        print(f"\nLength: {words} words, about {round(words / 2.6)} seconds when spoken.")
        print(f"\nPREFER CLICKING? Open {api.base}/review/{job['id']} in your browser (edit inline), submit there, then type 'web' here.")
        ans = ask("\n'yes' to approve as written, 'edit' to rewrite it first, 'web' = I used the browser page, 'no' to ask for a rewrite: ").lower()
        if ans == "web":
            print("  Waiting for you to submit in the browser page...")
            while api.call("GET", f"/jobs/{job['id']}")["state"] == "script_review":
                time.sleep(3)
            job = api.call("GET", f"/jobs/{job['id']}")
            return script_gate(api, job, reviewer) if job["state"] == "script_review" else job
        if ans == "no":
            note = ask("What should change? (this guides the rewrite): ")
            job = api.call("POST", f"/jobs/{job['id']}/script/reject", {"feedback": note, "reviewer": reviewer})
            job = wait_for(api, job["id"], {"script_review"}, "rewriting the script")
            continue
        if ans == "edit":
            edited = edit_script_draft(job)
            return api.call("POST", f"/jobs/{job['id']}/script/approve", {"reviewer": reviewer, "edited_script": edited})
        return api.call("POST", f"/jobs/{job['id']}/script/approve", {"reviewer": reviewer})


def keyword_gate(api: Api, job: dict, reviewer: str = "", file_terms: list[str] | None = None) -> dict:
    while True:
        # Auto-approved by default since the 2026-09-25 reorder (docs/PIPELINE_STAGES.md) -- this only ever
        # runs at all when the job set providers.options.auto_approve_keywords: false, restoring it as a
        # real gate ("Gate 1½" in the docs, between script and asset review).
        print("\n=== GATE 1½: keywords ===  (why: everything after this is built from what you pick)\n")
        kws = job["keywords"]
        for i, k in enumerate(kws, 1):
            extra = f"  vol~{k['search_volume']}" if k.get("search_volume") else ""
            print(f"  {i:>2}. {k['term']}{extra}")
        ans = ask("\nType the numbers to KEEP (e.g. 1,3,4), 'all' (or just press Enter) to keep every one, or 'no' to reject them: ").lower() or "all"
        if ans == "no":
            note = ask("What was wrong? (this guides the next attempt): ")
            job = api.call("POST", f"/jobs/{job['id']}/keywords/reject", {"feedback": note, "reviewer": reviewer})
            return wait_for(api, job["id"], {"keywords_review"}, "finding new keywords")
        try:
            idx = list(range(1, len(kws) + 1)) if ans == "all" else [int(x) for x in ans.replace(" ", "").split(",") if x]
            chosen = [kws[i - 1]["id"] for i in idx]
        except (ValueError, IndexError):
            print("  That didn't look right, try again.")
            continue
        if file_terms:
            print(f"  Adding {len(file_terms)} keywords from your keywords file.")
        extra = ask("Add your own keywords? (comma separated, no quotes needed, or Enter to skip): ")
        typed = [t.strip().strip("\"'") for t in extra.split(",") if t.strip().strip("\"'")]
        terms = list(dict.fromkeys((file_terms or []) + typed))
        return api.call("POST", f"/jobs/{job['id']}/keywords/review",
                        {"approved_ids": chosen, "extra_terms": terms, "reviewer": reviewer})


RISK_MARK = {"high": "HIGH  ", "medium": "MEDIUM", "low": "low   "}


def show_asset(i: int, a: dict) -> None:
    v = a.get("vetting") or {}
    tag = RISK_MARK.get(v.get("risk"), "??????")
    unusable = "" if v.get("usable", True) else "  [CAN'T BE USED]"
    print(f"\n  {i:>2}. [{tag}] ({score_text(a)}) {a['source']}: {a['title'] or a['id']}{unusable}")
    print(f"      license: {a.get('license') or '(none found)'}   by: {a.get('author') or '(unknown)'}")
    print(f"      from: {a.get('page_url') or a.get('source_url') or '(unknown)'}")
    print(f"      file: projects/.../{a['rel_path']}")
    for f in v.get("flags", []):
        if f["severity"] != "info":
            print(f"      - {f['rule']} ({f['severity']}): {f['message']}\n        evidence: {f['evidence']}")


def score_text(a: dict) -> str:
    rel = (a.get("vetting") or {}).get("relevance")
    return "score n/a" if rel is None else f"score {round(rel * 100)}%"


def off_topic(a: dict) -> bool:
    return any(f["rule"] == "RELEVANCE_LOW" for f in (a.get("vetting") or {}).get("flags", []))


def asset_gate(api: Api, job: dict, reviewer: str) -> dict:
    while True:
        print("\n=== GATE 2: assets ===  (why: only what you approve here can appear in the video)")
        print("The machine flagged risks below and said why. It did NOT filter anything: you decide.")
        assets = job["assets"]
        on = [(i, a) for i, a in enumerate(assets, 1) if not off_topic(a)]
        off = [(i, a) for i, a in enumerate(assets, 1) if off_topic(a)]
        for n in job.get("source_notes", []):
            if n.get("warning"):
                print(f"\n  WARNING: {n['warning']}")
        thr = round(float(job["providers"]["options"].get("min_relevance", 0.5)) * 100)
        print(f"\nScoring: each asset gets a 0-100% relevance score (share of a keyword's words found in its title/description/tags). Details: docs/SCORING.md")
        print(f"Showing {len(on)} at or above {thr}%. {len(off)} below {thr}% are hidden (numbers are unchanged; type 'hidden' to list them).")
        for i, a in on:
            show_asset(i, a)
        print(f"\nPREFER CLICKING? Open {api.base}/review/{job['id']} in your browser (thumbnails, scores, Use/Reject), submit there, then type 'web' here.")
        print("\nOpen the files in the folder shown above to look at them. Reasons are also saved in DECISIONS.md.")
        ans = ask("\nNumbers to APPROVE (e.g. 1,3,4), 'ok' = every on-topic, non-high-risk, usable one, 'web' = I used the browser page, 'hidden' = list the below-threshold ones, 'more' = search again, 'none': ").lower()
        if ans == "web":
            print("  Waiting for you to submit in the browser page...")
            while api.call("GET", f"/jobs/{job['id']}")["state"] == "assets_review":
                time.sleep(3)
            job = wait_for(api, job["id"], {"assets_review", "scenes_review"}, "working on your decisions", every=4)
            return asset_gate(api, job, reviewer) if job["state"] == "assets_review" else job
        if ans == "hidden":
            for i, a in off:
                print(f"  {i:>3}. ({score_text(a)}) [{a['source']}] {(a['title'] or a['id'])[:70]}")
            continue
        if ans == "more":
            fb = ask("What was wrong with these? ")
            extra = ask("New search terms? (comma separated, or Enter): ")
            job = api.call("POST", f"/jobs/{job['id']}/assets/reject",
                           {"feedback": fb, "extra_queries": [t for t in extra.split(",") if t.strip()], "reviewer": reviewer})
            return asset_gate(api, wait_for(api, job["id"], {"assets_review"}, "searching again"), reviewer)
        try:
            if ans == "ok":
                yes = {i for i, a in enumerate(assets, 1) if (a.get("vetting") or {}).get("risk") != "high" and (a.get("vetting") or {}).get("usable", True) and not off_topic(a)}
            elif ans == "none":
                yes = set()
            else:
                yes = {int(x) for x in ans.replace(" ", "").split(",") if x}
            if any(i < 1 or i > len(assets) for i in yes):
                raise ValueError
        except ValueError:
            print("  That didn't look right, try again.")
            continue
        decisions, bad = {}, False
        for i, a in enumerate(assets, 1):
            v = a.get("vetting") or {}
            if i in yes:
                if not v.get("usable", True):
                    print(f"  #{i} can't be used ({v.get('summary', '')}). Pick again."); bad = True; break
                note = ""
                if v.get("risk") == "high":
                    note = ask(f"  #{i} is HIGH risk. Why is it OK to use? (required): ")
                    if not note:
                        print("  A reason is required for high-risk assets."); bad = True; break
                decisions[a["id"]] = {"decision": "approve", "note": note}
            else:
                decisions[a["id"]] = {"decision": "reject", "note": "not chosen at the asset review"}
        if bad:
            continue
        job = api.call("POST", f"/jobs/{job['id']}/assets/review", {"decisions": decisions, "reviewer": reviewer})
        return api.call("POST", f"/jobs/{job['id']}/assets/approve", {"reviewer": reviewer})


def scene_gate(api: Api, job: dict, reviewer: str = "") -> dict:
    while True:
        print("\n=== GATE 3: scenes ===  (why: this is your last chance before the slow render)\n")
        # job["script"] is frozen at whatever the writer first drafted -- it's never updated when you edit a
        # scene's narration below, so showing it here would go stale the moment you make your first edit.
        # This is the CURRENT script instead: today's scene order + narration, exactly what will be spoken.
        print("SCRIPT (current):\n" + "\n\n".join(s["narration"] for s in job["scenes"]) + "\n")
        for i, s in enumerate(job["scenes"], 1):
            clip = Path(s["clip_path"]).name if s.get("clip_path") else "(NO CLIP - approve more assets or add footage to library/clips)"
            why = f"\n     clip chosen because: {s['clip_reason']}" if s.get("clip_reason") else ""
            print(f"  {i}. [{clip}]{why}\n     {s['narration']}")
        words = sum(len(sc["narration"].split()) for sc in job["scenes"])
        print(f"\nLength: {words} words, about {round(words / 2.6)} seconds when spoken.")
        order = ask("New order? e.g. 2,1,3  |  'edit 2' to change the text of scene 2  |  Enter keeps everything: ")
        if order.lower().startswith("edit"):
            try:
                n = int(order.split()[1])
                scene = job["scenes"][n - 1]
            except (ValueError, IndexError):
                print(f"  Write it like: edit 2   (a scene number from 1 to {len(job['scenes'])})")
                continue
            print(f"  Current text: {scene['narration']}")
            new = ask("  New text (one line, Enter cancels): ")
            if new:
                job = api.call("PATCH", f"/jobs/{job['id']}/scenes", {"edits": {scene["id"]: {"narration": new}}, "reviewer": reviewer})
            continue
        if order:
            try:
                n = len(job["scenes"])
                idx = [int(x) for x in order.replace(" ", "").split(",")]
                if sorted(idx) != list(range(1, n + 1)):
                    raise ValueError
                job = api.call("PATCH", f"/jobs/{job['id']}/scenes", {"order": [job["scenes"][i - 1]["id"] for i in idx], "reviewer": reviewer})
                continue                       # show the new order and ask again
            except (ValueError, IndexError):
                print(f"  Use every number 1-{len(job['scenes'])} exactly once.")
                continue
        ans = ask("Approve and render? (yes / no): ").lower()
        if ans in ("y", "yes"):
            return api.call("POST", f"/jobs/{job['id']}/scenes/approve", {"reviewer": reviewer})
        note = ask("What should change? (guides the rewrite): ")
        job = api.call("POST", f"/jobs/{job['id']}/scenes/reject", {"feedback": note, "reviewer": reviewer})
        job = wait_for(api, job["id"], {"scenes_review"}, "rewriting scenes")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subject", nargs="?", help="what the video is about")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--keywords", default="llm", help="keyword provider: llm or manual (no API key needed)")
    ap.add_argument("--sources", default="wikipedia,commons",
                    help='where to pull text/images from, comma separated: wikipedia,commons,pexels,folder ("" = your own clips only)')
    ap.add_argument("--reviewer", default="", help="your name, written to the decision log next to every choice you make")
    ap.add_argument("--niche", default="", choices=["", "true_crime", "conspiracy", "science", "pet_product", "food_bakery"],
                     help="one of the 5 core content niches (optional): biases keyword phrasing toward that "
                          "niche's aesthetic and adds a niche_evaluation (relevance/aesthetic_fit/risk/action) "
                          "to every asset at Gate 2 -- see docs/NICHES.md")
    ap.add_argument("--script-style", default="", choices=["", "true_crime_mystery", "stem_science", "dtc_marketing", "math_cs"],
                     help="one of 4 scriptwriter voices (optional, independent of --niche): replaces the "
                          "scriptwriter's own source-grounded prompt with a retention-mechanics-tuned persona "
                          "and word-count target at Gate 3 -- see docs/SCRIPT_STYLES.md")
    ap.add_argument("--urls", metavar="FILE", help="text file of URLs to pull videos from, one per line: URL | note | position "
                    "(adds the 'urls' source). Pass '-' instead of a file to paste them straight into the terminal "
                    "(one per line, blank line or Ctrl-D to finish) -- no file to save first")
    ap.add_argument("--resume", metavar="JOB_ID", help="continue an existing job (retries it first if it failed)")
    ap.add_argument("--keywords-file", metavar="FILE", help="text file of keywords, one per line (# for comments). Added as approved keywords; searched in batches of max_queries")
    ap.add_argument("--min-relevance", type=float, default=0.5, help="hide assets scoring below this (0 to 1) at the asset review; default 0.5")
    ap.add_argument("--max-queries", type=int, default=None,
                    help="how many approved keywords to search per sourcing round for THIS job (overrides config/pipeline.toml's "
                         "[sources] max_queries, default 8). Raise it with a big --keywords-file so one round covers more of it; "
                         "type 'more' at the asset prompt (or 'Search again' in the browser) for the next batch either way")
    ap.add_argument("--per-query", type=int, default=None,
                    help="how many photos each source keeps PER KEYWORD for THIS job (overrides config/pipeline.toml's [sources] "
                         "per_query, default 4). If keyword search isn't turning up enough good photos, raising this pulls more "
                         "candidates per keyword for the relevance score + the review page's min-score slider to filter, instead "
                         "of needing better keywords")
    ap.add_argument("--videos-per-query", type=int, default=None,
                    help="same as --per-query but for video clips (overrides [sources] videos_per_query, default 2)")
    ap.add_argument("--quiet", action="store_true", help="don't stream the pipeline's activity log while waiting")
    ap.add_argument("--debug", action="store_true", help="stream DEBUG detail too (every HTTP request); the full log is always in the project's logs/ folder")
    ap.add_argument("--no-color", action="store_true", help="plain-text activity log, no ANSI colors (auto-off "
                     "already when output isn't a terminal, e.g. piped to a file; same as setting NO_COLOR)")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()

    api = Api(args.api)
    LOG["show"], LOG["level"] = not args.quiet, "DEBUG" if args.debug else "INFO"
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    file_terms: list[str] = []
    keywords_provenance: dict | None = None
    keywords_provider = args.keywords
    reviewer = args.reviewer or ask("Your name (recorded in the decision log next to your choices): ")

    if args.resume:
        # Pick up a job the script was following earlier (for example one that failed and was fixed).
        job = api.call("GET", f"/jobs/{args.resume}")
        if job["state"] == "failed":
            print(f"Job {job['id']} had failed ({job.get('error') or 'no details'}). Retrying it...")
            api.call("POST", f"/jobs/{job['id']}/retry", {"reviewer": reviewer})
        print(f"Resuming job {job['id']}  ->  folder: projects/{job['slug']}-{job['id']}/")
        sources = job["providers"].get("sources") or []
    else:
        subject = args.subject or ask("What is the video about? ")
        # Where these keywords actually came from (docs/LOGGING.md "Where a keywords file came from") always
        # travels into this job's decision log below, and (docs/RUNNING.md "Keyword files, always") this job's
        # own keywords_proposed.json/keywords_approved.json get written no matter which path below was taken.
        if args.keywords_file:
            file_terms, keywords_provenance = load_keywords_file(Path(args.keywords_file), args.max_queries)
        else:
            keywords_provider, file_terms, keywords_provenance = resolve_keywords_source(subject, args.keywords)
        sources = [x.strip() for x in args.sources.split(",") if x.strip()]
        options = {"min_relevance": args.min_relevance}
        if args.max_queries is not None:
            options["max_queries"] = args.max_queries
        if args.per_query is not None:
            options["per_query"] = args.per_query
        if args.videos_per_query is not None:
            options["videos_per_query"] = args.videos_per_query
        if file_terms and keywords_provider == "manual":
            options["seed_keywords"] = file_terms          # keyword approval will show exactly these keywords
            if keywords_provenance:
                options["keywords_provenance"] = keywords_provenance
        if args.urls:
            if args.urls == "-":
                if sys.stdin.isatty():
                    print("Paste URLs, one per line (optionally 'URL | note | position'). Blank line or Ctrl-D to finish:")
                    lines = []
                    while True:
                        try:
                            ln = input()
                        except EOFError:
                            break
                        if not ln.strip():
                            break
                        lines.append(ln)
                    text = "\n".join(lines)
                else:
                    text = sys.stdin.read()          # piped, e.g. `pbpaste | python3 scripts/poc.py --urls - ...`
                origin = "pasted input"
            else:
                text = Path(args.urls).expanduser().read_text(encoding="utf-8")
                origin = args.urls
            options["urls"] = [dict(zip(("url", "note", "position"), [p.strip() for p in ln.split("|")]))
                               for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
            if "urls" not in sources:
                sources.append("urls")
            print(f"Read {len(options['urls'])} URL(s) from {origin}")
        create_body = {"subject": subject, "reviewer": reviewer,
                       "providers": {"keywords": keywords_provider, "sources": sources, "options": options}}
        if args.niche:
            create_body["niche"] = args.niche
        if args.script_style:
            create_body["script_style"] = args.script_style
        job = api.call("POST", "/jobs", create_body)
        print(f"Created job {job['id']}  ->  folder: projects/{job['slug']}-{job['id']}/")
        api.call("POST", f"/jobs/{job['id']}/start", {"reviewer": reviewer})

    # Each gate only runs if the job hasn't already passed it (matters when resuming).
    order = JOB_STATE_ORDER
    def at(name: str) -> bool:
        state = api.call("GET", f"/jobs/{job['id']}")["state"]
        return (order.index(state) if state in order else len(order)) <= order.index(name)
    if at("script_review"):
        job = wait_for(api, job["id"], {"script_review"}, "writing the script")
        job = script_gate(api, job, reviewer)
    if at("keywords_review"):
        job = wait_for(api, job["id"], {"keywords_review"}, "researching keywords")
        job = keyword_gate(api, job, reviewer, [] if keywords_provider == "manual" else file_terms)
    if sources and at("assets_review"):
        job = wait_for(api, job["id"], {"assets_review"}, "pulling and vetting sources", every=4)
        job = asset_gate(api, job, reviewer)
    if at("scenes_review"):
        job = wait_for(api, job["id"], {"scenes_review"}, "writing script and picking clips", every=4)
        job = scene_gate(api, job, reviewer)
    job = wait_for(api, job["id"], {"completed"}, "rendering video (a few minutes is normal)", every=5)

    dest = Path(args.out) / f"{job['id']}.mp4"
    api.download(f"/jobs/{job['id']}/output", dest)
    print(f"Done! Your video: {dest.resolve()}")
    cred = Path("projects") / f"{job['slug']}-{job['id']}" / "DESCRIPTION_CREDITS.txt"
    if cred.exists() and cred.read_text().strip():
        print("\nPaste this into your video description (credits required by the licenses):\n")
        print(cred.read_text())
    print(f"Every decision (yours and the machine's): projects/{job['slug']}-{job['id']}/DECISIONS.md")
    print_timing(api, job["id"])
    print_usage(api, job["id"])


if __name__ == "__main__":
    main()
