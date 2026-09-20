# Options to try

Choices we can change or experiment with, and what we learn from each. Add a line under
**Result** after you try something so the next decision is based on what actually happened.
Problems and trade-offs already in the code live in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

Status: `current` = what we use now, `to try` = not tried yet, `tried` = has a Result.

## Voice (`voice_name` in `config/pipeline.toml`)

Format: `<language>-<Region>-<Name>Neural-<Female|Male>`. After editing, run
`docker compose up --force-recreate`.

| Voice | Status | Result |
|---|---|---|
| `en-US-AriaNeural-Female` | current | |
| `en-US-JennyNeural-Female` | to try | |
| `en-US-GuyNeural-Male` | to try | |
| `en-GB-SoniaNeural-Female` | to try | |
| `en-AU-NatashaNeural-Female` | to try | |
| ElevenLabs (`elevenlabs:<voice_id>:<name>`) | to try later | Paid, needs a key. |

## Script model (`gemini_model_name` in `config/mpt-presets/llm-gemini.toml`)

| Model | Status | Result |
|---|---|---|
| `gemini-3.6-flash` | current | Named by Google after `gemini-2.0-flash` was retired. Free-tier limits unchecked. |
| Other providers (OpenAI, Ollama, etc.) | to try later | Presets in `config/mpt-presets/`. Free-only for now. |

## Where images and clips come from (`--sources`)

| Source | Status | Result |
|---|---|---|
| `wikipedia,commons` | current | Free, no key. Mostly still images, many CC BY-SA (medium flag). |
| `pexels` | to try | Free key. Adds video clips and simpler licensing. |
| `folder` (`library/scraped/`) | to try | Your own scraped or downloaded files, vetted like the rest. |

## Script length and grounding (see Limitation #1)

Settings in `[script]` of `config/pipeline.toml`. After editing, `docker compose up --force-recreate`.

| Option | Status | Result |
|---|---|---|
| Own writer, `target_words = 260` (~100 s), 12000 chars of sources | current | |
| `target_words = 150` (~60 s) | to try | |
| `target_words = 400` (~150 s) | to try | Check the facts closely: less source text per word. |
| Let MoneyPrinterTurbo write it (`provider = "mpt"`) | fallback | Short, about 1000 chars of sources. |
| Fact-check pass comparing each claim to the sources | to try | Extra AI call, logged. |
| Summarize long sources first | to try | Needed for true-crime length. |

## Video look and length (`config/pipeline.toml`, `[mpt]`)

| Setting | Current | Ideas | Result |
|---|---|---|---|
| `aspect` | `9:16` | `16:9` for long-form | |
| `clip_seconds` | `5` | shorter = faster cuts | |
| `paragraphs` | `3` | more = longer video | |
| `generate_scene_audio` | `false` | `true` to hear each scene at the review gate | |

## Features not built yet

| Idea | Status | Notes |
|---|---|---|
| `--voice` option in `poc.py` (per-video voice) | idea | Needs a small API change. |
| On-screen credits burned into the video | idea | Post-render ffmpeg step; MPT can't do this. |
| Image-content check (vision model) as an extra flag | idea | Limitation #2. Flags only, never filters. |
| True-crime mode | planned | After the POC works. Limitation #11. |
| Windows test run | planned | Limitation #10. |

## How to record a result

Change one thing at a time, render, and write one line under Result: what you changed, what
you saw, and whether to keep it. Commit the file with the change.
