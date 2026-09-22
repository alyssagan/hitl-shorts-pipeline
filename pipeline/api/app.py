"""HTTP API (Starlette). Your web UI talks to this; it holds no business logic.

  POST /jobs                          {subject, providers?}     create
  POST /jobs/{id}/start                                          -> keywords_running
  POST /jobs/{id}/keywords/review     {approved_ids, extra_terms?, reviewer}   GATE 1 approve
  POST /jobs/{id}/keywords/reject     {feedback, reviewer}                     GATE 1 re-run
  POST /jobs/{id}/assets/review       {decisions:{asset_id:{decision,note}}, reviewer}   GATE 2 per asset
  POST /jobs/{id}/assets/approve      {reviewer, note?}                        GATE 2 done -> scenes
  POST /jobs/{id}/assets/reject       {feedback, extra_queries?, reviewer}     GATE 2 search again
  PATCH /jobs/{id}/scenes             {order?, edits?, reviewer}               reorder / edit
  POST /jobs/{id}/scenes/approve      {reviewer}                               GATE 3 approve -> rendering
  POST /jobs/{id}/scenes/reject       {feedback, reviewer}                     GATE 3 re-run
  POST /jobs/{id}/back-to-keywords    | /back-to-assets | /cancel | /retry
  GET  /jobs, /jobs/{id}, /jobs/{id}/output, /providers, /health
  GET  /jobs/{id}/log                      step-by-step activity log (?level=DEBUG|INFO|WARN|ERROR&tail=N&after=N&format=json)
  GET  /jobs/{id}/decisions           the decision log (add ?format=md for DECISIONS.md)
  GET  /jobs/{id}/timing               provenance: total time, time per stage, time waiting on you (docs/LOGGING.md)
  GET  /jobs/{id}/assets/{asset_id}/file   the downloaded image/video (for previews)
  GET  /review, /review/{id}               the review web page (thumbnails, scores, Use/Reject)
"""
from __future__ import annotations

import asyncio
import contextlib
import tomllib
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from ..core import joblog
from ..core import state_machine as sm
from ..core.models import Job, ProviderChoice
from ..core.orchestrator import Orchestrator
from ..core.store import JobNotFound, JobStore
from ..stages.registry import Registry, build_default_registry
from .review_page import PAGE


def load_settings(path: str | Path = "config/pipeline.toml") -> dict[str, Any]:
    p = Path(path)
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _job_json(job: Job) -> dict[str, Any]:
    data = job.model_dump(mode="json")
    data["allowed_events"] = sm.allowed_events(job)
    return data


