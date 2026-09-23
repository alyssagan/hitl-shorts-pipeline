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

## What this doesn't do yet

There's no UI for any of this yet (Stage 2, alongside the rest of the Gate 2 field-surfacing work) and
nothing cross-checks a `case`-group search term's `entity`/`aliases`/`dates`/`locations` against the
reference sheet's facts -- that comparison, and any resulting "this search term doesn't match anything on
the reference sheet" warning, is a natural next step but isn't built.
