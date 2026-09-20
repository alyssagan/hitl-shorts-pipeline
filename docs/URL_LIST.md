# Pulling videos from your own list of URLs

Put links in a text file, one per line. Optionally add a note and where it should go in the video,
separated by `|`:

```
# urls.txt   (lines starting with # are ignored)
https://www.youtube.com/watch?v=XXXX | octopus escaping its tank | intro
https://example.com/clips/reef.mp4 | b-roll of the reef | 3
https://www.tiktok.com/@someone/video/123 | reaction shot | end
```

Run it with any other sources you like:

```
python3 scripts/poc.py "3 surprising facts about octopuses" --keywords manual \
    --sources wikipedia,commons --urls urls.txt --reviewer "Aly"
```

`--urls` adds the `urls` source automatically. You still approve keywords first (the other sources search with them).

## What is saved

In the project folder, `sources/urls/`:

- `files/` the downloaded videos or images;
- `info/` yt-dlp's full record of each link (title, uploader, duration, upload date, the platform's license label);
- `manifest.json` each kept file with its URL, uploader, duration, your note and position;
- `requests.jsonl` every download attempted, including failures.

Every kept file is also in `DECISIONS.md` and the asset review, with its link and uploader.

## Position hints

The third column tells the pipeline where a clip belongs: a scene number (`1` is the first scene), `intro`
(first scene), or `end` (last scene). Clips with a position are held back for their own scene; other scenes
choose from the remaining approved assets as usual. You can still reorder at the scene review.

## Risk

Every video from a social or video platform is flagged **high risk** (`PLATFORM_SOURCE`). The uploader may
not own the rights, and the platform's terms may forbid downloading. To approve one you must write a note,
which is saved in the decision log. Links that go to a plain file (`.mp4`, `.jpg`) have no license unless
you know it, so they are flagged high risk for unknown license too. The risk stays with you, as with RankReel.

## Limits

Set in `config/pipeline.toml` under `[sources]`: `url_max_mb` (default 200) and `url_max_height` (default 1080).
Playlists are not expanded: each line is one video. Sites change often, so a link that fails today may work after
`docker compose up --build` pulls a newer yt-dlp.
