import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import apply_preset as ap  # noqa: E402

BASE = '''[app]
# comment kept
llm_provider = "moonshot"
openai_api_key = ""
pexels_api_keys = []

[whisper]
model_size = "large-v3"
'''


class ApplyPresetTests(unittest.TestCase):
    def test_replaces_existing_keys_and_keeps_comments(self):
        out = ap.overlay(BASE, {"app": {"llm_provider": "openai", "openai_api_key": "sk-x"}})
        d = tomllib.loads(out)
        self.assertEqual((d["app"]["llm_provider"], d["app"]["openai_api_key"]), ("openai", "sk-x"))
        self.assertIn("# comment kept", out)

    def test_appends_new_key_inside_its_own_section_not_the_next(self):
        d = tomllib.loads(ap.overlay(BASE, {"app": {"new_key": True}}))
        self.assertTrue(d["app"]["new_key"])
        self.assertNotIn("new_key", d["whisper"])

    def test_creates_missing_section(self):
        d = tomllib.loads(ap.overlay(BASE, {"elevenlabs": {"api_key": "k"}}))
        self.assertEqual(d["elevenlabs"]["api_key"], "k")

    def test_lists_and_quotes_roundtrip(self):
        d = tomllib.loads(ap.overlay(BASE, {"app": {"pexels_api_keys": ['a"b', "c"]}}))
        self.assertEqual(d["app"]["pexels_api_keys"], ['a"b', "c"])

    def test_env_expansion_and_missing_report(self):
        missing: set[str] = set()
        self.assertEqual(ap.expand(["${A}", "x-${B}"], {"A": "1"}, missing), ["1", "x-"])
        self.assertEqual(missing, {"B"})

    def test_later_presets_win(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "base.toml").write_text(BASE)
            (p / "one.toml").write_text('[app]\nllm_provider = "openai"\n')
            (p / "two.toml").write_text('[app]\nllm_provider = "gemini"\n')
            text, _ = ap.build(["one", "two"], p / "base.toml", p, {})
            self.assertEqual(tomllib.loads(text)["app"]["llm_provider"], "gemini")


if __name__ == "__main__":
    unittest.main()
