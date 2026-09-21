import tempfile
import unittest
from pathlib import Path

from pipeline.core import joblog


class JobLogTests(unittest.TestCase):
    def test_write_read_levels_and_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            joblog.write(d, "DEBUG", "http", "GET 200", url="https://x")
            joblog.write(d, "INFO", "sourcing", "pulling from wikipedia", api_key="AIzaSECRET", queries=["a b"])
            joblog.write(d, "ERROR", "stage", "boom")
            info, nxt = joblog.read(d)                       # default INFO and up
            self.assertEqual(len(info), 2)
            self.assertNotIn("AIzaSECRET", "\n".join(info))
            self.assertIn("api_key=***", info[0])
            allv, _ = joblog.read(d, min_level="DEBUG")
            self.assertEqual(len(allv), 3)
            self.assertEqual(joblog.read(d, after=nxt)[0], [])       # cursor: nothing new
            joblog.write(d, "INFO", "state", "a -> b")
            self.assertEqual(len(joblog.read(d, after=nxt)[0]), 1)

    def test_bound_directory_used_by_log(self):
        with tempfile.TemporaryDirectory() as d:
            tok = joblog.bind(Path(d))
            joblog.info("sourcing", "hello")
            joblog.unbind(tok)
            joblog.info("sourcing", "not saved anywhere")
            self.assertEqual(len(joblog.read(Path(d))[0]), 1)
