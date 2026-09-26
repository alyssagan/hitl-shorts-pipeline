"""scripts/poc.py's JOB_STATE_ORDER (used by main()'s at() to skip a gate the job already passed) and
script_gate() (Gate 1 in the terminal). Regression coverage for a real bug found while checking the
2026-09-25 reorder was actually ready to test end-to-end: JOB_STATE_ORDER (then an inline list local to
main()) still had its pre-reorder shape, missing script_running/script_review entirely. Since at() treats a
state that isn't in the list as "already past everything", a fresh job -- which now sits at script_review
immediately after start() -- had every gate silently skipped, and main() ran straight to
`wait_for(..., {"completed"}, ...)`, hanging forever: nothing ever approved the script it was waiting on.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import poc  # noqa: E402

from pipeline.core.models import JobState


class JobStateOrderMatchesTheRealStateMachineTests(unittest.TestCase):
    def test_every_non_terminal_job_state_is_present_exactly_once(self):
        # FAILED/CANCELLED aren't pipeline steps to wait through, so at()'s list never needed them.
        expected = [s.value for s in JobState if s not in (JobState.FAILED, JobState.CANCELLED)]
        self.assertEqual(poc.JOB_STATE_ORDER, expected)

    def test_script_review_comes_before_every_later_gate(self):
        # The exact regression: script_review must be in the list at all, and before keywords/assets/scenes,
        # or a fresh job (which now stops at script_review first) gets every gate below skipped by at().
        i = poc.JOB_STATE_ORDER.index("script_review")
        for later in ("keywords_review", "assets_review", "scenes_review", "completed"):
            self.assertLess(i, poc.JOB_STATE_ORDER.index(later), f"script_review must come before {later}")


class FakeApi:
    """Records every call() and lets a test script canned GET responses (poc.py's Api talks real HTTP;
    this stands in for it so script_gate() can be tested without a server)."""
    def __init__(self, gets: list[dict]):
        self.base = "http://fake"
        self.gets = list(gets)     # consumed in order by GET /jobs/{id}
        self.calls: list[tuple[str, str, dict | None]] = []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/log" in path:
            return {"lines": [], "next": 0}       # show_log()'s own GET -- not a job-state poll
        if method == "GET":
            return self.gets.pop(0) if self.gets else self.gets[-1]
        if method == "POST" and path.endswith("/script/approve"):
            return {"id": "j1", "state": "keywords_running"}
        if method == "POST" and path.endswith("/script/reject"):
            return {"id": "j1", "state": "script_running"}
        raise AssertionError(f"unexpected call: {method} {path}")


class ScriptGateTests(unittest.TestCase):
    def setUp(self):
        self.job = {"id": "j1", "slug": "cats", "script": "Cats are great.\n\nThey purr."}

    def test_yes_approves_as_written(self):
        api = FakeApi([])
        with mock.patch.object(poc, "ask", side_effect=["yes"]):
            out = poc.script_gate(api, self.job, "Aly")
        self.assertEqual(out["state"], "keywords_running")
        method, path, body = api.calls[-1]
        self.assertEqual((method, path), ("POST", "/jobs/j1/script/approve"))
        self.assertEqual(body, {"reviewer": "Aly"})   # no edited_script -- approved as-is

    def test_edit_sends_the_edited_draft(self):
        api = FakeApi([])
        with mock.patch.object(poc, "ask", side_effect=["edit"]), \
             mock.patch.object(poc, "edit_script_draft", return_value="Cats are EXTREMELY great."):
            out = poc.script_gate(api, self.job, "Aly")
        self.assertEqual(out["state"], "keywords_running")
        method, path, body = api.calls[-1]
        self.assertEqual((method, path), ("POST", "/jobs/j1/script/approve"))
        self.assertEqual(body, {"reviewer": "Aly", "edited_script": "Cats are EXTREMELY great."})

    def test_no_rejects_then_loops_back_for_a_second_look(self):
        # First answer rejects with feedback; wait_for's first GET (after the reject POST) reports the
        # rewrite is done (back at script_review); the loop then asks again and this time approves.
        api = FakeApi([{"id": "j1", "state": "script_review", "script": "Rewritten narration."}])
        with mock.patch.object(poc, "ask", side_effect=["no", "make it punchier", "yes"]):
            out = poc.script_gate(api, self.job, "Aly")
        self.assertEqual(out["state"], "keywords_running")
        kinds = [(m, p) for m, p, _ in api.calls]
        self.assertIn(("POST", "/jobs/j1/script/reject"), kinds)
        reject_body = next(b for m, p, b in api.calls if p == "/jobs/j1/script/reject")
        self.assertEqual(reject_body, {"feedback": "make it punchier", "reviewer": "Aly"})
        self.assertIn(("POST", "/jobs/j1/script/approve"), kinds)


if __name__ == "__main__":
    unittest.main()
