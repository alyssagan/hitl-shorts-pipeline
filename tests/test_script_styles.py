"""Script-writing styles (docs/SCRIPT_STYLES.md): the 4 scriptwriter personas themselves
(pipeline/stages/scenes/script_styles.py), ScriptWriter's styled prompt path, and orchestrator wiring
(Job.script_style persists, independent of Job.niche, an unknown style is rejected at creation)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core.models import Job
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.stages.mpt_client import MptError
from pipeline.stages.scenes.script_styles import SCRIPT_STYLES, STYLE_LABELS
from pipeline.stages.scenes.writer import ScriptWriter
from tests.fakes import fake_registry


class StyleConstantsTests(unittest.TestCase):
    def test_four_styles_are_defined_with_matching_keys_and_labels(self):
        self.assertEqual(set(SCRIPT_STYLES), {"true_crime_mystery", "stem_science", "dtc_marketing", "math_cs"})
        self.assertEqual(set(STYLE_LABELS), set(SCRIPT_STYLES))
        for key, spec in SCRIPT_STYLES.items():
            self.assertEqual(spec.key, key)
            self.assertEqual(spec.label, STYLE_LABELS[key])

    def test_every_style_has_a_sane_word_range_and_a_subject_placeholder(self):
        for spec in SCRIPT_STYLES.values():
            self.assertLess(spec.min_words, spec.max_words)
            self.assertIn("{subject}", spec.task_template)
            self.assertTrue(spec.system_prompt.strip())

    def test_dtc_marketing_has_the_shorter_word_target(self):
        # The only one of the 4 tuned for ~30-50s pacing rather than ~61-75s (requested directly).
        dtc = SCRIPT_STYLES["dtc_marketing"]
        others = [s for k, s in SCRIPT_STYLES.items() if k != "dtc_marketing"]
        self.assertTrue(all(dtc.max_words < o.min_words for o in others))

    def test_styles_do_not_mention_source_grounding(self):
        # These prompts are meant to work from the topic alone (see script_styles.py's docstring
        # "Accuracy trade-off") -- unlike the default writer prompt, they never reference source text.
        for spec in SCRIPT_STYLES.values():
            self.assertNotIn("source", spec.system_prompt.lower())
            self.assertNotIn("source", spec.task_template.lower())


def chat(reply, status=200, capture=None):
    def handler(req: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(json.loads(req.content))
        if status != 200:
            return httpx.Response(status, json={"error": {"message": reply}})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    return httpx.MockTransport(handler)


TRUE_CRIME_REPLY = " ".join(["word"] * 145)   # inside true_crime_mystery's 130-160 range
SHORT_REPLY = " ".join(["word"] * 20)          # well outside every style's range


class ScriptWriterStyledPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_styled_script_uses_the_personas_system_prompt_as_a_system_message(self):
        seen: list = []
        job = Job(subject="the Dyatlov Pass incident", script_style="true_crime_mystery")
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat(TRUE_CRIME_REPLY, capture=seen))
        script, trace = await w.write(job, ["keyword the default path would have used"])
        messages = seen[0]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("True Crime and Historical Mystery Scriptwriter", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("the Dyatlov Pass incident", messages[1]["content"])
        self.assertIn("Target exactly 130 to 160 words", messages[1]["content"])
        self.assertEqual(trace["style"], "true_crime_mystery")
        self.assertEqual(trace["grounded_on"], [])   # deliberately not grounded in job.references
        self.assertEqual(trace["target_words_range"], [130, 160])

    async def test_style_selection_ignores_the_grounded_default_prompt_entirely(self):
        # A styled job still has references (e.g. from an earlier sourcing round) -- they must not leak in.
        with tempfile.TemporaryDirectory() as d:
            ref_path = Path(d) / "a.txt"
            ref_path.write_text("Title\n\nsome sourced fact that should not appear", encoding="utf-8")
            from pipeline.core.models import TextRef
            job = Job(subject="P vs NP", script_style="math_cs")
            job.references = [TextRef(source="wikipedia", title="Title", url="http://x", path=str(ref_path))]
            seen: list = []
            w = ScriptWriter("http://llm/v1", "k", "m", transport=chat(TRUE_CRIME_REPLY, capture=seen))
            await w.write(job, [])
        prompt_text = json.dumps(seen[0]["messages"])
        self.assertNotIn("should not appear", prompt_text)

    async def test_reviewer_feedback_still_reaches_a_styled_prompt(self):
        seen: list = []
        job = Job(subject="P vs NP", script_style="math_cs")
        job.scene_feedback = ["too slow to get to the twist"]
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat(TRUE_CRIME_REPLY, capture=seen))
        await w.write(job, [])
        self.assertIn("too slow to get to the twist", seen[0]["messages"][1]["content"])

    async def test_word_count_outside_the_styles_range_is_flagged(self):
        job = Job(subject="P vs NP", script_style="math_cs")
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat(SHORT_REPLY))
        script, trace = await w.write(job, [])
        self.assertIn("warning", trace)
        self.assertIn("Math, Statistics & Computer Science", trace["warning"])

    async def test_word_count_inside_the_styles_range_is_not_flagged(self):
        job = Job(subject="P vs NP", script_style="math_cs")   # math_cs range is 130-160, same as true_crime_mystery
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat(TRUE_CRIME_REPLY))
        script, trace = await w.write(job, [])
        self.assertNotIn("warning", trace)

    async def test_dtc_marketing_uses_its_own_shorter_range(self):
        seen: list = []
        job = Job(subject="Scoops Pet Bakery", script_style="dtc_marketing")
        reply = " ".join(["word"] * 90)   # inside 70-110
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat(reply, capture=seen))
        script, trace = await w.write(job, [])
        self.assertIn("promoting: Scoops Pet Bakery", seen[0]["messages"][1]["content"])
        self.assertNotIn("warning", trace)
        self.assertEqual(trace["target_words_range"], [70, 110])

    async def test_provider_error_becomes_a_clear_failure_same_as_the_default_path(self):
        job = Job(subject="x", script_style="stem_science")
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat("quota exceeded", status=429))
        with self.assertRaises(MptError) as cm:
            await w.write(job, [])
        self.assertIn("429", str(cm.exception))

    async def test_unset_script_style_is_untouched_by_any_of_this(self):
        # No job.script_style at all -- must behave byte-for-byte like before this feature existed.
        seen: list = []
        job = Job(subject="octopuses")
        w = ScriptWriter("http://llm/v1", "k", "m", transport=chat(TRUE_CRIME_REPLY, capture=seen))
        script, trace = await w.write(job, [])
        self.assertNotIn("style", trace)
        self.assertEqual(len(seen[0]["messages"]), 1)   # single user message, no system message injected
        self.assertEqual(seen[0]["messages"][0]["role"], "user")


class CreateJobScriptStyleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make(self):
        reg, _ = fake_registry()
        store = JobStore(self.root / "projects")
        return Orchestrator(store, reg, {"review": {}})

    async def test_script_style_is_optional_and_defaults_to_none(self):
        orch = self.make()
        job = await orch.create_job("Cute cats!")
        self.assertIsNone(job.script_style)

    async def test_a_valid_script_style_persists_on_the_job(self):
        orch = self.make()
        job = await orch.create_job("the Dyatlov Pass incident", script_style="true_crime_mystery")
        self.assertEqual(job.script_style, "true_crime_mystery")
        reloaded = orch.get(job.id)   # round-trips through JobStore's save/load
        self.assertEqual(reloaded.script_style, "true_crime_mystery")

    async def test_an_unknown_script_style_is_rejected(self):
        orch = self.make()
        with self.assertRaises(ValueError):
            await orch.create_job("Cute cats!", script_style="not_a_real_style")

    async def test_niche_and_script_style_are_independent(self):
        orch = self.make()
        job = await orch.create_job("Jack the Ripper", niche="true_crime", script_style="math_cs")
        self.assertEqual(job.niche, "true_crime")
        self.assertEqual(job.script_style, "math_cs")   # mismatched pairing is allowed -- nothing enforces it


if __name__ == "__main__":
    unittest.main()
