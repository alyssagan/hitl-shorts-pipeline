# Review web page

Open **http://localhost:8000/review** (list of projects) or **http://localhost:8000/review/JOB_ID** any time a job is waiting at
a human gate -- it shows the asset review at Gate 2 and the script/scene review at Gate 3, and refreshes on its own when the job
moves from one to the other. The terminal prints the exact link. Served by the pipeline container itself; nothing to install.

## Gate 2: assets

Each card shows the photo or video (click a photo to enlarge, videos have controls), the **relevance score**, risk, source, license,
author, size/length and a link to the original page. "Why this score and risk" opens the logic: the relevance threshold and machine
decision at scoring time, `scored by: <method> [<version>]` (e.g. `scored by: tfidf [tfidf-v1]`), every contribution when more than
one method scored the asset that round, a plain-English note on how the final decision was reached, a nested "What does `<version>`
do?" panel with that method's full definition (description, formula/prompt, parameters -- fetched once from `GET /methods`, see
docs/SCORING_CHANGELOG.md), every risk rule that fired with its evidence, and which search found it.

- **Use / Duplicate / Irrelevant** per card -- the three human review labels (docs/EVALUATION.md). Use = approve; Duplicate and
  Irrelevant both reject, since neither should end up in the video, but they're saved as different labels: Duplicate means "this is
  the same as another asset", which is a *separate* question from whether it's actually relevant to your topic -- a duplicate can
  still be a good, on-topic photo, it just isn't a new one. Using a HIGH-risk item requires a written reason. Items too small to
  render can't be used. Clicking a label saves it **immediately** (its own request, `POST /assets/label`) -- it does not wait for
  "Save and continue" -- so labelling works even if you never submit this round, and even on a job that's already moved past asset
  review (open its `/review/JOB_ID` link again any time).
- After picking a label, an optional **reason** dropdown appears (wrong case/person, keyword-only match, generic imagery, wrong era,
  poor visual quality, unreliable source, exact/near duplicate, or "other") plus a free-text **note**, and -- only for Duplicate --
  which other asset in this job it's a duplicate of. Click "save reason/note" to attach them (this re-saves the label with the
  extra context; nothing is lost, each save is its own row -- see "Training labels" below).
- **Min score slider** (starts at the job's `--min-relevance`, default 50%). Items below it are hidden; tick "show below threshold" to see them.
  Submitting while some are hidden records them as rejected with the note "hidden below threshold ... not looked at".
- Filter by source or photos/videos. **Sort defaults to risk**: low-risk items first, then medium, then high, and within
  each risk level the best-scoring (most relevant) items come first -- so the safest, most relevant items are always at
  the top and risk only gets worse as you scroll. Can also sort by score alone or by source. "Use all shown" skips
  HIGH-risk items.
- **Save decisions and continue** posts your approve/reject choices with your name to the same decision log the terminal uses, then
  moves on to scenes. You only need to click Use on the ones you want. Anything left undecided (or hidden below the threshold) is
  recorded as rejected with a note saying it had no decision -- and is NOT counted as a label, since you didn't judge it. The button
  is disabled only if nothing is approved or a HIGH-risk item is missing its note.
- **Get more** (bottom): two ways to pull more assets without leaving the page, both send you back to sourcing then
  straight back here when the round finishes. **Next batch** is the one-click option -- pick a batch size (defaults to 10,
  or the job's current `max_queries`) and it searches that many of your already-approved keywords that haven't been
  searched yet; no feedback needed. It also sets that batch size as the job's `max_queries` for every round after this
  one (see "Many keywords: batches" in docs/RUNNING.md), so once you've picked a comfortable size you don't have to
  reset it each round. **Search again** is for steering it: add specific new search terms and/or say what was wrong;
  fetches the next page of results without repeats.

In the terminal, after submitting in the browser, type `web` at the asset prompt so the script carries on.

## Gate 3: script and scenes

When the job reaches scene review, the same page switches to the script/scene editor -- reopen `/review/JOB_ID` (it refreshes on
its own within a few seconds of the job getting there, or reload).

- **Full script**, at the top: this is the CURRENT script -- today's scene order and narration, exactly what will be spoken --
  not the frozen first draft (`job.script`, which never updates as you edit and would otherwise go stale after your first
  change). It updates live as you type in a scene below, along with a word count and estimated seconds spoken (words ÷ 2.6,
  same estimate `scripts/poc.py` prints).
- **One card per scene**, in order: which clip it uses (filename, or "NO CLIP" if none is assigned -- approve more assets or
  add footage to `library/clips/` and come back; a clip picked from your approved photos that got reused because there
  weren't enough distinct ones says so right here, e.g. "REUSED: every approved asset was already used once elsewhere" --
  see docs/ROADMAP.md "clip matching quality" for why), why that clip was chosen if the machine picked it, an editable
  narration box, and a private **note** box underneath it (never spoken, never sent to the renderer -- just for you: why you
  picked something, what to reconsider, anything you want to remember later; saved the same way as narration). Drag the
  ☰ handle to reorder a scene, or use the ↑/↓ buttons -- either works, and they stay in sync. If this job sources its
  footage from approved assets (not just your local clip library), a **Clip** dropdown lets you swap in any other approved
  asset for that scene.
- **Save changes** persists your edits (and any reordering) without leaving the page or advancing the job -- keep editing
  afterward if you want. **Approve and render** saves whatever's still unsaved too, then starts the render: your last chance to
  change anything, since a render takes a few minutes and re-renders from scratch rather than patching in place.
- **Ask for a rewrite instead** sends the whole thing back to be rewritten from your feedback (what should change -- e.g.
  "shorter", "different tone", "get the date right"), the same as typing a reason at Gate 3 in the terminal. This throws away
  the current script and scenes and writes new ones; anything you'd typed but not saved is not carried over.

The terminal (`scripts/poc.py`) still walks Gate 3 too, one scene at a time (`edit 2` to change scene 2's text, a new order like
`2,1,3`, or feedback for a rewrite) -- either one works on the same job; use whichever is open. As of this doc, the terminal
also shows the current (not frozen) script the same way.

Not built yet: the after-render editor (see ROADMAP).

## Training labels
Every explicit **Use** / **Duplicate** / **Irrelevant** click is saved as its own row in
`projects/<name>-<id>/RELEVANCE_LABELS.jsonl` -- title, description, source, the search that found it, the approved keywords,
what the machine scored it (score, decision, threshold, and exactly which `scoring_method`/`method_version` produced that score,
snapshotted as they were at the time, never recalculated later), your label, and your optional reason/note/`duplicate_of_asset_id`.
This file is **append-only**: relabeling an asset adds a new row rather than replacing the old one, so a change of mind is never
lost -- "the current label" is simply the latest row per asset. Items you never clicked (undecided or hidden) are NOT labelled,
since you didn't judge them.

This is the ground truth `scripts/evaluate_relevance.py` reads to check whether the machine's scoring is actually finding what
you want to Use, broken down per method+version, and `scripts/sample_for_review.py` picks (and tags) candidates for you to label
so that comparison stays honest rather than built from whatever you happened to look at. See **docs/EVALUATION.md** for both.
Nothing is trained automatically from this file yet -- it's for measurement, not (yet) for feeding back into scoring.
