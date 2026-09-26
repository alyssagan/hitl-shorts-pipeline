"""scripts/poc.py's format_log_line: reformats one raw logs/pipeline.log line (docs/LOGGING.md's
`TIMESTAMP LEVEL  STAGE  message  k=v ...` format) for a live terminal stream -- trims the date off the
timestamp and, unless piped/NO_COLOR, colors the level and bolds the stage. This only touches what poc.py
echoes to the screen; it must never change what pipeline/core/joblog.py writes to the log file itself, and a
line it doesn't recognize must come back unchanged rather than mangled."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import poc  # noqa: E402

INFO_LINE = "2026-09-25T20:36:50Z INFO  decision   stage_finished: ok  by=orchestrator  area=keywords"
WARN_LINE = "2026-09-25T20:59:04Z WARN  sourcing   pexels skipped: PEXELS_API_KEY is not set"
ERROR_LINE = "2026-09-25T21:00:00Z ERROR sourcing   wikipedia failed: timeout"


class FormatLogLineTests(unittest.TestCase):
    def test_plain_mode_drops_the_date_but_keeps_every_field(self):
        out = poc.format_log_line(INFO_LINE, color=False)
        self.assertNotIn("2026-09-25", out, "the date is redundant during a live run")
        self.assertIn("20:36:50", out)
        self.assertIn("INFO", out)
        self.assertIn("decision", out)
        self.assertIn("stage_finished: ok  by=orchestrator  area=keywords", out)
        self.assertNotIn("\033", out, "no ANSI codes when color=False")

    def test_color_mode_wraps_level_and_stage_without_losing_the_message(self):
        out = poc.format_log_line(WARN_LINE, color=True)
        self.assertIn("\033[33m", out, "WARN should get the yellow code")
        self.assertIn("\033[1m", out, "the stage name should be bolded")
        self.assertIn("\033[0m", out, "codes must be reset, not left open")
        self.assertIn("pexels skipped: PEXELS_API_KEY is not set", out)

    def test_each_level_gets_a_distinct_color(self):
        info_out = poc.format_log_line(INFO_LINE, color=True)
        warn_out = poc.format_log_line(WARN_LINE, color=True)
        error_out = poc.format_log_line(ERROR_LINE, color=True)
        self.assertIn(poc._LEVEL_COLOR["INFO"], info_out)
        self.assertIn(poc._LEVEL_COLOR["WARN"], warn_out)
        self.assertIn(poc._LEVEL_COLOR["ERROR"], error_out)
        self.assertNotEqual(poc._LEVEL_COLOR["WARN"], poc._LEVEL_COLOR["ERROR"])

    def test_an_unrecognized_line_shape_passes_through_unchanged(self):
        odd = "not a real log line"
        self.assertEqual(poc.format_log_line(odd, color=True), odd)
        self.assertEqual(poc.format_log_line(odd, color=False), odd)

    def test_color_none_defers_to_use_color(self):
        with mock.patch.object(poc, "_use_color", return_value=True):
            self.assertIn("\033[", poc.format_log_line(INFO_LINE))
        with mock.patch.object(poc, "_use_color", return_value=False):
            self.assertNotIn("\033[", poc.format_log_line(INFO_LINE))


class UseColorTests(unittest.TestCase):
    def test_no_color_env_var_wins_even_on_a_real_terminal(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False), \
             mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertFalse(poc._use_color())

    def test_force_color_wins_even_when_piped(self):
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1", "NO_COLOR": ""}, clear=False), \
             mock.patch.object(sys.stdout, "isatty", return_value=False):
            self.assertTrue(poc._use_color())

    def test_defaults_to_isatty_when_neither_env_var_is_set(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "", "FORCE_COLOR": ""}, clear=False):
            with mock.patch.object(sys.stdout, "isatty", return_value=True):
                self.assertTrue(poc._use_color())
            with mock.patch.object(sys.stdout, "isatty", return_value=False):
                self.assertFalse(poc._use_color())


if __name__ == "__main__":
    unittest.main()
