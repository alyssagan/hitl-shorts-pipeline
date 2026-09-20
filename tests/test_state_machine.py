import unittest

from pipeline.core import state_machine as sm
from pipeline.core.models import Job, JobState as S, Keyword, Scene


def job_in(state, **kw):
    j = Job(subject="x", **kw)
    j.state = state
    return j


class StateMachineTests(unittest.TestCase):
    def test_happy_path(self):
        j = Job(subject="x")
        sm.apply(j, "start");            self.assertEqual(j.state, S.KEYWORDS_RUNNING)
        sm.apply(j, "keywords_ready");   self.assertEqual(j.state, S.KEYWORDS_REVIEW)
        j.keywords = [Keyword(term="a", approved=True)]
        sm.apply(j, "approve_keywords"); self.assertEqual(j.state, S.SCENES_RUNNING)
        sm.apply(j, "scenes_ready");     self.assertEqual(j.state, S.SCENES_REVIEW)
        j.scenes = [Scene(index=0, narration="n", approved=True)]
        sm.apply(j, "approve_scenes");   self.assertEqual(j.state, S.RENDERING)
        sm.apply(j, "render_done");      self.assertEqual(j.state, S.COMPLETED)

    def test_cannot_skip_a_human_gate(self):
        j = job_in(S.KEYWORDS_RUNNING)
        with self.assertRaises(sm.TransitionError):
            sm.apply(j, "approve_keywords")
        j = job_in(S.SCENES_RUNNING)
        with self.assertRaises(sm.TransitionError):
            sm.apply(j, "approve_scenes")
        j = job_in(S.KEYWORDS_REVIEW)
        with self.assertRaises(sm.TransitionError):
            sm.apply(j, "render_done")

    def test_keyword_gate_needs_an_approved_keyword(self):
        j = job_in(S.KEYWORDS_REVIEW)
        j.keywords = [Keyword(term="a")]
        with self.assertRaises(sm.TransitionError):
            sm.apply(j, "approve_keywords")
        self.assertEqual(j.state, S.KEYWORDS_REVIEW)   # unchanged on failure

    def test_scene_gate_needs_every_scene_approved(self):
        j = job_in(S.SCENES_REVIEW)
        j.scenes = [Scene(index=0, narration="a", approved=True), Scene(index=1, narration="b")]
        with self.assertRaises(sm.TransitionError):
            sm.apply(j, "approve_scenes")
        j.scenes = []
        with self.assertRaises(sm.TransitionError):
            sm.apply(j, "approve_scenes")

    def test_reject_loops_back(self):
        self.assertEqual(sm.apply(job_in(S.KEYWORDS_REVIEW), "reject_keywords").state, S.KEYWORDS_RUNNING)
        self.assertEqual(sm.apply(job_in(S.SCENES_REVIEW), "reject_scenes").state, S.SCENES_RUNNING)
        self.assertEqual(sm.apply(job_in(S.SCENES_REVIEW), "back_to_keywords").state, S.KEYWORDS_REVIEW)

    def test_fail_and_retry_resume_the_same_stage(self):
        for running in (S.KEYWORDS_RUNNING, S.SCENES_RUNNING, S.RENDERING):
            j = job_in(running)
            sm.apply(j, "fail")
            self.assertEqual((j.state, j.failed_from), (S.FAILED, running))
            sm.apply(j, "retry")
            self.assertEqual((j.state, j.failed_from), (running, None))

    def test_fail_only_from_running_states(self):
        for s in (S.CREATED, S.KEYWORDS_REVIEW, S.SCENES_REVIEW, S.COMPLETED):
            with self.assertRaises(sm.TransitionError):
                sm.apply(job_in(s), "fail")

    def test_retry_only_when_failed(self):
        with self.assertRaises(sm.TransitionError):
            sm.apply(job_in(S.KEYWORDS_RUNNING), "retry")

    def test_cancel_from_anywhere_non_terminal(self):
        for s in S:
            j = job_in(s)
            if s in (S.COMPLETED, S.CANCELLED):
                with self.assertRaises(sm.TransitionError):
                    sm.apply(j, "cancel")
            else:
                self.assertEqual(sm.apply(j, "cancel").state, S.CANCELLED)

    def test_terminal_states_are_dead_ends(self):
        for s in (S.COMPLETED, S.CANCELLED):
            self.assertEqual(sm.allowed_events(job_in(s)), [])

    def test_allowed_events_reflect_guards(self):
        j = job_in(S.KEYWORDS_REVIEW)
        self.assertNotIn("approve_keywords", sm.allowed_events(j))
        j.keywords = [Keyword(term="a", approved=True)]
        self.assertIn("approve_keywords", sm.allowed_events(j))

    def test_transitions_are_logged(self):
        j = Job(subject="x")
        sm.apply(j, "start", note="go")
        self.assertEqual(j.events[-1].kind, "transition")
        self.assertEqual(j.events[-1].data["event"], "start")

    def test_unknown_event_rejected(self):
        with self.assertRaises(sm.TransitionError):
            sm.apply(Job(subject="x"), "nonsense")


if __name__ == "__main__":
    unittest.main()
