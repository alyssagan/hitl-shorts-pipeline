import json
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core.models import Job, Keyword, Scene, TextRef
from pipeline.stages.base import StageContext
from pipeline.stages.keywords.llm import LLMKeywordStage, parse_keywords
from pipeline.stages.keywords.manual import ManualKeywordStage
from pipeline.stages.mpt_client import MptClient, MptError
from pipeline.stages.render.mpt import MptRenderStage
from pipeline.stages.scenes.clips import LocalFolderClipSource
from pipeline.stages.scenes.mpt import MptSceneStage, split_scenes


def ok(data):  # MoneyPrinterTurbo's response envelope
    return httpx.Response(200, json={"status": 200, "message": "success", "data": data})


class FakeMpt:
    """Minimal stand-in for the MoneyPrinterTurbo API, recording requests."""
    def __init__(self, video_states=(4, 1)):
        self.requests: list[tuple[str, str, dict]] = []
        self.video_states = list(video_states)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        self.requests.append((req.method, req.url.path, body))
        p = req.url.path
        if p == "/api/v1/scripts":
            return ok({"video_script": "Cats sleep a lot. They also purr. Some cats climb. Others hide."})
        if p == "/api/v1/terms":
            return ok({"video_terms": ["cat", "purr"]})
        if p == "/api/v1/audio":
            return ok({"task_id": "aud1"})
        if p == "/api/v1/videos":
            return ok({"task_id": "vid1"})
        if p.startswith("/api/v1/tasks/"):
            state = self.video_states.pop(0) if len(self.video_states) > 1 else self.video_states[0]
            return ok({"task_id": p.rsplit("/", 1)[1], "state": state,
                       "videos": ["http://mpt/tasks/vid1/final-1.mp4"] if state == 1 else None})
        if p.endswith(".mp3") or p.endswith(".mp4"):
            return httpx.Response(200, content=b"bytes")
        return httpx.Response(404, json={"status": 404, "message": "nope"})


class KeywordTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_keywords_tolerates_prose_around_json(self):
        kws = parse_keywords('Sure!\n[{"term":"cat facts","search_volume":"1200","difficulty":30.5,"why":"x"},{"nope":1}]', "llm:m")
        self.assertEqual(len(kws), 1)
        self.assertEqual((kws[0].term, kws[0].search_volume, kws[0].difficulty, kws[0].rank), ("cat facts", 1200, 30.5, 1))
        self.assertTrue(kws[0].meta["estimated"])

    def test_parse_keywords_errors(self):
        with self.assertRaises(ValueError):
            parse_keywords("no json here", "x")
        with self.assertRaises(ValueError):
            parse_keywords("[]", "x")

    async def test_manual_stage_dedupes_and_uses_seeds(self):
        job = Job(subject="cat facts")
        job.providers.options["seed_keywords"] = ["Cats", "cats", "purring"]
        out = await ManualKeywordStage().run(job, StageContext(Path(".")))
        self.assertEqual([k.term for k in out], ["Cats", "purring"])

    async def test_manual_stage_with_no_seeds_traces_the_subject_fallback(self):
        """Where the keywords came from (docs/LOGGING.md): no --keywords-file at all."""
        stage = ManualKeywordStage()
        await stage.run(Job(subject="true crime jack the ripper"), StageContext(Path(".")))
        self.assertIn("no --keywords-file was given", stage.last_trace["method"])

    async def test_manual_stage_surfaces_keywords_file_provenance_into_its_trace(self):
        """A --keywords-file made by scripts/make_keywords.py carries which API/model/tokens produced it
        (its .meta.json sidecar, read by poc.py into job.providers.options["keywords_provenance"]) all the way
        into the decision log's `proposed_keywords` entry -- docs/LOGGING.md "Where a keywords file came from"."""
        job = Job(subject="jack the ripper")
        job.providers.options["seed_keywords"] = ["whitechapel 1888"]
        job.providers.options["keywords_provenance"] = {
            "file": "library/keywords/jack-the-ripper.txt", "provider": "primary", "model": "gemini-3.6-flash",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "generated_at": "2026-09-22T00:00:00Z",
            "usage": {"prompt_tokens": 300, "completion_tokens": 120, "total_tokens": 420},
        }
        stage = ManualKeywordStage()
        await stage.run(job, StageContext(Path(".")))
        self.assertEqual(stage.last_trace["provider"], "primary")
        self.assertEqual(stage.last_trace["model"], "gemini-3.6-flash")
        self.assertEqual(stage.last_trace["usage"]["total_tokens"], 420)
        self.assertEqual(stage.last_trace["file"], "library/keywords/jack-the-ripper.txt")

    async def test_manual_stage_with_a_file_but_no_sidecar_still_traces_the_file_path(self):
        """A hand-written keywords file (no scripts/make_keywords.py, no .meta.json) still says *where* the
        keywords came from, even with no model/tokens to report."""
        job = Job(subject="jack the ripper")
        job.providers.options["seed_keywords"] = ["whitechapel 1888"]
        job.providers.options["keywords_provenance"] = {"file": "library/keywords/hand-written.txt"}
        stage = ManualKeywordStage()
        await stage.run(job, StageContext(Path(".")))
        self.assertEqual(stage.last_trace["file"], "library/keywords/hand-written.txt")
        self.assertNotIn("model", stage.last_trace)

    async def test_manual_stage_does_not_leak_trace_between_instances(self):
        """register_keywords("manual", ManualKeywordStage) hands out a fresh instance per run (registry.py) --
        this pins down that last_trace is per-instance, not a class attribute two jobs could share."""
        job_a = Job(subject="a")
        job_a.providers.options["seed_keywords"] = ["x"]
        job_a.providers.options["keywords_provenance"] = {"file": "a.txt", "model": "model-a"}
        job_b = Job(subject="b")
        job_b.providers.options["seed_keywords"] = ["y"]
        stage_a, stage_b = ManualKeywordStage(), ManualKeywordStage()
        await stage_a.run(job_a, StageContext(Path(".")))
        await stage_b.run(job_b, StageContext(Path(".")))
        self.assertEqual(stage_a.last_trace.get("model"), "model-a")
        self.assertNotIn("model", stage_b.last_trace)

    async def test_llm_stage_sends_feedback_and_auth(self):
        seen = {}
        def handler(req: httpx.Request):
            seen["auth"] = req.headers.get("authorization")
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": '[{"term":"a"}]'}}]})
        stage = LLMKeywordStage("http://llm/v1", "sk-test", "m")
        # route the stage's own httpx client through the mock
        real = httpx.AsyncClient
        httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
        try:
            job = Job(subject="cats"); job.keyword_feedback = ["too generic"]
            out = await stage.run(job, StageContext(Path(".")))
        finally:
            httpx.AsyncClient = real
        self.assertEqual(seen["auth"], "Bearer sk-test")
        self.assertIn("too generic", seen["body"]["messages"][0]["content"])
        self.assertEqual(out[0].term, "a")


