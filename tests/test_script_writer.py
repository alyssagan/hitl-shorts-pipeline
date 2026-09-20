import json
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core.models import Job, Keyword, TextRef
from pipeline.stages.base import StageContext
from pipeline.stages.mpt_client import MptClient, MptError
from pipeline.stages.scenes.clips import LocalFolderClipSource
from pipeline.stages.scenes.mpt import MptSceneStage
from pipeline.stages.scenes.writer import ScriptWriter, clean_script

LONG = "\n\n".join(f"Paragraph {i} has a fact about octopus hearts and arms. It is short." for i in range(8))


def chat(reply, status=200, capture=None):
    def handler(req: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(json.loads(req.content))
            capture.append(dict(req.headers))
        if status != 200:
            return httpx.Response(status, json={"error": {"message": reply}})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    return httpx.MockTransport(handler)


def job_with_sources(d: Path) -> Job:
    a, b = d / "a.txt", d / "b.txt"
    a.write_text("Kraken\n\n" + "kraken legend. " * 2000, encoding="utf-8")
    b.write_text("Octopus\n\n" + "octopus hearts. " * 2000, encoding="utf-8")
    job = Job(subject="octopuses")
    job.keywords = [Keyword(term="octopus", approved=True)]
    job.references = [TextRef(source="wikipedia", title="Kraken", url="http://k", path=str(a)),
                      TextRef(source="wikipedia", title="Octopus", url="http://o", path=str(b))]
    return job


class ScriptWriterTests(unittest.IsolatedAsyncioTestCase):
    def test_clean_script_strips_markup(self):
        raw = "# Title\n**Bold** fact (see note) [1].\n- bullet one\n1. numbered"
        out = clean_script(raw)
        self.assertNotIn("#", out)
        self.assertNotIn("*", out)
        self.assertNotIn("[1]", out)
        self.assertNotIn("(see note)", out)
        self.assertIn("bullet one", out)
        self.assertNotIn("- bullet", out)

    async def test_prompt_has_target_length_and_reads_more_than_2000_chars_from_every_source(self):
        seen: list = []
        with tempfile.TemporaryDirectory() as d:
            job = job_with_sources(Path(d))
            w = ScriptWriter("http://llm/v1", "k", "m", target_words=300, grounding_chars=6000,
                             transport=chat(LONG, capture=seen))
            script, trace = await w.write(job, ["octopus"])
        prompt = seen[0]["messages"][0]["content"]
        self.assertIn("about 300 words", prompt)
        self.assertGreater(len(prompt), 5000)               # far past MPT's 2000-character limit
        self.assertIn("octopus hearts", prompt)              # second source is not crowded out
        self.assertIn("ONLY facts stated in the source text", prompt)
        self.assertEqual(seen[1]["authorization"], "Bearer k")
        self.assertIn("Paragraph 3", script)
        self.assertEqual({g["title"] for g in trace["grounded_on"]}, {"Kraken", "Octopus"})
        self.assertIn("warning", trace)                     # 8 short paragraphs is far below 300 words

    async def test_reviewer_notes_reach_the_prompt(self):
        seen: list = []
        job = Job(subject="octopuses")
        job.scene_feedback = ["longer please"]
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat(LONG, capture=seen))
        await w.write(job, [])
        self.assertIn("longer please", seen[0]["messages"][0]["content"])

    async def test_provider_error_becomes_a_clear_failure(self):
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat("quota exceeded", status=429))
        with self.assertRaises(MptError) as cm:
            await w.write(Job(subject="x"), [])
        self.assertIn("429", str(cm.exception))
        self.assertIn("quota exceeded", str(cm.exception))

    async def test_scene_stage_uses_writer_instead_of_mpt(self):
        mpt_calls: list = []

        def mpt(req: httpx.Request) -> httpx.Response:
            mpt_calls.append(req.url.path)
            return httpx.Response(404, json={})
        with tempfile.TemporaryDirectory() as lib, tempfile.TemporaryDirectory() as assets:
            job = job_with_sources(Path(assets))
            writer = ScriptWriter("http://llm/v1", "k", "gemini-x", transport=chat(LONG))
            stage = MptSceneStage(MptClient("http://mpt", transport=httpx.MockTransport(mpt)),
                                  LocalFolderClipSource(lib), writer=writer)
            res = await stage.run(job, StageContext(Path(assets)))
        self.assertEqual(len(res.scenes), 8)
        self.assertNotIn("/api/v1/scripts", mpt_calls)
        self.assertEqual(stage.actor.model, "gemini-x")
        self.assertEqual(stage.last_trace["writer"], "own")


if __name__ == "__main__":
    unittest.main()
