"""HTTP API (Starlette). Your web UI talks to this; it holds no business logic.

  POST /jobs                          {subject, providers?}     create
  POST /jobs/{id}/start                                          -> keywords_running
  POST /jobs/{id}/keywords/review     {approved_ids, extra_terms?, reviewer}   GATE 1 approve
  POST /jobs/{id}/keywords/reject     {feedback, reviewer}                     GATE 1 re-run
  POST /jobs/{id}/assets/review       {decisions:{asset_id:{decision,note,label?,label_reason?,label_note?,
                                        duplicate_of_asset_id?}}, reviewer}    GATE 2 per asset
  POST /jobs/{id}/assets/approve      {reviewer, note?}                        GATE 2 done -> scenes
  POST /jobs/{id}/assets/reject       {feedback, extra_queries?, max_queries?, per_query?, videos_per_query?,
                                        extra_urls_text?, folder_files?, reviewer}   GATE 2 search again / next
                                        batch (max_queries alone, no feedback, just pulls more keywords) / more
                                        photos per keyword. extra_urls_text queues links (one per line, "url |
                                        note | position") for the NEXT sourcing round -- no per-link feedback;
                                        prefer /assets/add-url below for anything the UI does. folder_files
                                        queues specific relative paths from the server's own-footage folder
                                        (#10, see GET /jobs/{id}/folder-files) for the next round, each a plain
                                        path or {path, note} -- explicit per-job selection, not the whole folder.
  POST /jobs/{id}/assets/add-url      {url, note?, position?, reviewer}   GATE 2 "Add links": download and add
                                        ONE link synchronously (#9), same yt-dlp/direct pattern Gate 3's
                                        from-url uses, with an immediate success/failure result. The asset
                                        lands `pending`, same as a searched one -- never auto-approved.
  POST /jobs/{id}/youtube-search       {query, count?}   Gate 2 "Find more": list up to `count` (default 5,
                                        max 10) YouTube candidates via yt-dlp's own search, metadata only --
                                        nothing downloaded or added. Pick one by posting its `url` to
                                        /assets/add-url above, same as any pasted link.
  GET  /jobs/{id}/folder-files        what's currently in the server's configured own-footage folder (#10),
                                        each with whether it's already selected for this job -- read-only,
                                        pick from this list for assets/reject's folder_files.
  POST /jobs/{id}/assets/label        {asset_id, label, reviewer, reason?, note?, duplicate_of_asset_id?}
                                        save a Use/Duplicate/Irrelevant label any time, any job state (docs/EVALUATION.md)
  POST /jobs/{id}/assets/{asset_id}/identity   {status, depicts?, case_connection?, identity_evidence?, notes?,
                                        reviewer}   #7/#13: record a human identity determination (unverified/
                                        verified/disputed) -- the only thing that ever moves it. Any job state.
  POST /jobs/{id}/assets/{asset_id}/rights     {status, evidence?, notes?, reviewer}   #8/#13: record a human
                                        rights determination (public_domain/cc0/open_license/paid_license/
                                        unresolved), independent of identity/decision. Any job state.
  POST /jobs/{id}/assets/{asset_id}/category   {category, reviewer}   #7/#13: move an asset's category --
                                        promoting to verified_case or flagging reconstruction is always this
                                        explicit action, never inferred. Any job state.
  GET  /jobs/{id}/asset-report        #13: per-source photo/video/research counts, category/identity/rights
                                        breakdowns, and the job's running LLM cost (same numbers as /usage).
  GET  /methods                        what each relevance-scoring method+version does (docs/SCORING_CHANGELOG.md)
  GET  /label-reasons                  the three labels and the suggested (extensible) reason list
  GET  /render-settings                {aspect}   the deployment's render aspect ratio (config/pipeline.toml
                                        [mpt] aspect) -- Gate 3's crop tool needs this to draw a correctly
                                        proportioned crop box, same as the render stage's own crop math
  POST /jobs/{id}/case-reference       {canonical_name, reviewer}     set the case's canonical name (#6)
  POST /jobs/{id}/case-reference/facts {kind, text, detail?, source_links?, status?, conflict_note?, reviewer}
                                        add one fact -- always human-entered, never written by search/vetting
  PATCH /jobs/{id}/case-reference/facts/{fact_id}   {any subset of the above fields, reviewer}   edit a fact
  POST /jobs/{id}/case-reference/facts/{fact_id}/remove   {reviewer}   remove a fact
  POST /jobs/{id}/visual-checklist/generate   {reviewer}   seed DRAFT items from approved keywords'
                                        visual_needed/entity text (skips terms already linked to an item)
  POST /jobs/{id}/visual-checklist     {label, linked_keyword_term?, group?, reviewer}   add an item by hand
  PATCH /jobs/{id}/visual-checklist/{item_id}   {status?, note?, asset_id?, reviewer}   edit an item
  POST /jobs/{id}/visual-checklist/{item_id}/remove   {reviewer}   remove an item
  GET  /jobs/{id}/visual-coverage     #12: pre-render report -- which visual_checklist items are still
                                        unresolved (needed/candidates_found), which "fulfilled" case-group
                                        items point at an asset that isn't actually categorized case material
                                        (category_mismatches), both cross-referenced with scenes via
                                        search_terms, plus the 5 remediation options for each. Read-only, any
                                        job state -- Gate 3 calls this before "Approve and render", not just
                                        when actually approving. has_checklist=false means the job never used
                                        the checklist feature, so nothing here is gating it.
  PATCH /jobs/{id}/scenes             {order?, edits?, reviewer}               reorder / edit
  POST /jobs/{id}/scenes/{scene_id}/upload         multipart: file, note?, reviewer?   GATE 3 drag a file
                                        from your computer onto a scene (adds it as an asset, assigns it if
                                        not high-risk / a note is given)
  POST /jobs/{id}/scenes/{scene_id}/from-url       {url, note?, reviewer?}     GATE 3 drop a video/photo
                                        link onto a scene (yt-dlp/direct download, same as the 'urls' source)
  POST /jobs/{id}/scenes/{scene_id}/approve-pending {asset_id, note?, reviewer?}  finish approving+assigning
                                        an asset the two routes above left pending (high risk, no note yet)
  PATCH /jobs/{id}/scenes/{scene_id}/crop   {center_x?, center_y?, zoom?, reviewer}   GATE 3 manually frame
                                        this scene's clip -- overrides MoneyPrinterTurbo's own automatic
                                        center-crop (MPT has no hook to accept a crop itself; this pipeline
                                        bakes an actually-cropped copy at render time, see pipeline/stages/
                                        render/crop.py). center_x/center_y 0..1 (0.5,0.5 = MPT's own
                                        centering), zoom >=1.0. The scene needs a clip first, and any later
                                        clip_path change (a fresh drag/upload/picker swap) clears the crop --
                                        it was framed for the old clip, not the new one.
  POST /jobs/{id}/scenes/{scene_id}/crop/remove   {reviewer}   go back to MoneyPrinterTurbo's own auto-crop
  POST /jobs/{id}/scenes/approve      {reviewer, override_note?}               GATE 3 approve -> rendering.
                                        #12: refused if any visual_checklist item is still unresolved, unless
                                        override_note explains why it's OK to render past it anyway (both the
                                        override and exactly which items were left unresolved are logged).
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
from ..core.models import Job, JobState, ProviderChoice
from ..core.orchestrator import LABELS, Orchestrator, SUGGESTED_LABEL_REASONS
from ..core.store import JobNotFound, JobStore
from ..sources.base import LoggedHttp, SourceContext, SourceUnavailable
from ..sources.folder import list_available as list_folder_files, normalize_selection as normalize_folder_entry
from ..sources.upload import asset_from_upload
from ..sources.urls import UrlListSource
from ..sources.youtube_search import search_youtube
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
        known_urls = {a.source_url for a in job.assets if a.source == "urls"}
        if url in known_urls:
            raise HTTPException(422, "that link is already in this project")
        project_dir = orch.store.job_dir(job_id)
        src_dir = project_dir / "sources" / "urls"
        (src_dir / "files").mkdir(parents=True, exist_ok=True)
        ctx = SourceContext(project_dir=project_dir, dir=src_dir, http=LoggedHttp(src_dir, "urls"), subject=job.subject,
                            known_urls=known_urls, known_hashes={a.sha256 for a in job.assets if a.sha256})
        try:
            asset = await UrlListSource().fetch_one(url, d.get("note", ""), len(job.assets) + 1, ctx)
        except Exception as exc:
            raise HTTPException(422, f"couldn't get that link: {type(exc).__name__}: {exc}") from None
        return await orch.add_scene_asset(job_id, scene_id, asset, reviewer=d.get("reviewer", ""), note=d.get("note", ""))

    async def assets_add_url(r: Request):
        """Gate 2's "Add links" (#9): download ONE video/photo URL synchronously (same UrlListSource.fetch_one
        Gate 3's scene_from_url above uses) and add it as a normal pending asset for Gate 2 review. The frontend
        calls this once per pasted line so each link gets its own immediate success/failure result, instead of
        the old fire-and-forget behaviour where a whole batch was queued for the next sourcing round with no
        per-link feedback at all. JSON: {url, note?, position?, reviewer}."""
        job_id = r.path_params["id"]
        d = await body(r)
        url = (d.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url is required")
        job = orch.get(job_id)                       # 404 if unknown, before spending time on a download
        if job.state is not JobState.ASSETS_REVIEW:
            raise HTTPException(409, f"links can only be added during asset review (job is '{job.state.value}')")
        known_urls = {a.source_url for a in job.assets if a.source == "urls"}
        if url in known_urls:
            raise HTTPException(422, "that link is already in this project")
        project_dir = orch.store.job_dir(job_id)
        src_dir = project_dir / "sources" / "urls"
        (src_dir / "files").mkdir(parents=True, exist_ok=True)
        ctx = SourceContext(project_dir=project_dir, dir=src_dir, http=LoggedHttp(src_dir, "urls"), subject=job.subject,
                            known_urls=known_urls, known_hashes={a.sha256 for a in job.assets if a.sha256})
        try:
            asset = await UrlListSource().fetch_one(url, d.get("note", ""), len(job.assets) + 1, ctx, position=d.get("position", ""))
        except Exception as exc:
            raise HTTPException(422, f"couldn't get that link: {type(exc).__name__}: {exc}") from None
        return await orch.add_reviewable_asset(job_id, asset, reviewer=d.get("reviewer", ""), note=d.get("note", ""))

    async def youtube_search_view(r: Request):
        """Gate 2's "Find more" panel: list up to `count` YouTube candidates for `query` via yt-dlp's own
        search (pipeline/sources/youtube_search.py) -- metadata only, nothing downloaded or added to the job.
        Pick one by POSTing its `url` to assets_add_url above, same as any pasted link (still auto-flagged
        high risk, still needs a note). JSON: {query, count?} -> [{id, title, url, uploader, duration,
        thumbnail, upload_date, description}, ...]. Logged to sources/youtube_search/requests.jsonl like any
        other outbound call, success or failure."""
        job_id = r.path_params["id"]
        d = await body(r)
        query = (d.get("query") or "").strip()
        if not query:
            raise HTTPException(400, "query is required")
        raw_count = d.get("count")
        count = max(1, min(int(raw_count) if raw_count not in (None, "") else 5, 10))
        job = orch.get(job_id)                       # 404 if unknown
        if job.state is not JobState.ASSETS_REVIEW:
            raise HTTPException(409, f"YouTube search is only available during asset review (job is '{job.state.value}')")
        log_dir = orch.store.job_dir(job_id) / "sources" / "youtube_search"
        log = LoggedHttp(log_dir, "youtube_search")
        try:
            results = await search_youtube(query, count)
        except SourceUnavailable as exc:
            log._log(method="yt-dlp-search", url=f"ytsearch{count}:{query}", status=None,
                     purpose=f"search YouTube for '{query}'", error=str(exc))
            raise HTTPException(503, str(exc)) from None
        except Exception as exc:
            log._log(method="yt-dlp-search", url=f"ytsearch{count}:{query}", status=None,
                     purpose=f"search YouTube for '{query}'", error=str(exc))
            raise HTTPException(422, f"YouTube search failed: {exc}") from None
        log._log(method="yt-dlp-search", url=f"ytsearch{count}:{query}", status=200,
                 purpose=f"search YouTube for '{query}'", found=len(results))
        return JSONResponse(results)

    async def scene_approve_pending(r: Request):
        d = await body(r)
        return await orch.approve_pending_scene_asset(r.path_params["id"], d.get("asset_id", ""), r.path_params["scene_id"],
                                                       reviewer=d.get("reviewer", ""), note=d.get("note", ""))

    async def scene_crop_set(r: Request):
        d = await body(r)
        return await orch.set_scene_crop(r.path_params["id"], r.path_params["scene_id"],
                                         center_x=float(d.get("center_x", 0.5)), center_y=float(d.get("center_y", 0.5)),
                                         zoom=float(d.get("zoom", 1.0)), reviewer=d.get("reviewer", ""))

    async def scene_crop_remove(r: Request):
        d = await body(r)
        return await orch.remove_scene_crop(r.path_params["id"], r.path_params["scene_id"], reviewer=d.get("reviewer", ""))

    async def render_settings_view(_: Request) -> JSONResponse:
        # Job-independent (the whole deployment renders at one aspect ratio, config/pipeline.toml's
        # [mpt] aspect) -- Gate 3's crop tool needs this to draw a correctly-proportioned crop box and
        # to run the same crop_box() math client-side that pipeline/stages/render/crop.py runs server-side.
        return JSONResponse({"aspect": settings.get("mpt", {}).get("aspect", "9:16")})

    async def scenes_approve(r: Request):
        d = await body(r)
        return await orch.approve_scenes(r.path_params["id"], reviewer=d.get("reviewer", ""),
                                          override_note=d.get("override_note", ""))

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

    async def asset_identity(r: Request):
        d = await body(r)
        return await orch.set_asset_identity(r.path_params["id"], r.path_params["asset_id"], status=d.get("status", ""),
                                              depicts=d.get("depicts", ""), case_connection=d.get("case_connection", ""),
                                              identity_evidence=d.get("identity_evidence", ""), notes=d.get("notes", ""),
                                              reviewer=d.get("reviewer", ""))

    async def asset_rights(r: Request):
        d = await body(r)
        return await orch.set_asset_rights(r.path_params["id"], r.path_params["asset_id"], status=d.get("status", ""),
                                            evidence=d.get("evidence", ""), notes=d.get("notes", ""), reviewer=d.get("reviewer", ""))

    async def asset_category(r: Request):
        d = await body(r)
        return await orch.set_asset_category(r.path_params["id"], r.path_params["asset_id"], category=d.get("category", ""),
                                              reviewer=d.get("reviewer", ""))

    async def asset_report_view(r: Request):
        orch.get(r.path_params["id"])                       # 404 if unknown
        return JSONResponse(orch.asset_report(r.path_params["id"]))

    async def case_reference_name(r: Request):
        d = await body(r)
        return await orch.set_case_canonical_name(r.path_params["id"], d.get("canonical_name", ""),
                                                    reviewer=d.get("reviewer", ""))

    async def case_reference_fact_add(r: Request):
        d = await body(r)
        return await orch.add_case_fact(r.path_params["id"], d.get("kind", "other"), d.get("text", ""),
                                         detail=d.get("detail", ""), source_links=d.get("source_links"),
                                         status=d.get("status", "confirmed"), conflict_note=d.get("conflict_note", ""),
                                         reviewer=d.get("reviewer", ""))

    async def case_reference_fact_edit(r: Request):
        d = await body(r)
        return await orch.edit_case_fact(r.path_params["id"], r.path_params["fact_id"], text=d.get("text"),
                                          detail=d.get("detail"), source_links=d.get("source_links"),
                                          status=d.get("status"), conflict_note=d.get("conflict_note"),
                                          reviewer=d.get("reviewer", ""))

    async def case_reference_fact_remove(r: Request):
        d = await body(r)
        return await orch.remove_case_fact(r.path_params["id"], r.path_params["fact_id"], reviewer=d.get("reviewer", ""))

    async def visual_checklist_generate(r: Request):
        d = await body(r)
        return await orch.generate_visual_checklist(r.path_params["id"], reviewer=d.get("reviewer", ""))

    async def visual_checklist_add(r: Request):
        d = await body(r)
        return await orch.add_checklist_item(r.path_params["id"], d.get("label", ""),
                                              linked_keyword_term=d.get("linked_keyword_term", ""),
                                              group=d.get("group", "historical"), reviewer=d.get("reviewer", ""))

    async def visual_checklist_update(r: Request):
        d = await body(r)
        return await orch.update_checklist_item(r.path_params["id"], r.path_params["item_id"],
                                                  status=d.get("status"), note=d.get("note"),
                                                  asset_id=d.get("asset_id"), reviewer=d.get("reviewer", ""))

    async def visual_checklist_remove(r: Request):
        d = await body(r)
        return await orch.remove_checklist_item(r.path_params["id"], r.path_params["item_id"], reviewer=d.get("reviewer", ""))

    async def visual_coverage_view(r: Request):
        orch.get(r.path_params["id"])                       # 404 if unknown
        return JSONResponse(orch.check_visual_coverage(r.path_params["id"]))

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
                                         extra_urls_text=d.get("extra_urls_text", ""), folder_files=d.get("folder_files"))

    async def folder_files_list(r: Request):
        """What's currently sitting in the server's configured `library/scraped` folder (#10), for a human to
        pick specific files from for THIS job -- read-only, imports nothing by itself. See folder.py's
        list_available() and module docstring for why this exists (the folder used to be imported whole into
        any job listing `folder` as a source, with no per-job filtering at all)."""
        job = orch.get(r.path_params["id"])                       # 404 if unknown
        root = Path((settings.get("sources") or {}).get("folder_path", "library/scraped"))
        selected = {normalize_folder_entry(e)["path"] for e in job.providers.options.get("folder_files", [])}
        files = list_folder_files(root)
        for f in files:
            f["selected_for_this_job"] = f["path"] in selected
        return JSONResponse({"folder": str(root), "files": files})

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

    async def http_exception_json(_: Request, exc: HTTPException) -> JSONResponse:
        # Starlette's own default for a directly-raised HTTPException is a PLAIN TEXT body, not JSON --
        # every handler above (and the review page's api() helper, which reads body.error) assumes the
        # {"error": ...} shape wrap() below produces for ValueError/JobNotFound/etc. Without this, a
        # message like "couldn't get that link: ..." (#9) never reaches the UI: the frontend's fetch
        # can't parse it as JSON and silently falls back to the generic HTTP status text instead.
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

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
        Route(f"{P}/assets/add-url", wrap(assets_add_url), methods=["POST"]),
        Route(f"{P}/youtube-search", wrap(youtube_search_view), methods=["POST"]),
        Route(f"{P}/folder-files", wrap(folder_files_list), methods=["GET"]),
        Route(f"{P}/assets/label", wrap(assets_label), methods=["POST"]),
        Route(f"{P}/assets/{{asset_id}}/identity", wrap(asset_identity), methods=["POST"]),
        Route(f"{P}/assets/{{asset_id}}/rights", wrap(asset_rights), methods=["POST"]),
        Route(f"{P}/assets/{{asset_id}}/category", wrap(asset_category), methods=["POST"]),
        Route(f"{P}/asset-report", wrap(asset_report_view), methods=["GET"]),
        Route(f"{P}/assets/{{asset_id}}/file", wrap(asset_file), methods=["GET"]),
        Route(f"{P}/case-reference", wrap(case_reference_name), methods=["POST"]),
        Route(f"{P}/case-reference/facts", wrap(case_reference_fact_add), methods=["POST"]),
        Route(f"{P}/case-reference/facts/{{fact_id}}", wrap(case_reference_fact_edit), methods=["PATCH"]),
        Route(f"{P}/case-reference/facts/{{fact_id}}/remove", wrap(case_reference_fact_remove), methods=["POST"]),
        Route(f"{P}/visual-checklist/generate", wrap(visual_checklist_generate), methods=["POST"]),
        Route(f"{P}/visual-checklist", wrap(visual_checklist_add), methods=["POST"]),
        Route(f"{P}/visual-checklist/{{item_id}}", wrap(visual_checklist_update), methods=["PATCH"]),
        Route(f"{P}/visual-checklist/{{item_id}}/remove", wrap(visual_checklist_remove), methods=["POST"]),
        Route(f"{P}/visual-coverage", wrap(visual_coverage_view), methods=["GET"]),
        Route("/methods", methods_view, methods=["GET"]),
        Route("/label-reasons", label_reasons_view, methods=["GET"]),
        Route("/render-settings", render_settings_view, methods=["GET"]),
        Route(f"{P}/decisions", wrap(decisions), methods=["GET"]),
        Route(f"{P}/log", wrap(log_view), methods=["GET"]),
        Route(f"{P}/timing", wrap(timing_view), methods=["GET"]),
        Route(f"{P}/usage", wrap(usage_view), methods=["GET"]),
        Route(f"{P}/back-to-assets", wrap(back_assets), methods=["POST"]),
        Route(f"{P}/scenes", wrap(scenes_edit), methods=["PATCH"]),
        Route(f"{P}/scenes/{{scene_id}}/upload", wrap(scene_upload), methods=["POST"]),
        Route(f"{P}/scenes/{{scene_id}}/from-url", wrap(scene_from_url), methods=["POST"]),
        Route(f"{P}/scenes/{{scene_id}}/approve-pending", wrap(scene_approve_pending), methods=["POST"]),
        Route(f"{P}/scenes/{{scene_id}}/crop", wrap(scene_crop_set), methods=["PATCH"]),
        Route(f"{P}/scenes/{{scene_id}}/crop/remove", wrap(scene_crop_remove), methods=["POST"]),
        Route(f"{P}/scenes/approve", wrap(scenes_approve), methods=["POST"]),
        Route(f"{P}/scenes/reject", wrap(scenes_reject), methods=["POST"]),
        Route(f"{P}/back-to-keywords", wrap(back), methods=["POST"]),
        Route(f"{P}/cancel", wrap(cancel), methods=["POST"]),
        Route(f"{P}/retry", wrap(retry), methods=["POST"]),
        Route(f"{P}/output", wrap(output), methods=["GET"]),
    ]
    return Starlette(routes=routes, lifespan=lifespan, exception_handlers={HTTPException: http_exception_json})


def app_factory() -> Starlette:  # uvicorn --factory pipeline.api.app:app_factory
    return create_app()
