import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("mk", Path(__file__).resolve().parent.parent / "scripts" / "make_keywords.py")
mk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mk)


class MakeKeywords(unittest.TestCase):
    def test_parse_fenced_json_and_clean(self):
        reply = '```json\n{"places": ["\\"whitechapel 1888\\"", "ripper", "Whitechapel 1888"], "atmosphere_broll": ["foggy victorian street"]}\n```'
        kept, dropped = mk.clean(mk.parse_reply(reply))
        self.assertEqual(kept, {"places": ["whitechapel 1888"], "atmosphere_broll": ["foggy victorian street"]})
        self.assertEqual(len(dropped), 2)                     # the single word and the repeat

    def test_render_is_a_valid_keywords_file(self):
        text = mk.render("jack the ripper", {"places": ["whitechapel 1888"]})
        terms = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        self.assertEqual(terms, ["whitechapel 1888"])

    def test_prompt_mentions_topic_rules(self):
        p = mk.build_prompt("h h holmes", 20, "1890s Chicago")
        for s in ("h h holmes", "1890s Chicago", "NEVER a single word", "no corpses"):
            self.assertIn(s, p)

    def test_config_reader(self):
        cfg = mk.read_keywords_config(Path(__file__).resolve().parent.parent / "config" / "pipeline.toml")
        self.assertIn("generativelanguage", cfg["base_url"])


if __name__ == "__main__":
    unittest.main()