def create_app(orch: Orchestrator | None = None, settings: dict[str, Any] | None = None) -> Starlette:
    settings = settings if settings is not None else load_settings()
    if orch is None:
        data_dir = settings.get("storage", {}).get("data_dir", "projects")
        orch = Orchestrator(JobStore(data_dir), build_default_registry(settings), settings)

    tasks: set[asyncio.Task] = set()

    def launch(job_id: str) -> None:
        t = asyncio.get_running_loop().create_task(orch.run_pending(job_id))
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    orch.on_running = launch

    async def body(request: Request) -> dict[str, Any]:
        if not (await request.body()):
            return {}
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, "body must be JSON") from None
        if not isinstance(data, dict):
            raise HTTPException(400, "body must be a JSON object")
        return data

    def wrap(handler):
        async def endpoint(request: Request) -> JSONResponse:
            try:
                result = await handler(request)
            except JobNotFound:
                return JSONResponse({"error": "job not found"}, status_code=404)
            except sm.TransitionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
            except (ValueError, KeyError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=422)
            return result if isinstance(result, Response) else JSONResponse(_job_json(result))
        return endpoint

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def providers(_: Request) -> JSONResponse:
        return JSONResponse(orch.registry.available())

    async def list_jobs(_: Request) -> JSONResponse:
        return JSONResponse([_job_json(j) for j in orch.store.list()])

    async def create_job(r: Request):
        d = await body(r)
        providers = ProviderChoice(**d["providers"]) if d.get("providers") else None
        if providers:
            avail = orch.registry.available()
            for kind, name in (("keywords", providers.keywords), ("scenes", providers.scenes), ("render", providers.render)):
                if name not in avail[kind]:
                    raise ValueError(f"unknown {kind} provider '{name}'")
            for name in providers.sources:
                if name not in avail["sources"]:
                    raise ValueError(f"unknown source '{name}'. available: {avail['sources']}")
        job = await orch.create_job(d.get("subject", ""), providers, reviewer=d.get("reviewer", ""))
        return JSONResponse(_job_json(job), status_code=201)

    async def get_job(r: Request):
        return orch.get(r.path_params["id"])

    async def start(r: Request):
        d = await body(r)
        return await orch.start(r.path_params["id"], reviewer=d.get("reviewer", ""))

    async def kw_review(r: Request):
        d = await body(r)
        return await orch.review_keywords(r.path_params["id"], d.get("approved_ids", []), d.get("extra_terms"),
                                          reviewer=d.get("reviewer", ""), note=d.get("note", ""))

    async def kw_reject(r: Request):
        d = await body(r)
        return await orch.reject_keywords(r.path_params["id"], d.get("feedback", ""), reviewer=d.get("reviewer", ""))

    async def scenes_edit(r: Request):
        d = await body(r)
        return await orch.edit_scenes(r.path_params["id"], d.get("order"), d.get("edits"), reviewer=d.get("reviewer", ""))

    async def scenes_approve(r: Request):
        d = await body(r)
        return await orch.approve_scenes(r.path_params["id"], reviewer=d.get("reviewer", ""))

    async def scenes_reject(r: Request):
        d = await body(r)
        return await orch.reject_scenes(r.path_params["id"], d.get("feedback", ""), reviewer=d.get("reviewer", ""))

    async def assets_review(r: Request):
        d = await body(r)
        return await orch.review_assets(r.path_params["id"], d.get("decisions", {}), reviewer=d.get("reviewer", ""))

    async def assets_approve(r: Request):
        d = await body(r)
        return await orch.approve_assets(r.path_params["id"], reviewer=d.get("reviewer", ""), note=d.get("note", ""))

    async def assets_reject(r: Request):
        d = await body(r)
        return await orch.reject_assets(r.path_params["id"], d.get("feedback", ""), d.get("extra_queries"), reviewer=d.get("reviewer", ""))

    async def back(r: Request):
        d = await body(r)
        return await orch.back_to_keywords(r.path_params["id"], reviewer=d.get("reviewer", ""))

    async def back_assets(r: Request):
        d = await body(r)
        return await orch.back_to_assets(r.path_params["id"], reviewer=d.get("reviewer", ""))

    async def cancel(r: Request):
        d = await body(r)
        return await orch.cancel(r.path_params["id"], reviewer=d.get("reviewer", ""))

    async def retry(r: Request):
        d = await body(r)
        return await orch.retry(r.path_params["id"], reviewer=d.get("reviewer", ""))

    async def decisions(r: Request):
        orch.get(r.path_params["id"])                       # 404 if unknown
        log = orch.store.decisions(r.path_params["id"])
        if r.query_params.get("format") == "md":
            return Response(log.md.read_text(encoding="utf-8") if log.md.exists() else "", media_type="text/markdown")
        ok, bad = log.verify()
        return JSONResponse({"intact": ok, "first_bad_entry": bad, "entries": log.entries()})

    async def asset_file(r: Request):
        job = orch.get(r.path_params["id"])
        a = next((x for x in job.assets if x.id == r.path_params["asset_id"]), None)
        if a is None or not Path(a.path).exists():
            return JSONResponse({"error": "asset not found"}, status_code=404)
        return FileResponse(a.path, media_type=a.mime or None)

    async def log_view(r: Request):
        orch.get(r.path_params["id"])                       # 404 if unknown
        q = r.query_params
        lines, nxt = joblog.read(orch.store.job_dir(r.path_params["id"]), after=int(q.get("after", 0) or 0),
                                 min_level=q.get("level", "INFO"), tail=int(q["tail"]) if q.get("tail") else None)
        if q.get("format") == "json":
            return JSONResponse({"lines": lines, "next": nxt})
        return Response("\n".join(lines) + ("\n" if lines else ""), media_type="text/plain; charset=utf-8")

    async def timing_view(r: Request):
        orch.get(r.path_params["id"])                       # 404 if unknown
        return JSONResponse(orch.timing_summary(r.path_params["id"]))

    async def output(r: Request):
        job = orch.get(r.path_params["id"])
        if not job.output_path or not Path(job.output_path).exists():
            return JSONResponse({"error": "no output yet"}, status_code=404)
        return FileResponse(job.output_path, media_type="video/mp4", filename=f"{job.id}.mp4")

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        orch.resume_all()
        yield
        for t in list(tasks):
            t.cancel()

    async def review_page(_: Request):
        return HTMLResponse(PAGE)

    P = "/jobs/{id}"
    routes = [
        Route("/review", review_page, methods=["GET"]),
        Route("/review/{id}", review_page, methods=["GET"]),
        Route("/health", health),
        Route("/providers", providers),
        Route("/jobs", wrap(list_jobs), methods=["GET"]),
        Route("/jobs", wrap(create_job), methods=["POST"]),
        Route(P, wrap(get_job), methods=["GET"]),
        Route(f"{P}/start", wrap(start), methods=["POST"]),
        Route(f"{P}/keywords/review", wrap(kw_review), methods=["POST"]),
        Route(f"{P}/keywords/reject", wrap(kw_reject), methods=["POST"]),
        Route(f"{P}/assets/review", wrap(assets_review), methods=["POST"]),
        Route(f"{P}/assets/approve", wrap(assets_approve), methods=["POST"]),
        Route(f"{P}/assets/reject", wrap(assets_reject), methods=["POST"]),
        Route(f"{P}/assets/{{asset_id}}/file", wrap(asset_file), methods=["GET"]),
        Route(f"{P}/decisions", wrap(decisions), methods=["GET"]),
        Route(f"{P}/log", wrap(log_view), methods=["GET"]),
        Route(f"{P}/timing", wrap(timing_view), methods=["GET"]),
        Route(f"{P}/back-to-assets", wrap(back_assets), methods=["POST"]),
        Route(f"{P}/scenes", wrap(scenes_edit), methods=["PATCH"]),
        Route(f"{P}/scenes/approve", wrap(scenes_approve), methods=["POST"]),
        Route(f"{P}/scenes/reject", wrap(scenes_reject), methods=["POST"]),
        Route(f"{P}/back-to-keywords", wrap(back), methods=["POST"]),
        Route(f"{P}/cancel", wrap(cancel), methods=["POST"]),
        Route(f"{P}/retry", wrap(retry), methods=["POST"]),
        Route(f"{P}/output", wrap(output), methods=["GET"]),
    ]
    return Starlette(routes=routes, lifespan=lifespan)


def app_factory() -> Starlette:  # uvicorn --factory pipeline.api.app:app_factory
    return create_app()
