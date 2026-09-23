"""HTTP API (Starlette). Your web UI talks to this; it holds no business logic.

  POST /jobs                          {subject, providers?}     create
  POST /jobs/{id}/start                                          -> keywords_running
  POST /jobs/{id}/keywords/review     {approved_ids, extra_terms?, reviewer}   GATE 1 approve
  POST /jobs/{id}/keywords/reject     {feedback, reviewer}                     GATE 1 re-run
  POST /jobs/{id}/assets/review       {decisions:{asset_id:{decision,note,label?,label_reason?,label_note?,
                                        duplicate_of_asset_id?}}, reviewer}    GATE 2 per asset
  POST /jobs/{id}/assets/approve      {reviewer, note?}                        GATE 2 done -> scenes
  POST /jobs/{id}/assets/reject       {feedback, extra_queries?, max_queries?, per_query?, videos_per_query?,
                                        extra_urls_text?, reviewer}   GATE 2 search again / next batch
                                        (max_queries alone, no feedback, just pulls more keywords) / more
                                        photos per keyword / add links (one per line, "url | note | position")
  POST /jobs/{id}/assets/label        {asset_id, label, reviewer, reason?, note?, duplicate_of_asset_id?}
                                        save a Use/Duplicate/Irrelevant label any time, any job state (docs/EVALUATION.md)
  GET  /methods                        what each relevance-scoring method+version does (docs/SCORING_CHANGELOG.md)
  GET  /label-reasons                  the three labels and the suggested (extensible) reason list
  PATCH /jobs/{id}/scenes             {order?, edits?, reviewer}               reorder / edit
  POST /jobs/{id}/scenes/{scene_id}/upload         multipart: file, note?, reviewer?   GATE 3 drag a file
                                        from your computer onto a scene (adds it as an asset, assigns it if
                                        not high-risk / a note is given)
  POST /jobs/{id}/scenes/{scene_id}/from-url       {url, note?, reviewer?}     GATE 3 drop a video/photo
                                        link onto a scene (yt-dlp/direct download, same as the 'urls' source)
  POST /jobs/{id}/scenes/{scene_id}/approve-pending {asset_id, note?, reviewer?}  finish approving+assigning
                                        an asset the two routes above left pending (high risk, no note yet)
  POST /jobs/{id}/scenes/approve      {reviewer}                               GATE 3 approve -> rendering
  POST /jobs/{id}/scenes/reject       {feedback, reviewer}                     GATE 3 re-run
  POST /jobs/{id}/back-to-keywords    | /back-to-assets | /cancel | /retry
  GET  /jobs, /jobs/{id}, /jobs/{id}/output, /providers, /health
  GET  /jobs/{id}/log                      step-by-step activity log (?level=DEBUG|INFO|WARN|ERROR&tail=N&after=N&format=json)
  GET  /jobs/{id}/decisions           the decision log (add ?format=md for DECISIONS.md)
  GET  /jobs/{id}/timing               provenance: total time, time per stage, time waiting on you (docs/LOGGING.md)
  GET  /jobs/{id}/usage                provenance: LLM calls, tokens in/out, $ cost, by model (docs/LOGGING.md)
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
from ..core.orchestrator import LABELS, Orchestrator, SUGGESTED_LABEL_REASONS
from ..core.store import JobNotFound, JobStore
from ..sources.base import LoggedHttp, SourceContext
from ..sources.upload import asset_from_upload
from ..sources.urls import UrlListSource
from ..stages.registry import Registry, build_default_registry
from ..vetting.method_registry import as_json as method_definitions_json
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

    async def scene_upload(r: Request):
        """Drag a file from your computer onto a scene card. multipart/form-data: file, note?, reviewer?."""
        job_id, scene_id = r.path_params["id"], r.path_params["scene_id"]
        form = await r.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            raise HTTPException(400, "no file in the upload")
        data = await upload.read()
        if not data:
            raise HTTPException(400, "uploaded file was empty")
        project_dir = orch.store.job_dir(job_id)
        try:
            asset = asset_from_upload(data, upload.filename, project_dir / "sources" / "upload" / "files", project_dir,
                                       note=str(form.get("note", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return await orch.add_scene_asset(job_id, scene_id, asset, reviewer=str(form.get("reviewer", "")), note=str(form.get("note", "")))

    async def scene_from_url(r: Request):
        """Drop a video/photo link onto a scene card. JSON: {url, note?, reviewer?}."""
        job_id, scene_id = r.path_params["id"], r.path_params["scene_id"]
        d = await body(r)
        url = (d.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url is required")
        job = orch.get(job_id)                       # 404 if unknown, before spending time on a download
        project_dir = orch.store.job_dir(job_id)
        src_dir = project_dir / "sources" / "urls"
        (src_dir / "files").mkdir(parents=True, exist_ok=True)
        ctx = SourceContext(project_dir=project_dir, dir=src_dir, http=LoggedHttp(src_dir, "urls"), subject=job.subject,
                            known_urls={a.source_url for a in job.assets if a.source == "urls"},
                            known_hashes={a.sha256 for a in job.assets if a.sha256})
        try:
            asset = await UrlListSource().fetch_one(url, d.get("note", ""), len(job.assets) + 1, ctx)
        except Exception as exc:
            raise HTTPException(422, f"couldn't get that link: {type(exc).__name__}: {exc}") from None
        return await orch.add_scene_asset(job_id, scene_id, asset, reviewer=d.get("reviewer", ""), note=d.get("note", ""))

    async def scene_approve_pending(r: Request):
        d = await body(r)
        return await orch.approve_pending_scene_asset(r.path_params["id"], d.get("asset_id", ""), r.path_params["scene_id"],
                                                       reviewer=d.get("reviewer", ""), note=d.get("note", ""))

    async def scenes_approve(r: Request):
        d = await body(r)
        return await orch.approve_scenes(r.path_params["id"], reviewer=d.get("reviewer", ""))

    async def scenes_reject(r: Request):
        d = await body(r)
        return await orch.reject_scenes(r.path_params["id"], d.get("feedback", ""), reviewer=d.get("reviewer", ""))

    async def assets_review(r: Request):
        d = await body(r)
        return await orch.review_assets(r.path_params["id"], d.get("decisions", {}), reviewer=d.get("reviewer", ""))

    async def assets_label(r: Request):
        d = await body(r)
        return await orch.label_asset(r.path_params["id"], d.get("asset_id", ""), d.get("label", ""),
                                       reviewer=d.get("reviewer", ""), reason=d.get("reason", ""), note=d.get("note", ""),
                                       duplicate_of_asset_id=d.get("duplicate_of_asset_id", ""))

    async def methods_view(_: Request) -> JSONResponse:
        return JSONResponse(method_definitions_json())

    async def label_reasons_view(_: Request) -> JSONResponse:
        return JSONResponse({"labels": list(LABELS), "suggested_reasons": SUGGESTED_LABEL_REASONS})

    async def assets_approve(r: Request):
        d = await body(r)
        return await orch.approve_assets(r.path_params["id"], reviewer=d.get("reviewer", ""), note=d.get("note", ""))

    async def assets_reject(r: Request):
        d = await body(r)
        return await orch.reject_assets(r.path_params["id"], d.get("feedback", ""), d.get("extra_queries"),
                                         reviewer=d.get("reviewer", ""), max_queries=d.get("max_queries"),
                                         per_query=d.get("per_query"), videos_per_query=d.get("videos_per_query"),
                                         extra_urls_text=d.get("extra_urls_text", ""))

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

    async def usage_view(r: Request):
        orch.get(r.path_params["id"])                       # 404 if unknown
        return JSONResponse(orch.usage_summary(r.path_params["id"]))

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
        Route(f"{P}/assets/label", wrap(assets_label), methods=["POST"]),
        Route(f"{P}/assets/{{asset_id}}/file", wrap(asset_file), methods=["GET"]),
        Route("/methods", methods_view, methods=["GET"]),
        Route("/label-reasons", label_reasons_view, methods=["GET"]),
        Route(f"{P}/decisions", wrap(decisions), methods=["GET"]),
        Route(f"{P}/log", wrap(log_view), methods=["GET"]),
        Route(f"{P}/timing", wrap(timing_view), methods=["GET"]),
        Route(f"{P}/usage", wrap(usage_view), methods=["GET"]),
        Route(f"{P}/back-to-assets", wrap(back_assets), methods=["POST"]),
        Route(f"{P}/scenes", wrap(scenes_edit), methods=["PATCH"]),
        Route(f"{P}/scenes/{{scene_id}}/upload", wrap(scene_upload), methods=["POST"]),
        Route(f"{P}/scenes/{{scene_id}}/from-url", wrap(scene_from_url), methods=["POST"]),
        Route(f"{P}/scenes/{{scene_id}}/approve-pending", wrap(scene_approve_pending), methods=["POST"]),
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
