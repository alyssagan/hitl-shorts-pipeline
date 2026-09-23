# Case reference sheet & visual checklist

(Requirement #6: canonical name, people/aliases, dates/locations, addresses/institutions/events, source
links, unresolved/conflicting facts, plus an editable generated visual checklist -- never inventing facts
or treating a search result as confirmation.)

## Why this exists

Search results and AI relevance scores tell you what a source *claims* or what *looks* similar -- neither
is confirmation of a fact about the actual case. The case reference sheet is deliberately separate from
everything the pipeline searches, scores or vets: it's the one place in a job that only ever contains what
a human has actually entered or confirmed, with its own source links per fact. Assets found by sourcing can
be compared against it, but nothing about a search result ever writes to it automatically.

It's stored on the job itself (`Job.case_reference`, `Job.visual_checklist` -- `pipeline/core/models.py`),
editable at any point in the job's life, not gated behind a particular pipeline state the way keyword/asset/
scene review are.

## Case reference sheet (`Job.case_reference`, a `CaseReferenceSheet`)

- `canonical_name` -- the case's canonical name, e.g. "The Dyatlov Pass Incident".
- `facts` -- a list of `CaseFact`, each one atomic: a person, alias, date, location, address, institution,
  event, or other fact (`kind`), the fact text itself, optional `detail`, `source_links`, a `status`
  (`confirmed` / `unresolved` / `conflicting`) and a `conflict_note` for what's actually uncertain or
  disputed when status isn't `confirmed`.

Every fact records who added or last edited it and when (`added_by`/`added_at`/`updated_by`/`updated_at`).
Nothing here is ever written by sourcing, vetting, or relevance scoring -- only the orchestrator methods
below, called by a human through the API.

### Orchestrator methods (`pipeline/core/orchestrator.py`)

- `set_case_canonical_name(job_id, canonical_name, *, reviewer)`
- `add_case_fact(job_id, kind, text, *, detail="", source_links=None, status="confirmed", conflict_note="", reviewer)`
- `edit_case_fact(job_id, fact_id, *, text=None, detail=None, source_links=None, status=None, conflict_note=None, reviewer)`
  -- partial update; only the fields you pass are changed. `text` can't be blanked out (remove the fact instead).
- `remove_case_fact(job_id, fact_id, *, reviewer)`

### HTTP API

```
POST  /jobs/{id}/case-reference                    {canonical_name, reviewer}
POST  /jobs/{id}/case-reference/facts               {kind, text, detail?, source_links?, status?, conflict_note?, reviewer}
PATCH /jobs/{id}/case-reference/facts/{fact_id}      {any subset of the fields above, reviewer}
POST  /jobs/{id}/case-reference/facts/{fact_id}/remove   {reviewer}
```

`reviewer` is required on every call (a name is written to the decision log, same as every other human
action in this pipeline). `kind` must be one of `person`/`alias`/`date`/`location`/`address`/`institution`/
`event`/`other`; `status` one of `confirmed`/`unresolved`/`conflicting`.

## Visual checklist (`Job.visual_checklist`, a list of `VisualChecklistItem`)

What the video still needs a visual for. Each item has a `label` (what's needed on screen), an optional
`linked_keyword_term` (which approved keyword generated it, if any) and `group` (its `QueryGroup`), a
`status` (`needed` / `candidates_found` / `fulfilled` / `not_available` / `skipped`), a free-text `note`,
and an `asset_id` once a human has picked which approved asset actually fulfills it.

**Nothing here is ever set to `fulfilled` or `not_available` automatically.** `generate_visual_checklist()`
only ever creates items at `status="needed"` -- even when it runs after a search has already found
candidates. Moving an item past `needed` (to `candidates_found`, `fulfilled`, `not_available` or `skipped`)
is always an explicit human edit through `update_checklist_item()`.

### Generating draft items

`generate_visual_checklist(job_id, *, reviewer)` seeds one draft item per approved keyword, using
`Keyword.visual_needed` when the LLM's structured plan set it (falling back to the keyword's own `term`),
carrying over its `group`. It's safe to call more than once: an approved keyword whose term is already
linked to an existing checklist item (generated OR added by hand) is skipped, so re-running it after
approving more keywords only adds items for the new ones.

### Orchestrator methods

- `generate_visual_checklist(job_id, *, reviewer)`
- `add_checklist_item(job_id, label, *, linked_keyword_term="", group="historical", reviewer)` -- add one by hand
- `update_checklist_item(job_id, item_id, *, status=None, note=None, asset_id=None, reviewer)` -- partial
  update. Setting `asset_id` requires it to already be a known asset on the job.
- `remove_checklist_item(job_id, item_id, *, reviewer)`

### HTTP API

```
POST  /jobs/{id}/visual-checklist/generate           {reviewer}
POST  /jobs/{id}/visual-checklist                     {label, linked_keyword_term?, group?, reviewer}
PATCH /jobs/{id}/visual-checklist/{item_id}            {status?, note?, asset_id?, reviewer}
POST  /jobs/{id}/visual-checklist/{item_id}/remove     {reviewer}
```

