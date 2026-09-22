"""Integration check for docs/LOGGING.md "Where a keywords file came from": when a job is created with
--keywords manual --keywords-file <file>, and poc.py found that file's .meta.json sidecar (written by
scripts/make_keywords.py), the API/model/tokens that produced the file actually land in the job's own
decision log -- not just printed to a terminal that scrolls away."""
from __future__ import annotations

import tempfile
import unittest

from pipeline.core.models import ProviderChoice
from pipeline.core.orchestrator import Orchestrator
from pipeline.core.store import JobStore
from pipeline.stages.registry import Registry
from pipeline.stages.keywords.manual import ManualKeywordStage
from tests.fakes import fake_registry


class KeywordsFileProvenanceLandsInDecisionLogTests(unittest.IsolatedAsyncioTestCase):
    def _registry(self):
        reg, stages = fake_registry()          # fake scenes/render; we register the REAL manual keyword stage
        reg.register_keywords("manual", ManualKeywordStage)
        return reg

    async def test_provenance_from_a_meta_json_sidecar_appears_in_proposed_keywords(self):
        with tempfile.TemporaryDirectory() as d:
            orch = Orchestrator(JobStore(d), self._registry(), settings={})
            job = await orch.create_job("jack the ripper", ProviderChoice(keywords="manual", scenes="fake", render="fake", sources=[]),
                                        reviewer="Aly")
            job.providers.options["seed_keywords"] = ["whitechapel 1888", "victorian london"]
            job.providers.options["keywords_provenance"] = {
                "file": "library/keywords/jack-the-ripper.txt", "provider": "primary", "model": "gemini-3.6-flash",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "usage": {"prompt_tokens": 300, "completion_tokens": 120, "total_tokens": 420},
            }
            orch.store.save(job)
            await orch.start(job.id, reviewer="Aly")
            await orch.run_pending(job.id)

            entries = orch.store.decisions(job.id).entries()
            proposed = [e for e in entries if e["action"] == "proposed_keywords"]
            self.assertEqual(len(proposed), 1)
            logic = proposed[0]["logic"]
            self.assertEqual(logic["file"], "library/keywords/jack-the-ripper.txt")
            self.assertEqual(logic["provider"], "primary")
            self.assertEqual(logic["model"], "gemini-3.6-flash")
            self.assertEqual(logic["usage"]["total_tokens"], 420)

    async def test_a_file_with_no_sidecar_still_records_the_file_path(self):
        with tempfile.TemporaryDirectory() as d:
            orch = Orchestrator(JobStore(d), self._registry(), settings={})
            job = await orch.create_job("jack the ripper", ProviderChoice(keywords="manual", scenes="fake", render="fake", sources=[]),
                                        reviewer="Aly")
            job.providers.options["seed_keywords"] = ["whitechapel 1888"]
            job.providers.options["keywords_provenance"] = {"file": "library/keywords/hand-written.txt"}
            orch.store.save(job)
            await orch.start(job.id, reviewer="Aly")
            await orch.run_pending(job.id)

            entries = orch.store.decisions(job.id).entries()
            logic = next(e for e in entries if e["action"] == "proposed_keywords")["logic"]
            self.assertEqual(logic["file"], "library/keywords/hand-written.txt")
            self.assertNotIn("model", logic)

    async def test_no_keywords_file_at_all_traces_the_subject_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            orch = Orchestrator(JobStore(d), self._registry(), settings={})
            job = await orch.create_job("jack the ripper", ProviderChoice(keywords="manual", scenes="fake", render="fake", sources=[]),
                                        reviewer="Aly")
            await orch.start(job.id, reviewer="Aly")
            await orch.run_pending(job.id)

            entries = orch.store.decisions(job.id).entries()
            logic = next(e for e in entries if e["action"] == "proposed_keywords")["logic"]
            self.assertIn("no --keywords-file was given", logic["method"])


if __name__ == "__main__":
    unittest.main()
