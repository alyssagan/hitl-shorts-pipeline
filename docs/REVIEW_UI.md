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
docs/SCORING_CHANGELOG.md), every risk rule that fired (its rule id, severity, plain-English message and the actual evidence text
that triggered it -- e.g. `PLATFORM_SOURCE`, `LIC_SA`, `LIC_UNKNOWN`), and which search found it. It's **open by default** on every
card (requested directly, after "high risk" was mistaken for "rejected" during testing) -- collapsing one sticks across
re-renders (`whyOpen`, per asset id) rather than silently popping back open the next time the page refreshes. The category/
identity/rights badges below (#13) are the other half of this: click "Case connection & rights" on a card for who's actually
pictured and where the usage rights stand, beyond what the automated flags alone can tell you.

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
- **Get more** (bottom): three ways to pull more assets without leaving the page. **Next batch** and **Search again**
  send you back to sourcing then straight back here when the round finishes. **Next batch** is the one-click option --
  pick a batch size (defaults to 10, or the job's current `max_queries`) and it searches that many of your
  already-approved keywords that haven't been searched yet; no feedback needed. It also sets that batch size as the
  job's `max_queries` for every round after this one (see "Many keywords: batches" in docs/RUNNING.md), so once you've
  picked a comfortable size you don't have to reset it each round. **Search again** is for steering it: add specific
  new search terms and/or say what was wrong; fetches the next page of results without repeats.
- **Add links** (below the other two, #9) is for a specific video/photo you already have a URL for -- paste one or
  more, one per line, same `url | note | position` format as `scripts/poc.py --urls`. Unlike Next batch/Search again,
  each line downloads **right away**, one at a time, and shows its own result underneath the box as it finishes --
  a green line for "added, pending review below", a red one with the actual reason if it failed (an unsupported/bad
  link, or one already in the project), so a typo in line 3 doesn't leave you guessing what happened to it. Failed
  lines stay in the box so you can fix and resubmit without retyping the ones that worked. A YouTube/TikTok/X/Vimeo/
  Instagram/news link is pulled with yt-dlp; a direct `.mp4`/`.jpg`/... link is downloaded as-is. yt-dlp's own error
  is shown as-is, with a plain-language hint added when it looks like the link is a webpage rather than an actual
  video/photo (no login/paywall is ever bypassed -- a link behind one just fails here the way it would for yt-dlp on
  its own). Every added link lands **pending** -- it is never auto-approved, so it still goes through the same
  Use/Duplicate/Irrelevant review and the same high-risk-needs-a-note rule as everything else -- but not in the main
  grid: it shows up in its own always-visible **"Added by link"** panel above the grid instead (requested directly,
  after a real added link turned out to be hidden by both the relevance-score filter and the risk-first sort at
  once and looked like it had vanished). A platform link (YouTube/TikTok/Instagram/...) is auto-flagged high risk
  regardless of content, and its caption/title often doesn't text-match your approved keywords either, so it's easy
  for one to be simultaneously "sorted last" and "hidden below threshold" in the main grid -- the panel sidesteps
  both by always showing every pending manually-added link, filters and sort order aside. Downloaded files also
  already live in their own `sources/urls/` folder on disk, separate from every other source's files, so nothing
  extra was needed there. Once you decide Use/Duplicate/Irrelevant on one, it moves down into the main grid like
  any other asset the next time the page refreshes. (The older behaviour -- queuing links into the job's URL list
  for the *next* sourcing round with no per-link feedback -- still exists as `extra_urls_text` on `POST
  /assets/reject` for scripts, just not from this box anymore.)
- **Add your own footage** (below Add links, #10) opens onto a list of whatever's currently sitting in the server's
  `library/scraped/` folder -- your own scraper's output, or anything you dropped there yourself. Nothing here is
  added to any job by itself: tick the specific files this job should use (each gets an optional note, e.g. why
  it's yours to use), then **Add selected files**. Unlike Add links, these aren't downloaded -- they're already on
  disk -- so they're queued the same way Next batch is (pulled on the next sourcing round; `folder` is added to
  the job's sources automatically) rather than added synchronously one at a time. A ticked-and-added file is
  marked "already selected" if you open the panel again. Every file added this way is recorded as your own
  submitted material (`owner_submitted`), not inferred from anything about the file itself.
- **Category / identity / rights badges** (#13) sit alongside the score/risk badges on every card: which of the five
  categories it's in (`verified case`, `unverified case candidate`, `historical context`, `illustrative stock`,
  `reconstruction`, or "not categorized" until someone sets one), its identity status (`unverified`/`verified`/
  `disputed`), and its rights status (`public domain`/`CC0`/`open license`/`paid license`/`unresolved`). These three
  are independent of relevance and of each other -- nothing (not scoring, not the Use/Duplicate/Irrelevant labels, not
  keyword matching) ever sets them for you; they only move when a person opens "Case connection & rights" on a card
  and saves a value. That panel also shows who set each one and when, plus any depicts/case-connection/evidence/notes
  text recorded with it, and how the asset entered this job (search / manual URL / your own footage / a Gate-3 scene
  upload). Setting identity and rights are separate saves -- changing one never touches the other (#8) -- and both
  work on a card at any point, not just while the job happens to be sitting at Gate 2, the same way labelling already
  did, since a person may want to record who's actually pictured, or where the usage rights stand, well after the
  asset was approved or rejected.
- **"Sources & rights so far"** (#13, above the grid) is a per-job summary: a table of photo/video/research counts by
  source, then how the whole pool currently breaks down by category, by identity status, and by rights status, plus
  the LLM labeling cost run up so far for this job (calls and an estimated dollar figure, from the same usage rollup
  the terminal's cost reporting already used -- not a second calculation). It's collapsed by default and only loads
  when opened, so it costs nothing on a page that's just being used to click through cards.
- **"Visual checklist"** (#6/#12, right below that) is the only place to actually put things on the checklist that
  Gate 3's pre-render coverage check reads (docs/CASE_REFERENCE.md) -- without it the checklist stays empty and that
  check never has anything to flag. **Generate from approved keywords** seeds one draft item per approved keyword
  (skipping any term already linked to an item, so it's safe to click again after approving more); the inline form
  below it adds one by hand (a label, a group -- research/case/historical/stock -- and an optional linked keyword
  term, which is what lets the coverage check later match the item back to a specific scene). Every new item starts
  at `needed`; nothing here marks one fulfilled or not available -- that only happens explicitly, at Gate 3.
- **"Find more"** (requested directly, after a specific well-documented case turned up little through the automated
  sources) is a search-elsewhere helper, not another source: the pipeline's own search only reaches free,
  openly-licensed archive APIs, which structurally can't carry platform videos, press photo archives, or public
  records -- often exactly what a specific case needs. The box defaults to the job's subject; clicking one of the
  suggestion chips (every approved keyword's term/entity/aliases, plus every visual-checklist label) swaps it in
  without retyping. The eight buttons below each open a real search on a free site in a new tab, prefilled with
  whatever's in the box -- Internet Archive, Chronicling America (Library of Congress's own newspaper search, wider
  than the automated source's query), Wikimedia Commons, YouTube, Google Images, FindAGrave, TikTok, and Facebook.
  These eight never fetch, download, or add anything themselves -- they only open a search page for you to look at
  and judge yourself. Google Images in particular is a discovery tool, not a rights source: it's there to help you
  find where a photo actually lives, and whatever you find still needs its own license checked before use.
  Facebook's search almost always needs you to already be logged into Facebook in that same browser to show
  anything at all -- otherwise it's just a login page. Instagram is deliberately not in this list: unlike the
  others, it has no plain query-string search URL to link to (its search is a logged-in, JS-driven experience), so
  a button for it would only ever land on its login screen, not a shortcut to anything. Right below those buttons,
  a one-line paste-back box (requested directly: "i need a way to streamline this") adds whatever you found on any
  of those eight sites without scrolling down to **Add links** -- it calls that exact same endpoint.

  Four of the eight can also be searched without leaving this page at all -- **Search YouTube/Internet
  Archive/Chronicling America/Wikimedia Commons here**, one block per source, each with its own result count
  (3/5/10) and results grid. All four are metadata-only: nothing downloads or gets added until you click **Add this
  one** on a specific result. YouTube's search runs through yt-dlp (no paid API key) and adding a result calls the
  exact same path as pasting that video's link into **Add links** below -- synchronous download, lands `pending`,
  still auto-flagged high risk (`PLATFORM_SOURCE`) and still needs a written note before you can approve it. Every
  YouTube search (successful or not) is logged to `sources/youtube_search/requests.jsonl`. Internet Archive,
  Chronicling America and Wikimedia Commons are different: they're already automated sources in the pipeline's
  normal per-round search (docs/SEARCH_PLANNING.md) with a free no-key API each, so their "search here" block just
  calls that same source's `search()` on demand (`POST /jobs/{id}/source-search`) instead of duplicating it with a
  second implementation -- and adding a result (`POST /jobs/{id}/assets/add-candidate`) keeps the license, author
  and attribution that search already found, rather than re-deriving them from a bare URL the way `add-url` has to.
  These three aren't auto-flagged high risk the way a platform video is, but still go through the same
  vetting/relevance scoring and review as everything else. Google Images and FindAGrave don't get a "search here"
  block: neither has a free API to call, only a search page to open. Anything worth using -- from a link-out
  button, the paste-back box, or one of the four "search here" blocks -- comes back in the normal way and still
  goes through the same vetting/decision flow as everything else.

In the terminal, after submitting in the browser, type `web` at the asset prompt so the script carries on.

## Gate 3: script and scenes

When the job reaches scene review, the same page switches to the script/scene editor -- reopen `/review/JOB_ID` (it refreshes on
its own within a few seconds of the job getting there, or reload).

- **Full script**, at the top: this is the CURRENT script -- today's scene order and narration, exactly what will be spoken --
  not the frozen first draft (`job.script`, which never updates as you edit and would otherwise go stale after your first
  change). It updates live as you type in a scene below, along with a word count and estimated seconds spoken (words ÷ 2.6,
  same estimate `scripts/poc.py` prints).
- **Visual coverage** (#12, right under the script -- only shown once it's loaded and there's actually something unresolved):
  the pre-render check against the visual checklist (docs/CASE_REFERENCE.md). Lists every item still sitting at `needed` or
  `candidates_found` -- i.e. nobody's actually decided what happens here yet -- which scene(s) it's tied to (matched by
  shared search term, so you know exactly where the gap shows up), and lets you resolve each one right there: pick an
  already-approved asset and **Mark fulfilled**, or type a reason and **Mark not available** / **Mark skipped**. Both of
  those require the note; there's no way to silently drop an item off the list. A separate section below that, in red,
  lists anything already marked fulfilled whose attached asset isn't actually categorized case material (verified_case/
  unverified_case_candidate) -- the exact silent-substitution case this check exists to catch, one step past "still
  unresolved" -- with the same **Re-pick fulfilling asset** / not available / skipped options. If you'd rather get more material first,
  the asset strip and the drop-a-file/link-onto-a-scene affordances right below this panel are the fastest way, without
  leaving the page. **Approve and render** refuses to proceed while anything's unresolved unless you also fill in the
  override note at the bottom of this panel, explaining why it's fine to render without it -- that note, and exactly which
  items were left unresolved, get written to the decision log alongside the approval, so nothing renders on a silent
  substitution of generic footage for case material. A job that's never used the checklist at all doesn't show this panel
  and isn't gated by any of it -- it's an opt-in feature, not a new requirement forced onto every job.
- **Drag a clip onto a scene**, above the scene cards: every approved photo/video as a small thumbnail strip you can
  drag onto any scene card to use it there -- a visual, drag-and-drop version of the Clip dropdown described below
  (both do the same thing; use whichever's easier). A scene card also accepts two other kinds of drop directly: a
  file dragged straight from your computer (uploads it and offers it for that scene), or a video/photo URL dropped
  onto the card (pulled with yt-dlp/direct-download, same as Gate 2's "Add links"). Either way the new clip goes
  through the same vetting every sourced asset gets -- low/medium risk (or you're asked for a note first) and it's
  assigned to that scene immediately; high risk with no note yet is still kept, just left unassigned, and shows up
  in a **"Dragged in, needs a decision"** panel with a note box and an Approve & use button.
- **One card per scene**, in order: which clip it uses (filename, or "NO CLIP" if none is assigned -- approve more assets or
  add footage to `library/clips/` and come back; a clip picked from your approved photos that got reused because there
  weren't enough distinct ones says so right here, e.g. "REUSED: every approved asset was already used once elsewhere" --
  see docs/ROADMAP.md "clip matching quality" for why), why that clip was chosen if the machine picked it, an editable
  narration box, and a private **note** box underneath it (never spoken, never sent to the renderer -- just for you: why you
  picked something, what to reconsider, anything you want to remember later; saved the same way as narration). Drag the
  ☰ handle to reorder a scene, or use the ↑/↓ buttons -- either works, and they stay in sync. If this job sources its
  footage from approved assets (not just your local clip library), a **Clip** dropdown lets you swap in any other approved
  asset for that scene.
- **Crop**, a collapsed "Crop: automatic" / "Crop: adjusted" line under each scene's clip picker. MoneyPrinterTurbo always
  auto-center-crops a clip to the video's aspect ratio on its own, with no way to choose what part of the frame survives --
  open this to override that when the auto-crop would cut off a face, a caption, whatever the shot's actually about. Drag
  the preview to reposition it, use the zoom slider to tighten the window, then **Save crop**; **Reset to auto-crop**
  goes back to MoneyPrinterTurbo's own centering. Needs the clip's width/height to be known (sourced assets have this;
  if it's somehow missing, the panel says so and falls back to the auto-crop instead of guessing) and, if there's an
  unsaved clip change pending in the picker above, save that first -- a crop is framed for one specific clip, so picking
  a different one clears whatever crop was set for the old one. The actual cropped file is only produced at render time
  (pipeline/stages/render/crop.py), never live in the browser.
- **Save changes** persists your edits (and any reordering) without leaving the page or advancing the job -- keep editing
  afterward if you want. **Approve and render** saves whatever's still unsaved too, then starts the render: your last chance to
  change anything, since a render takes a few minutes and re-renders from scratch rather than patching in place. See "Visual
  coverage" above -- this button won't go through while anything's unresolved there, unless you've also given an override note.
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
