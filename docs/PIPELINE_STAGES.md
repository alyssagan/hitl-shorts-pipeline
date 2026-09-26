# Pipeline stages, stage by stage

Every machine step and every human gate, in the order the orchestrator actually runs them, with
exactly what each one reads, what it writes back onto the job, and what lands in the project
folder because of it. This is the file to check before changing stage order, adding a stage, or
wiring a new output file -- it is meant to always match the code; if you find a mismatch, that's
a bug in this doc, not in the code.

See also: [README.md "Where everything is saved"](../README.md#where-everything-is-saved) for the
project-folder layout as a whole, [docs/REVIEW_UI.md](REVIEW_UI.md) for what the review page shows
at each gate, and `pipeline/stages/base.py` for the `Protocol`s themselves.

**How to read "Writes (project folder)":** `SOURCES.md`, `VETTING_REPORT.md` and `SCRIPT.md` are
regenerated in full after *every* job mutation (`Orchestrator._write_derived_files`, called from both
`_mutate` and `_commit`) -- each one simply writes nothing until its stage has produced something to
show. `keywords_proposed.json` / `keywords_approved.json` and `CREDITS.md` are written once, at the
specific moment named below, not on every mutation.

**Reordered 2026-09-25.** The narration used to be written last, after keywords and assets were
already locked in, using whatever keywords had been approved. It's now written FIRST, grounded
directly on the subject (no keyword needed for that), and keywords are derived FROM the finished
script instead -- a 2-4 query SHOT LIST per scene (most specific first, then broader fallbacks;
2026-09-26), tagged with `Keyword.scene_index`/`.term`/`.alternatives` -- so each scene's own clip
search actually reflects what that scene says, and isn't sunk by one overly-specific phrase finding
nothing. Gate 1 is now "approve the narration," not
"approve the keywords." The keyword step itself still runs and still has a real review screen
(`keywords_review`), but it's auto-approved by default straight through, folded into Gate 2 (asset
review) rather than being its own stop -- see the `keywords_running` section below for how to turn
that back into a real human gate.

## 1. `script_running` -- machine