class SceneTests(unittest.IsolatedAsyncioTestCase):
    def test_split_scenes(self):
        self.assertEqual(split_scenes("A.\n\nB."), ["A.", "B."])
        self.assertEqual(split_scenes("A. B. C. D. E."), ["A. B.", "C. D.", "E."])

    async def test_local_clip_source_matches_names_then_falls_back_and_never_reuses(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("a_ocean.mp4", "b_cat_video.mp4", "notes.txt"):
                (Path(d) / n).write_bytes(b"x")
            src = LocalFolderClipSource(d)
            used: set[str] = set()
            s = Scene(index=0, narration="n", search_terms=["cat"])
            first = await src.fetch(s, Path(d), used)
            self.assertTrue(first.endswith("b_cat_video.mp4"))
            used.add(first)
            second = await src.fetch(s, Path(d), used)      # no more 'cat' -> next unused
            self.assertTrue(second.endswith("a_ocean.mp4"))
            used.add(second)
            self.assertIsNone(await src.fetch(s, Path(d), used))

    async def test_script_prompt_never_exceeds_mpt_limit(self):
        mpt = FakeMpt()
        client = MptClient("http://mpt", transport=httpx.MockTransport(mpt))
        with tempfile.TemporaryDirectory() as lib, tempfile.TemporaryDirectory() as assets:
            src = Path(assets) / "wiki.txt"
            src.write_text("Title\n\n" + "Octopuses have three hearts. " * 500, encoding="utf-8")
            stage = MptSceneStage(client, LocalFolderClipSource(lib), voice_name="v", generate_audio=False)
            job = Job(subject="octopuses")
            job.keywords = [Keyword(term="octopus", approved=True)]
            job.references = [TextRef(source="wikipedia", title="Octopus", url="http://x", path=str(src))]
            job.scene_feedback = ["make it punchier " * 20]
            await stage.run(job, StageContext(Path(assets)))
        script_req = next(b for m, p, b in mpt.requests if p == "/api/v1/scripts")
        self.assertLessEqual(len(script_req["video_script_prompt"]), 2000)

    async def test_error_text_from_mpt_is_a_failure_not_a_script(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return ok({"video_script": "Error: 404 NOT_FOUND. This model is no longer available."})
        client = MptClient("http://mpt", transport=httpx.MockTransport(handler))
        with self.assertRaises(MptError) as cm:
            await client.script("octopuses")
        self.assertIn("404 NOT_FOUND", str(cm.exception))

    async def test_every_source_gets_a_share_of_the_grounding(self):
        mpt = FakeMpt()
        client = MptClient("http://mpt", transport=httpx.MockTransport(mpt))
        with tempfile.TemporaryDirectory() as lib, tempfile.TemporaryDirectory() as assets:
            a, b = Path(assets) / "a.txt", Path(assets) / "b.txt"
            a.write_text("Kraken\n\n" + "kraken legend. " * 800, encoding="utf-8")
            b.write_text("Octopus\n\n" + "octopus hearts. " * 800, encoding="utf-8")
            stage = MptSceneStage(client, LocalFolderClipSource(lib), voice_name="v", generate_audio=False)
            job = Job(subject="octopuses")
            job.keywords = [Keyword(term="octopus", approved=True)]
            job.references = [TextRef(source="wikipedia", title="Kraken", url="http://k", path=str(a)),
                              TextRef(source="wikipedia", title="Octopus", url="http://o", path=str(b))]
            await stage.run(job, StageContext(Path(assets)))
        prompt = next(b for m, p, b in mpt.requests if p == "/api/v1/scripts")["video_script_prompt"]
        self.assertIn("octopus hearts", prompt)
        self.assertLessEqual(len(prompt), 2000)

    async def test_mpt_scene_stage_builds_scenes_from_approved_keywords_only(self):
        mpt = FakeMpt()
        client = MptClient("http://mpt", transport=httpx.MockTransport(mpt))
        with tempfile.TemporaryDirectory() as lib, tempfile.TemporaryDirectory() as assets:
            (Path(lib) / "cat.mp4").write_bytes(b"x")
            stage = MptSceneStage(client, LocalFolderClipSource(lib), voice_name="v", generate_audio=True)
            job = Job(subject="cats")
            job.keywords = [Keyword(term="cat facts", approved=True), Keyword(term="dogs")]
            job.scene_feedback = ["punchier"]
            res = await stage.run(job, StageContext(Path(assets)))
        script_req = next(b for m, p, b in mpt.requests if p == "/api/v1/scripts")
        self.assertIn("cat facts", script_req["video_script_prompt"])
        self.assertNotIn("dogs", script_req["video_script_prompt"])
        self.assertIn("punchier", script_req["video_script_prompt"])
        self.assertEqual(len(res.scenes), 2)                      # 4 sentences / 2 per scene
        self.assertTrue(res.scenes[0].clip_path.endswith("cat.mp4"))
        self.assertIsNone(res.scenes[1].clip_path)                # library exhausted
        self.assertTrue(res.scenes[0].audio_path.endswith("scene_00.mp3"))


class RenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_sends_ordered_local_materials_and_downloads_result(self):
        mpt = FakeMpt(video_states=(4, 1))
        client = MptClient("http://mpt", transport=httpx.MockTransport(mpt))
        # make polling instant
        orig = client.wait_task
        async def fast(task_id, poll=0, timeout=5): return await orig(task_id, poll=0, timeout=timeout)
        client.wait_task = fast
        job = Job(subject="cats")
        job.scenes = [Scene(index=1, narration="second", clip_path="/lib/b.mp4"),
                      Scene(index=0, narration="first", clip_path="/lib/a.mp4")]
        with tempfile.TemporaryDirectory() as assets:
            stage = MptRenderStage(client, voice_name="v", clip_root_host="/lib", clip_root_mpt="/data/clips")
            out = await stage.run(job, StageContext(Path(assets)))
            self.assertEqual(Path(out).read_bytes(), b"bytes")
        params = next(b for m, p, b in mpt.requests if p == "/api/v1/videos")
        self.assertEqual([m["url"] for m in params["video_materials"]], ["/data/clips/a.mp4", "/data/clips/b.mp4"])
        self.assertEqual(params["video_source"], "local")
        self.assertEqual(params["video_concat_mode"], "sequential")
        self.assertEqual(params["video_script"], "first\n\nsecond")

    async def test_render_fails_clearly_without_clips(self):
        stage = MptRenderStage(MptClient("http://mpt", transport=httpx.MockTransport(FakeMpt())))
        job = Job(subject="x"); job.scenes = [Scene(index=0, narration="n")]
        with self.assertRaises(MptError):
            await stage.run(job, StageContext(Path(".")))

    async def test_failed_mpt_task_raises(self):
        mpt = FakeMpt(video_states=(-1,))
        client = MptClient("http://mpt", transport=httpx.MockTransport(mpt))
        with self.assertRaises(MptError):
            await client.wait_task("t", poll=0)


if __name__ == "__main__":
    unittest.main()


class ManualFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_single_words_or_filler(self):
        out = await ManualKeywordStage().run(Job(subject="true crime jack the ripper"), StageContext(Path(".")))
        self.assertEqual([k.term for k in out], ["true crime jack the ripper", "jack ripper"])
