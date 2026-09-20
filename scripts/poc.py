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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


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


def wait_for(api: Api, job_id: str, states: set[str], what: str, every: float = 3.0) -> dict:
    """Poll until the job reaches one of `states`. Exits with the reason if it fails."""
    started = time.time()
    while True:
        job = api.call("GET", f"/jobs/{job_id}")
        if job["state"] in states:
            print()
            return job
        if job["state"] in ("failed", "cancelled"):
            print()
            sys.exit(f"The job {job['state']}: {job.get('error') or 'no details'}\n"
                     "Fix the problem, then retry with:\n"
                     f"  curl -X POST {api.base}/jobs/{job_id}/retry")
        print(f"\r  {what}... {int(time.time() - started)}s", end="", flush=True)
        time.sleep(every)


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        sys.exit("\nNo input available; run this in an interactive terminal.")


def keyword_gate(api: Api, job: dict, reviewer: str = "") -> dict:
    while True:
        print("\n=== GATE 1: keywords ===  (why: everything after this is built from what you pick)\n")
        kws = job["keywords"]
        for i, k in enumerate(kws, 1):
            extra = f"  vol~{k['search_volume']}" if k.get("search_volume") else ""
            print(f"  {i:>2}. {k['term']}{extra}")
        ans = ask("\nType the numbers to KEEP (e.g. 1,3,4), 'all', or 'no' to reject them: ").lower()
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
        extra = ask("Add your own keywords? (comma separated, no quotes needed, or Enter to skip): ")
        return api.call("POST", f"/jobs/{job['id']}/keywords/review",
                        {"approved_ids": chosen, "extra_terms": [t.strip().strip("\"'") for t in extra.split(",") if t.strip().strip("\"'")], "reviewer": reviewer})


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
        print("SCRIPT:\n" + job["script"] + "\n")
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
    ap.add_argument("--urls", metavar="FILE", help="text file of URLs to pull videos from, one per line: URL | note | position (adds the 'urls' source)")
    ap.add_argument("--resume", metavar="JOB_ID", help="continue an existing job (retries it first if it failed)")
    ap.add_argument("--min-relevance", type=float, default=0.5, help="hide assets scoring below this (0 to 1) at the asset review; default 0.5")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()

    api = Api(args.api)
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
        sources = [x.strip() for x in args.sources.split(",") if x.strip()]
        options = {"min_relevance": args.min_relevance}
        if args.urls:
            text = Path(args.urls).expanduser().read_text(encoding="utf-8")
            options["urls"] = [dict(zip(("url", "note", "position"), [p.strip() for p in ln.split("|")]))
                               for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
            if "urls" not in sources:
                sources.append("urls")
            print(f"Read {len(options['urls'])} URL(s) from {args.urls}")
        job = api.call("POST", "/jobs", {"subject": subject, "reviewer": reviewer,
                                         "providers": {"keywords": args.keywords, "sources": sources, "options": options}})
        print(f"Created job {job['id']}  ->  folder: projects/{job['slug']}-{job['id']}/")
        api.call("POST", f"/jobs/{job['id']}/start", {"reviewer": reviewer})

    # Each gate only runs if the job hasn't already passed it (matters when resuming).
    order = ["created", "keywords_running", "keywords_review", "sourcing_running", "vetting_running",
             "assets_review", "scenes_running", "scenes_review", "rendering", "completed"]
    def at(name: str) -> bool:
        state = api.call("GET", f"/jobs/{job['id']}")["state"]
        return (order.index(state) if state in order else len(order)) <= order.index(name)
    if at("keywords_review"):
        job = wait_for(api, job["id"], {"keywords_review"}, "researching keywords")
        job = keyword_gate(api, job, reviewer)
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


if __name__ == "__main__":
    main()
