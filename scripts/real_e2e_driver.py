#!/usr/bin/env python3
"""Standalone, non-interactive driver for a REAL end-to-end run of the pipeline -- real Gemini/Pexels/
Pixabay/Unsplash/Smithsonian/Groq calls, a real Orchestrator/Registry/JobStore (pipeline.stages.registry.
build_default_registry), the same wiring pipeline/api/app.py's create_app() uses in production. Built to
demonstrate the two-pass shot-list fallback feature (docs/ROADMAP.md "two-pass hybrid") actually working
against live sources, end to end through Gate 3 (SCENES_REVIEW) -- without needing MoneyPrinterTurbo/
Docker: SCENES_RUNNING's clip-matching pass never calls MPT when the job has asset sources configured
(AssetClipSource, not MptClient, does the matching -- pipeline/stages/scenes/mpt.py), [script]'s writer
uses its own Gemini call instead of MPT's script endpoint when a script LLM key is configured, and
per-scene audio previews are off by default ([mpt] generate_scene_audio = false). Only the final
RENDERING stage (after Gate 3's approve_scenes) actually needs MoneyPrinterTurbo -- this driver
deliberately stops before that.

Each invocation is its own short-lived process (this is meant to be re-invoked across separate shell
calls, not left running): it builds a fresh Orchestrator/JobStore/Registry from config/pipeline.toml +
the environment (cheap -- no persistent connections held open), does ONE thing, prints JSON, and exits.
All job state lives on disk via JobStore, so calls can be split across as many separate invocations as
needed; nothing here depends on any earlier invocation's in-memory state.

Run from the repository root, with real API keys in the environment (e.g. `set -a; source .env; set +a`
first, so this never needs to print or hard-code any key):

  python3 scripts/real_e2e_driver.py create "<subject>" [--sources wikipedia,pexels,pixabay,unsplash,smithsonian] [--reviewer NAME]
  python3 scripts/real_e2e_driver.py run <job_id> [--max-steps N]     # drives run_pending() forward
                                                                       # until a review gate or a terminal state
  python3 scripts/real_e2e_driver.py status <job_id>
  python3 scripts/real_e2e_driver.py approve-script <job_id> [--reviewer NAME]
  python3 scripts/real_e2e_driver.py assets <job_id>                  # list current assets: id/status/title/
                                                                       # query/risk/usable/relevance
  python3 scripts/real_e2e_driver.py approve-all-assets <job_id> [--reviewer NAME]
                                                                       # approve every pending, usable asset;
                                                                       # reject every pending, unusable one
  python3 scripts/real_e2e_driver.py close-gate2 <job_id> [--reviewer NAME]
                                                                       # approve_assets() -- may bounce back to
                                                                       # SOURCING_RUNNING (Pass 2) instead of
                                                                       # advancing to SCENES_RUNNING
  python3 scripts/real_e2e_driver.py scenes <job_id>                  # list scenes + clip assignment
  python3 scripts/real_e2e_driver.py decisions <job_id> [--action NAME] [--limit N]
                                                                       # tail the decision log (optionally
                                                                       # filtered to one `action`), most
                                                                       # recent last
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import types
from pathlib import Path


def _install_tomllib_shim() -> None:
    """Compatibility shim, this script only: a shell driving the user's own repo may be Python 3.10
    (tomllib is 3.11+, and this particular shell has no route to pip-install a 3.10 backport --
    installing one would mean writing a package into the user's repo folder just to unblock a test
    harness, which we avoid). Rather than reimplementing a TOML parser (real risk of parsing something
    subtly wrong), this replays REAL tomllib's own parse of config/pipeline.toml and config/
    query_groups.toml -- computed once under a real Python 3.11 tomllib and cached alongside this
    script in _toml_shim_cache.json, keyed by each file's exact sha256. If either file's bytes ever
    change, the hash won't match anything cached and this raises loudly (TOMLDecodeError) rather than
    silently serving stale config -- regenerate the cache in that case (parse both files with a real
    tomllib and dump {sha256(text): parsed_dict, ...} to _toml_shim_cache.json). Every environment that
    already has tomllib built in (Python 3.11+, e.g. this repo's own Docker image) never touches this
    path at all."""
    try:
        import tomllib  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    cache_path = Path(__file__).resolve().parent / "_toml_shim_cache.json"
    cache: dict[str, dict] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    mod = types.ModuleType("tomllib")

    class TOMLDecodeError(ValueError):
        pass

    def loads(text: str) -> dict:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if h not in cache:
            raise TOMLDecodeError(
                f"no real tomllib available in this Python, and this content's sha256 ({h[:12]}...) isn't in "
                f"{cache_path.name} -- regenerate the cache (parse this file with a real Python 3.11+ tomllib "
                f"and add its {{sha256: parsed_dict}} entry) rather than trusting a guessed parse")
        return cache[h]

    def load(fp) -> dict:
        data = fp.read()
        return loads(data.decode("utf-8") if isinstance(data, bytes) else data)

    mod.loads, mod.load, mod.TOMLDecodeError = loads, load, TOMLDecodeError
    sys.modules["tomllib"] = mod


_install_tomllib_shim()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.core.models import ProviderChoice, RUNNING_STATES  # noqa: E402
from pipeline.core.orchestrator import Orchestrator            # noqa: E402
from pipeline.core.store import JobStore                       # noqa: E402
from pipeline.stages.registry import build_default_registry    # noqa: E402


def load_settings(path: str = "config/pipeline.toml") -> dict:
    """Same two lines as pipeline.api.app.load_settings -- duplicated here (rather than importing that
    module) so this script never pulls in FastAPI/the API layer just to parse one TOML file."""
    import tomllib
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def build_orch() -> Orchestrator:
    settings = load_settings()
    data_dir = settings.get("storage", {}).get("data_dir", "projects")
    return Orchestrator(JobStore(data_dir), build_default_registry(settings), settings)


def job_summary(job) -> dict:
    return {
        "id": job.id, "slug": job.slug, "state": job.state.value, "subject": job.subject,
        "error": job.error,
        "scenes": len(job.scenes),
        "keywords_total": len(job.keywords),
        "approved_keywords": [{"term": k.term, "group": k.group, "scene_index": k.scene_index,
                                "alternatives": k.alternatives} for k in job.approved_keywords],
        "assets_total": len(job.assets),
        "assets_pending": sum(1 for a in job.assets if a.status == "pending"),
        "assets_approved": sum(1 for a in job.assets if a.status == "approved"),
        "assets_rejected": sum(1 for a in job.assets if a.status == "rejected"),
        "fallback_only_queries": job.providers.options.get("fallback_only_queries"),
        "fallback_queries_tried": job.providers.options.get("fallback_queries_tried"),
    }


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


async def cmd_create(args) -> None:
    orch = build_orch()
    providers = ProviderChoice(sources=[s for s in args.sources.split(",") if s] if args.sources else [])
    job = await orch.create_job(args.subject, providers, reviewer=args.reviewer)
    job = await orch.start(job.id, reviewer=args.reviewer)
    _print(job_summary(job))


async def cmd_run(args) -> None:
    orch = build_orch()
    job = orch.get(args.job_id)
    steps = 0
    while job.state in RUNNING_STATES and steps < args.max_steps:
        job = await orch.run_pending(args.job_id)
        steps += 1
        print(f"-> {job.state.value}", file=sys.stderr)
        if job.state.value == "failed":
            break
    _print(job_summary(job))


async def cmd_status(args) -> None:
    orch = build_orch()
    _print(job_summary(orch.get(args.job_id)))


async def cmd_approve_script(args) -> None:
    orch = build_orch()
    job = await orch.approve_script(args.job_id, reviewer=args.reviewer)
    _print(job_summary(job))


async def cmd_assets(args) -> None:
    orch = build_orch()
    job = orch.get(args.job_id)
    rows = [{"id": a.id, "status": a.status, "kind": a.kind, "title": a.title, "query": a.query,
             "source": a.source, "risk": a.vetting.risk if a.vetting else None,
             "usable": a.vetting.usable if a.vetting else None,
             "relevance": a.vetting.relevance if a.vetting else None} for a in job.assets]
    _print(rows)


async def cmd_approve_all_assets(args) -> None:
    orch = build_orch()
    job = orch.get(args.job_id)
    decisions = {}
    for a in job.assets:
        if a.status != "pending":
            continue
        usable = bool(a.vetting and a.vetting.usable)
        risk = a.vetting.risk if a.vetting else "unvetted"
        d = {"decision": "approve" if usable else "reject"}
        if usable and risk == "high":
            d["note"] = "Real end-to-end test of the two-pass shot-list fallback feature -- approving to exercise the Gate 2 -> Gate 3 flow."
        decisions[a.id] = d
    if not decisions:
        _print({"note": "no pending assets"})
        return
    job = await orch.review_assets(args.job_id, decisions, reviewer=args.reviewer)
    _print(job_summary(job))


async def cmd_close_gate2(args) -> None:
    orch = build_orch()
    job = await orch.approve_assets(args.job_id, reviewer=args.reviewer)
    _print(job_summary(job))


async def cmd_scenes(args) -> None:
    orch = build_orch()
    job = orch.get(args.job_id)
    rows = [{"index": s.index, "narration": s.narration[:80], "search_terms": s.search_terms,
             "clip_path": s.clip_path, "asset_id": s.asset_id, "clip_reason": s.clip_reason}
            for s in job.scenes]
    _print(rows)


async def cmd_decisions(args) -> None:
    orch = build_orch()
    rows = [json.loads(line) for line in orch.store.decisions(args.job_id).jsonl.read_text().splitlines() if line.strip()]
    if args.action:
        rows = [r for r in rows if r.get("action") == args.action]
    _print(rows[-args.limit:])


COMMANDS = {
    "create": cmd_create, "run": cmd_run, "status": cmd_status, "approve-script": cmd_approve_script,
    "assets": cmd_assets, "approve-all-assets": cmd_approve_all_assets, "close-gate2": cmd_close_gate2,
    "scenes": cmd_scenes, "decisions": cmd_decisions,
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("subject")
    c.add_argument("--sources", default="")
    c.add_argument("--reviewer", default="test-harness")

    c = sub.add_parser("run")
    c.add_argument("job_id")
    c.add_argument("--max-steps", type=int, default=10, dest="max_steps")

    c = sub.add_parser("status")
    c.add_argument("job_id")

    c = sub.add_parser("approve-script")
    c.add_argument("job_id")
    c.add_argument("--reviewer", default="test-harness")

    c = sub.add_parser("assets")
    c.add_argument("job_id")

    c = sub.add_parser("approve-all-assets")
    c.add_argument("job_id")
    c.add_argument("--reviewer", default="test-harness")

    c = sub.add_parser("close-gate2")
    c.add_argument("job_id")
    c.add_argument("--reviewer", default="test-harness")

    c = sub.add_parser("scenes")
    c.add_argument("job_id")

    c = sub.add_parser("decisions")
    c.add_argument("job_id")
    c.add_argument("--action", default="")
    c.add_argument("--limit", type=int, default=20)

    args = p.parse_args()
    asyncio.run(COMMANDS[args.cmd](args))


if __name__ == "__main__":
    main()
