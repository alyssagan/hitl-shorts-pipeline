# Reference notes

Background gathered while designing the pipeline. Sources were public pages read in September 2026.

## RankReel (rankreel.higgsfield.app)

A browser-based countdown ("Top 5") video maker. Renders in the browser; the site says clips never
leave your device. It does **not** appear to offer keyword or SEO analysis.

| Area | Options |
|---|---|
| Script | 5,000 character limit, live word count and spoken-time estimate; drives caption timing |
| Voiceover | Generate AI read, record your own, or upload a file. Volume slider (default 1) |
| AI voices | Ridge, Vale, Atlas, Iris (US), Corbin, Wren (UK), Onyx, Blaze (US) |
| Media sources | AI, Upload (MP4, MOV, WEBM), Link |
| Ranking | On/off toggle for countdown numbering and persistent title; rank badges; drag to reorder; shuffle |
| Titles | Hook title toggle; font selection, stroke size, keyword color highlighting |
| Captions | Word-timed, four presets, generated from the voiceover |
| Canvas | 1080x1920; "Fill frame" or "Fit inside"; background color picker (default black) |
| Timeline | Split, Delete, Fit to voiceover, zoom |
| Export | MP4, H.264 + AAC, 30 fps, overlays burned in |

Not stated publicly: pricing, limits, watermarking, max length, AI model behind shot generation.

## MoneyPrinterTurbo (github.com/harry0703/MoneyPrinterTurbo)

Included as a git submodule at `vendor/MoneyPrinterTurbo`, pinned to a commit (MIT license).

- Config lives in `config.toml` (template: `config.example.toml`). `llm_provider` selects the script
  model (OpenAI, Anthropic, Gemini, DeepSeek, Ollama, OpenRouter and many more). `video_source`
  selects footage (`local`, Pexels, Pixabay, Coverr, AI video providers).
- Voice providers have their own sections: `[elevenlabs]`, `[kokoro]`, `[chatterbox]`, `[fish_audio]`,
  `[azure]`, and others. `subtitle_provider` is `edge` or `whisper`.
- REST API on port 8080 (`/docs` for OpenAPI). Endpoints this project uses: `POST /api/v1/scripts`,
  `/terms`, `/audio`, `/videos`, and `GET /api/v1/tasks/{id}` (state `1` done, `-1` failed, `4` running).
- `video_concat_mode = "sequential"` with `video_source = "local"` and `video_materials` uses clips in
  the order given, which is how the scene-review order reaches the renderer.