| | |
|---|---|
| **Code** | `pipeline/stages/scenes/mpt.py` (`MptSceneStage.write_script`), behind the `SceneStage` protocol -- the same class that later runs `scenes_running` (step 5), split into two methods |
| **Reads from job** | `job.subject` (research is fetched fresh from this, via whichever `research_source` factory the registry wired -- normally "wikipedia" -- skipped entirely if that source isn't configured, if `ctx.project_dir` is `None`, or if `job.references` is already non-empty); `job.script_feedback` (reviewer notes from an earlier rejection, if any) |
| **Returns** | `SceneResult(script, scenes, references)` -- `scenes` here are narration-only: no `clip_path`/`asset_id`/`audio_path` yet |
| **Writes to job** | `job.script = res.script`, `job.scenes = res.scenes`, `job.references.extend(res.references)` (`Orchestrator._apply_script`) |
| **Writes (project folder)** | `sources/wikipedia/...` (the research fetch, same shape as any other source adapter) if it ran; `SCRIPT.md` (narration only at this point -- no scenes/clips/audio yet to show) |
| **Then** | → **Gate 1** `script_review` |

**Gate 1 -- human** (`approve_script()` / `reject_script()`): approve the narration as written, or
edit it first (`approve_script(edited_script=...)` re-splits into fresh scenes before approving --
any clip/audio a scene had is impossible at this point anyway, since this is before keywords or
sourcing exist). A rejection's notes become next round's `script_feedback`; `reject_script` clears
`job.script`/`job.scenes` so the retry starts clean.

## 2. `keywords_running` -- machine, auto-approved by default

| | |
|---|---|
| **Code** | `pipeline/stages/keywords/llm.py` (`LLMKeywordStage.run_for_scenes`) or `manual.py` (`ManualKeywordStage.run`), picked by `job.providers.keywords`, behind the `KeywordStage` protocol (`pipeline/stages/base.py`) |
| **Reads from job** | `job.scenes` (one search term is requested per scene, tagged with that scene's index -- `run_for_scenes()`'s `SCENE_PROMPT`); `job.keyword_feedback` (reviewer notes from an earlier rejection, if any); `job.niche` / `job.script_style` (bias wording only). A provider with no `run_for_scenes` (e.g. `manual`) falls back to its ordinary scene-agnostic `run()` |
| **Returns** | `list[Keyword]` -- `term`, `group` (`research`/`case`/`historical`/`stock`, `pipeline/sources/groups.py`), `rank`, `search_volume`/`difficulty` (LLM estimates, flagged `estimated`), `meta.why`, `scene_index` (which scene this term was generated for, or `None` for a scene-agnostic provider) |
| **Writes to job** | `job.keywords = result` (`Orchestrator._apply_keywords`), after `Orchestrator._assign_keywords_to_scenes` guarantees every scene has at least one `scene_index`-tagged keyword (claims an untagged keyword in place for `manual`-style providers; only duplicates a term across scenes if there are genuinely fewer keywords than scenes) |
| **Writes (project folder)** | `keywords_proposed.json` -- this round's candidate list, before any human decision. Rewritten every time this stage runs (history of every round lives in `decisions.jsonl`, append-only) |
| **Then** | → **Gate 1½** `keywords_review`, then straight on -- see below |

**Gate 1½ -- auto-approved by default** (`job.providers.options["auto_approve_keywords"]`, default
`True`; a settings-level default for every job on a deployment can also be set via
`settings["keywords"]["auto_approve_keywords"]` -- the per-job option, when a job sets it, always
wins). `Orchestrator._auto_approve_keywords` approves every keyword, copies each one's term onto the
scene(s) it was tagged for (`Orchestrator._apply_keyword_terms_to_scenes`, so
`pipeline/stages/scenes/clips.py`'s clip matching has something to search on), writes
`keywords_approved.json` with `reviewer="(auto-approved)"`, and advances the job on -- all in the
same `run_pending()` call that ran the keyword stage, so from the outside a job with auto-approval
on never visibly stops at `keywords_review` at all.

Set `auto_approve_keywords` to `False` (per job, or as the deployment default above) to turn this
back into a real human gate: the state and `review_keywords()`/`reject_keywords()` are completely
unchanged either way, so flipping the option is the entire pivot, no other rewiring needed. With it
off, a human reviews the proposed terms (adding their own if they like), and approving copies terms
onto scenes exactly the same way the auto-approve path does.

## 3. `sourcing_running` -- machine

| | |
|---|---|
| **Code** | `pipeline/stages/sourcing.py` (`SourcingStage`), not behind a named `Protocol` but structurally the same shape |
| **Reads from job** | `job.approved_keywords`, routed to sources by `QueryGroup` (`pipeline/sources/groups.py`'s `route_queries()` -- an ungrouped term reaches every configured source); `job.providers.sources`; `job.providers.options` (`extra_queries` from "Search again", `folder_files`, per-source budgets) |
| **Returns** | `SourcingResult(assets, references, trace)` |
| **Writes to job** | `job.assets.extend(...)`, `job.references.extend(...)`, `job.source_notes.extend(...)` (`Orchestrator._apply_sourcing`) |
| **Writes (project folder)** | `sources/<name>/requests.jsonl` + `manifest.json` + `files/` per source, written by each adapter as it runs; `SOURCES.md` (aggregate, `write_manifests` → `write_sources_md`) |
| **Then** | → `vetting_running` automatically, no gate. Only reached at all when `job.uses_sources` (`job.providers.sources` non-empty); otherwise Gate 1½ goes straight to `scenes_running` (step 5) instead |

## 4. `vetting_running` -- machine

| | |
|---|---|
| **Code** | No `Stage` class or `Protocol` -- `Orchestrator._apply_vetting` calls straight into `pipeline/vetting/rules.py` (`vet_all`/`vet_asset`, risk rules, `rules-v1`) plus `tfidf_relevance.py` and (for borderline scores) `llm_relevance.py` for the relevance number, then `pipeline/vetting/niche.py` if `job.niche` is set |
| **Reads from job** | Every `pending` asset in `job.assets`; `job.approved_keywords` + `extra_queries` as the relevance topic terms; `job.providers.options` (`min_relevance`, `defer_relevance`) |
| **Writes to job** | Each `Asset.vetting` (`risk`, `flags`, `relevance`, `scoring_method`, `usable`, ...) |
| **Writes (project folder)** | `VETTING_REPORT.md` (`pipeline/vetting/report.py`) -- risk + relevance + every flag and why, per asset; risk/relevance also folded into each source's `manifest.json` |
| **Then** | → **Gate 2** `assets_review` automatically, no separate gate of its own |

**Gate 2 -- human** (`review_assets()` / `approve_assets()` / `reject_assets()`): approve or reject
each asset (high-risk approvals need a written note); every Use/Duplicate/Irrelevant click also
appends one row to `RELEVANCE_LABELS.jsonl` (`Orchestrator._write_label`, append-only, ground truth
for `docs/EVALUATION.md` -- separate from the approve/reject decision itself). From here you can also
**search again** (new `extra_queries`, re-enters `sourcing_running`/`vetting_running`) or use
**suggest more search terms** (`suggest_keywords()`/`approve_suggested_keywords()` -- reruns the same
Gate-1½ keyword stage mid-review, appends new unapproved `Keyword`s to `job.keywords`, no state
transition). Approving the pool moves the job on. A job with no sources configured
(`job.uses_sources` false) skips sourcing/vetting/this gate entirely and goes straight from Gate 1½
to `scenes_running`.

## 5. `scenes_running` -- machine, shrunk

| | |
|---|---|
| **Code** | `pipeline/stages/scenes/mpt.py` (`MptSceneStage.run`) -- the same class as step 1, but this method only matches clips now; it never writes narration. Composes `clips.py` (`AssetClipSource`/`ClipSource`, matches a clip to each scene) |
| **Reads from job** | `job.scenes` (already written by `script_running`, narration untouched here); each `Scene.search_terms` (assigned by Gate 1½ approval, above); `job.approved_assets` (the clip pool, when `job.uses_sources`) or the local clip library otherwise |
| **Returns** | `SceneResult(script, scenes)` -- `script` passed through unchanged; each scene now has `clip_path`/`asset_id`/`clip_reason` filled in (and `audio_path`, if `generate_audio` is configured) |
| **Writes to job** | `job.scenes = res.scenes` (`Orchestrator._apply_scenes`) -- `job.script` is not touched here at all |
| **Writes (project folder)** | `SCRIPT.md` (`pipeline/stages/scenes/report.py`) -- narration + each scene's search term, matched clip, why, audio status; narration audio at `assets/scene_XX.mp3` per scene, if `generate_audio` is configured |
| **Then** | → **Gate 3** `scenes_review` |

**Gate 3 -- human** (`approve_scenes()` / `reject_scenes()`, plus per-scene reorder/edit/clip-swap
calls): reorder scenes, edit narration, swap clips, approve. A rejection's feedback goes into
`job.scene_feedback`, but since the reorder that no longer feeds a script rewrite (the script is
already fixed by Gate 1) -- a rejection here just re-runs clip/audio matching against the existing
narration.

## 6. `rendering` -- machine

| | |
|---|---|
| **Code** | `pipeline/stages/render/mpt.py` (`MptRenderStage`), behind the `RenderStage` protocol |
| **Reads from job** | `job.scenes`, in your exact approved order; voice/subtitle/caption/BGM config (`config/pipeline.toml`) |
| **Returns** | `RenderResult(output_path, social_metadata)` |
| **Writes to job** | `job.output_path`, `job.social_metadata` (`Orchestrator._apply_render`) |
| **Writes (project folder)** | `assets/final.mp4`; `CREDITS.md` + `DESCRIPTION_CREDITS.txt` (`write_credits`, `pipeline/stages/sourcing.py` -- written once here, not regenerated on every mutation like the three `_write_derived_files` above) |
| **Then** | → `completed` |

## Adding a stage or moving one

Each stage is a small async class behind one of the `Protocol`s in `pipeline/stages/base.py`
(`KeywordStage`, `SceneStage`, `RenderStage`) or, for `sourcing`/`vetting`, the same one-`run()`
shape without a named protocol. The orchestrator never imports a stage's class directly -- it asks
`pipeline/stages/registry.py`'s `Registry` for whichever one `job.providers.<slot>` names, so a new
provider is one class plus one `registry.register_*()` line (`README.md` "Adding your own
provider"). Moving *when* a stage runs, or what feeds it, is an orchestrator + `JobState` change
(`pipeline/core/orchestrator.py`, `pipeline/core/models.py`'s `JobState` enum,
`pipeline/core/state_machine.py`) -- the stage classes themselves don't need to change shape.
