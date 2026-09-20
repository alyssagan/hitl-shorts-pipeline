# Known limitations

Things that work today but with a trade-off we chose on purpose. Each has a way to
improve it later. Add new ones at the bottom; move an entry to "Resolved" when fixed.

## Open

### 1. Script grounding text is capped at ~1100 characters
- **What:** when the script is written from Wikipedia text, only the first ~1100
  characters of the source text are sent to MoneyPrinterTurbo (MPT). Roughly the article intro.
- **Why:** MPT rejects a `video_script_prompt` longer than 2000 characters
  (`app/models/schema.py`, `VideoScriptParams`) and reports it as a misleading
  `400 field required`. Our instructions, approved keywords and reviewer notes share that budget.
- **Effect:** scripts can miss facts that only appear further down an article. The
  scene review gate is where a human catches this.
- **Also:** the budget is split evenly across the source articles. Wikipedia's search can
  return a loosely related article first ("3 surprising facts about octopuses" returned
  "Kraken" before "Octopus"), so some of the budget may go to the wrong topic.
- **Where in code:** `GROUNDING_CHARS` in `pipeline/stages/scenes/mpt.py`, and the last-resort
  trim `MAX_SCRIPT_PROMPT` in `pipeline/stages/mpt_client.py`.
- **Options to improve (not started):**
  1. Send the grounding through MPT's `custom_system_prompt` field (limit 8000). First check
     whether it replaces MPT's built-in instructions.
  2. Summarize the whole article into a short fact list first (extra AI call; log it in the
     decision log and show it at the scene gate).
  3. Write the script ourselves and skip MPT's script step.
- **Revisit when:** the first scripts look thin or wrong, or when true-crime mode needs long
  source material (it will).

### 2. Vetting reads metadata, not pictures
- **What:** the risk rules (`pipeline/vetting/rules.py`) look at license text, titles,
  descriptions and image size. They never look at the image itself, so they can't spot
  a recognizable face, a child, a logo or a watermark unless the text says so.
- **Effect:** a clean-looking image can still be risky. Your review at the asset gate is the
  real check; the flags are a head start.
- **Also:** the license on Commons is whatever the uploader entered. Mislabeled or wrongly
  claimed licenses exist, and the pipeline can't tell.
- **Options:** add an image-content check (an AI vision model, logged in the decision log
  with its reasons) as an extra flag, never as a filter.

### 3. Share-alike and other license questions are flagged, not answered
- **What:** `LIC_SA`, `LIC_ND`, `LIC_GFDL` etc. tell you a question exists. Whether a video
  counts as an adaptation is a legal question the pipeline can't answer, and this is not legal advice.
- **Effect:** for anything monetized, client work or company use, take real advice or use
  public-domain / CC0 / plain CC BY assets.

### 4. Credits: description text only, and license matching is simple
- **What:** `DESCRIPTION_CREDITS.txt` is written for you to paste. Nothing is posted for you,
  and nothing is burned into the video (MPT can't overlay text per image).
- **Also:** deciding whether an asset needs a credit line is a substring match on the license
  text (`pipeline/stages/sourcing.py`, `description_block`). An unusual license string could
  be missed or included wrongly. Check the block before publishing.
- **Options:** a post-render ffmpeg step for on-screen credits; a proper license table.

### 5. Mostly still images from the free sources
- **What:** Wikipedia and Commons return mostly photos and diagrams, a few videos.
- **Effect:** the video will be photos with motion added, not stock footage. Pexels (free key)
  is already supported as an optional source and would add video clips.
- **Unverified:** how good MPT's image-to-clip motion looks. We'll know after the first render.

### 6. Free tiers and unofficial services can change or throttle
- **Gemini free tier** has rate and daily limits, and model names get retired. The model in
  `config/mpt-config.toml` (`gemini-2.0-flash`) may need updating.
- **Edge TTS** (the free voice) is an unofficial service that needs internet and can change
  without notice.
- **Wikipedia / Commons** expect polite use. Heavy repeated searches may need throttling.

### 7. Everything is single-user and local
- **What:** no login on the pipeline or MPT API. Both bind to `127.0.0.1` only. The reviewer
  name is just text you type, so the decision log records who *said* they decided.
- **Also:** the decision log's hash chain shows edits to a line, but someone with file access
  could rebuild the whole chain. It is a record for you, not a legal audit trail.
- **Options:** if anyone else uses it, add real user accounts and sign log entries.

### 8. MoneyPrinterTurbo is built from our own Docker recipe
- **What:** upstream's Dockerfile pins Debian "bullseye", which is end-of-life and no longer
  downloads. `docker-compose.yml` builds MPT from `python:3.11-slim` with an inline recipe.
- **Effect:** if a newer MPT version adds system packages (for example ImageMagick for
  subtitles), our recipe won't install them until we add them. The submodule is pinned, so
  this only matters when upgrading MPT.
- **Options:** move the recipe into a real `docker/mpt.Dockerfile`, and re-check it whenever
  the MPT submodule is updated.

### 9. Not verified end to end yet
Assumptions that unit tests can't confirm, to be checked against a real render:
- per-scene audio path (`/tasks/<id>/audio.mp3`) matches what MPT really writes;
- MPT reads approved images from the mounted project folder (`PATH_MAP` mapping);
- subtitle fonts and text rendering work in our MPT container;
- scene timing and reordering give the video you expect.
Update this entry as each one is confirmed or fixed.

### 10. Windows is untested
- **What:** designed to run on Windows through Docker, but only developed against a Mac.
- **Watch for:** path separators, line endings, and Docker bind-mount paths.

### 11. True-crime mode is not built yet
- **What:** agreed but deliberately postponed until the POC works. Needs its own review
  states (eligibility, images, final), its guardrails carried over, one shared MPT copy, and
  much longer source material (which will hit limitation #1 hard).

## Resolved

_(nothing yet)_
