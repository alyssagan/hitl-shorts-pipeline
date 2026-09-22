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


class Retry(unittest.TestCase):
    def test_retries_503_then_succeeds(self):
        import io, json, urllib.error, urllib.request
        calls = {"n": 0}
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps({"choices": [{"message": {"content": "ok"}}],
                                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()
        def fake(req, timeout=0):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b"busy"))
            return R()
        real = urllib.request.urlopen
        urllib.request.urlopen = fake
        try:
            slept = []
            text, usage = mk.call_llm("http://x", "m", "k", "p", waits=(1, 2, 3), sleep=slept.append)
        finally:
            urllib.request.urlopen = real
        self.assertEqual(text, "ok")
        self.assertEqual(usage, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual((calls["n"], slept), (3, [1, 2]))
