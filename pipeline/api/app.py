"""HTTP API (Starlette). Your web UI talks to this; it holds no business logic.

  POST /jobs                          {subject, providers?}     create
  POST /jobs/{id}/start                                          -> keywords_running
  POST /jobs/{id}/keywords/review     {approved_ids, extra_terms?}   GATE 1 approve
  POST /jobs/{id}/keywords/reject     {feedback}                     GATE 1 re-run
  PATCH /jobs/{id}/scenes             {order?, edits?}               reorder / edit
  POST /jobs/{id}/scenes/approve                                     GATE 2 approve -> rendering
  POST /jobs/{id}/scenes/reject       {feedback}                     GATE 2 re-run
  POST /jobs/{id}/back-to-keywords    | /cancel | /retry
  GET  /jobs, /jobs/{id}, /jobs/{id}/output, /providers, /health
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
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ..core import state_machine as sm
from ..core.models import Job, ProviderChoice
from ..core.orchestrator import Orchestrator
from ..core.store import JobNotFound, JobStore
from ..stages.registry import Registry, build_default_registry


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
        data_dir = settings.get("storage", {}).get("data_dir", "data")
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
            for kind, name in (("keywords", providers.keywords), ("scenes", providers.scenes), ("render", providers.render)):
                if name not in orch.registry.available()[kind]:
                    raise ValueError(f"unknown {kind} provider '{name}'")
        job = await orch.create_job(d.get("subject", ""), providers)
        return JSONResponse(_job_json(job), status_code=201)

    async def get_job(r: Request):
        return orch.get(r.path_params["id"])

    async def start(r: Request):
        return await orch.start(r.path_params["id"])

    async def kw_review(r: Request):
        d = await body(r)
        return await orch.review_keywords(r.path_params["id"], d.get("approved_ids", []), d.get("extra_terms"))

    async def kw_reject(r: Request):
        d = await body(r)
        return await orch.reject_keywords(r.path_params["id"], d.get("feedback", ""))

    async def scenes_edit(r: Request):
        d = await body(r)
        return await orch.edit_scenes(r.path_params["id"], d.get("order"), d.get("edits"))

    async def scenes_approve(r: Request):
        return await orch.approve_scenes(r.path_params["id"])

    async def scenes_reject(r: Request):
        d = await body(r)
        return await orch.reject_scenes(r.path_params["id"], d.get("feedback", ""))

    async def back(r: Request):
        return await orch.back_to_keywords(r.path_params["id"])

    async def cancel(r: Request):
        return await orch.cancel(r.path_params["id"])

    async def retry(r: Request):
        return await orch.retry(r.path_params["id"])

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

    P = "/jobs/{id}"
    routes = [
        Route("/health", health),
        Route("/providers", providers),
        Route("/jobs", wrap(list_jobs), methods=["GET"]),
        Route("/jobs", wrap(create_job), methods=["POST"]),
        Route(P, wrap(get_job), methods=["GET"]),
        Route(f"{P}/start", wrap(start), methods=["POST"]),
        Route(f"{P}/keywords/review", wrap(kw_review), methods=["POST"]),
        Route(f"{P}/keywords/reject", wrap(kw_reject), methods=["POST"]),
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