`not_available` and `skipped` both require a note explaining why -- `update_checklist_item` refuses the
status change otherwise, so an item never quietly drops off the list without a reason attached. `fulfilled`
likewise requires an `asset_id` (already set on the item, or given in the same call): it has to name which
specific approved asset actually satisfies the need.

## Pre-render visual coverage check (#12)

The whole point of the checklist is that rendering can't quietly go ahead while it still says something is
`needed` or `candidates_found` -- those two statuses mean "nobody has actually decided what happens here
yet". `Orchestrator.check_visual_coverage(job_id)` is a read-only report, callable at any job state:

```python
{
  "has_checklist": bool,   # False if this job never used the checklist -- then nothing here gates anything
  "ready": bool,           # True once every item is fulfilled/not_available/skipped with no mismatches
  "counts": {"needed": 0, "candidates_found": 0, "fulfilled": 0, "not_available": 0, "skipped": 0},
  "unresolved": [{"id", "label", "status", "group", "linked_keyword_term", "linked_scene_ids"}, ...],
  "category_mismatches": [{"id", "label", "asset_id", "asset_category", "linked_scene_ids"}, ...],
  "remediation_options": [...],   # the same 5-item menu every time, see below
}
```

`linked_scene_ids` is a best-effort cross-reference: which of the job's scenes share the item's
`linked_keyword_term` in their own `search_terms`, so the report can say *which scene* a gap actually
affects, not just that one exists somewhere.

Resolving an item to `fulfilled` isn't automatically the end of the story: `update_checklist_item` requires
an `asset_id` for `fulfilled`, but doesn't check that the asset is actually case material -- that's exactly
the silent-substitution risk #12 exists to catch, just one step later than "still unresolved". So
`category_mismatches` lists every `case`-group item marked `fulfilled` whose `asset_id` points at an asset
that isn't categorized `verified_case`/`unverified_case_candidate` (or whose asset is missing/uncategorized).
It's not a hard block on its own -- a human may have deliberately decided a historical photo is the best
available stand-in -- but it's never silent: it counts toward `ready` exactly like an unresolved item, and
still needs a resolve (repoint it at real case material, or an explicit override) before rendering proceeds.
A `historical`/`stock`/`research`-group item fulfilled with non-case material is completely normal and is
never flagged -- the check only applies where the group itself says this is supposed to be case material.

`Orchestrator.approve_scenes()` calls this internally before it lets a job move to `RENDERING`. If any item
is unresolved or mismatched, it refuses (`ValueError`, a 422 over HTTP) unless the caller also passes a
non-blank `override_note` -- in which case it proceeds, but records the override note and exactly which
items were left unresolved/mismatched in that `approved_scenes` decision log entry (`outputs.
rendered_with_unresolved_visual_needs` and `outputs.rendered_with_category_mismatches`), so rendering past a
gap is always a recorded, explicit call, never a silent default. `GET /jobs/{id}/visual-coverage` exposes
the same report so a UI can show it before the reviewer even reaches "Approve and render" (docs/REVIEW_UI.md's
Gate 3 section).

The five remediation options a reviewer actually has for an unresolved item (`VISUAL_COVERAGE_REMEDIATION_OPTIONS`
in `pipeline/core/orchestrator.py`):

1. **Use a candidate already found** -- `PATCH .../visual-checklist/{item_id} {status: "fulfilled", asset_id}`.
   For a `case`-group item, that asset should actually be `verified_case`/`unverified_case_candidate` --
   pointing it at anything else lands in `category_mismatches` above, not treated as resolved.
2. **Search again / add a link / add your own footage** -- Gate 2's existing sourcing tools, or Gate 3's
   drag-and-drop/upload/link-drop straight onto a scene; no new endpoint, just point back at what's already built.
3. **Mark not available** -- `{status: "not_available", note}`, explaining why nothing could be found. Never
   promised that every case will have accessible footage -- this is the explicit, on-the-record way to say so.
4. **Mark skipped** -- `{status: "skipped", note}`, deciding the need isn't actually essential to the final video.
5. **Approve and render anyway** -- `POST /jobs/{id}/scenes/approve {override_note}`, proceeding without
   resolving everything right now, with a written reason kept alongside exactly what was left unresolved.

### HTTP API

```
GET   /jobs/{id}/visual-coverage                      the report above
POST  /jobs/{id}/scenes/approve   {reviewer, override_note?}   refused (422) if unresolved items exist and
                                        override_note is blank; otherwise proceeds and logs the override
```

## What this doesn't do yet

There's still no UI for the case reference sheet itself (canonical name, facts) -- editing it means calling
its HTTP API directly. The visual checklist's UI gap is closed (Gate 2's "Visual checklist" panel, docs/
REVIEW_UI.md, generates it from approved keywords or adds items by hand, and Gate 3's "Visual coverage"
panel resolves them) -- without that, the pre-render check had nothing to actually check in a real browser
session, since nothing populates the checklist on its own.

Nothing cross-checks a `case`-group search term's `entity`/`aliases`/`dates`/`locations` against the
reference sheet's facts -- that comparison, and any resulting "this search term doesn't match anything on
the reference sheet" warning, is a natural next step but isn't built.
