# Roadmap

What we agreed to build next, in order, with the choices made. Limits that shape each item
are in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md); things to experiment with are in
[OPTIONS_TO_TRY.md](OPTIONS_TO_TRY.md).

## Decisions

| Question | Choice |
|---|---|
| Review UI | A local web app served by the pipeline (localhost, opens in the browser, works on Mac and Windows) |
| URL list | Any link, using a downloader (yt-dlp); every clip keeps its URL, title and uploader; unknown or platform licenses are flagged high-risk and need a written note to approve |
| Script length | 90 seconds or more (default target 260 words) |
| Editor after render | Reorder, swap, delete and edit text, then re-render (about 4 minutes each time, because MoneyPrinterTurbo renders the whole video at once) |
| Videos | Stay local. Never committed to git (`.gitignore`) |

## Order of work

1. **Longer, editable script.** Own script writer, word count and estimated seconds at the
   review gate, edit a scene's text (terminal now, web page in step 3). *Built.*
2. **More material: a URL list.** A file or page where you paste URLs. Each is downloaded into
   the project (`sources/urls/`), with URL, title, uploader, duration and license kept in the
   manifest and the decision log, and a suggested position in the video. Also more free stock
   sources (Pexels and Pixabay with a free key, Internet Archive, NASA).
3. **Review web app.** Every photo and clip on one page with its source, license, flags and an
   approved / rejected badge, and a "still to review" count before moving on. Script editor on
   the same page.
4. **After-render editor.** Timeline of scenes: drag to reorder, replace or remove a clip, edit
   the text, then re-render. Not a live-preview video editor (see decisions).
5. **True-crime mode.** After the above; its own review states and guardrails.

## What each step depends on

- Steps 3 and 4 share one web app and one set of API routes, so step 3 is built to hold both.
- Step 2 needs `yt-dlp` and `ffmpeg` in the pipeline's Docker image, and internet access from it.
- Downloading videos from platforms can break their terms of service and copyright. The
  pipeline flags it and records the decision; the risk stays with you (Limitation #3).

> Update: the asset review web page (thumbnails, scores, Use/Reject, search again) is built. See docs/REVIEW_UI.md. Scene/script page still to do.
