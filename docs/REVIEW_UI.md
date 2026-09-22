# Review web page

Open **http://localhost:8000/review** (list of projects) or **http://localhost:8000/review/JOB_ID** while a job is at the asset review.
The terminal prints the exact link. Served by the pipeline container itself; nothing to install.

Each card shows the photo or video (click a photo to enlarge, videos have controls), the **relevance score**, risk, source, license,
author, size/length and a link to the original page. "Why this score and risk" opens the logic: matched words, formula, every rule
that fired with its evidence, and which search found it.

- **Use / Irrelevant** per card. "Irrelevant" means not about your topic, so don't use it. Using a HIGH-risk item requires a written reason. Items too small to render can't be used.
- **Min score slider** (starts at the job's `--min-relevance`, default 50%). Items below it are hidden; tick "show below threshold" to see them.
  Submitting while some are hidden records them as rejected with the note "hidden below threshold ... not looked at".
- Filter by source or photos/videos. **Sort defaults to risk**: low-risk items first, then medium, then high, and within
  each risk level the best-scoring (most relevant) items come first -- so the safest, most relevant items are always at
  the top and risk only gets worse as you scroll. Can also sort by score alone or by source. "Use all shown" skips
  HIGH-risk items.
- **Save decisions and continue** posts your choices with your name to the same decision log the terminal uses, then moves on to scenes.
  You only need to click Use on the ones you want. Anything left undecided (or hidden below the threshold) is recorded as rejected with a note
  saying it had no decision. The button is disabled only if nothing is approved or a HIGH-risk item is missing its note.
- **Search again** (bottom): new search terms plus what was wrong; fetches the next page of results without repeats.

In the terminal, after submitting in the browser, type `web` at the asset prompt so the script carries on.

Not built yet: the scene/script editor page and the after-render editor (see ROADMAP).

## Training labels
Every explicit **Use** or **Irrelevant** click is also saved as a labelled example in `projects/<name>-<id>/RELEVANCE_LABELS.jsonl`
(one line per asset: title, description, source, the search that found it, the keywords, what the machine scored it, and your label).
Items you never clicked (undecided or hidden) are NOT labelled, since you didn't judge them. Later these can be used to check and tune
the score threshold and word lists (does the machine's 30% match your "irrelevant"?), or to skip known-irrelevant items in future runs.
The decision log also notes when a label was saved. Nothing is trained automatically yet.
