import importlib.util
import json
import tempfile
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

    def test_toml_section_reader_reads_llm_fallback_too(self):
        cfg = mk.read_toml_section(Path(__file__).resolve().parent.parent / "config" / "pipeline.toml", "llm_fallback")
        self.assertEqual(cfg.get("provider"), "groq")
        self.assertIn("api.groq.com", cfg.get("base_url", ""))

    def test_write_provenance_writes_a_meta_json_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "jack-the-ripper.txt"
            out.write_text("whitechapel 1888\n", encoding="utf-8")
            meta = mk.write_provenance(out, topic="jack the ripper", provider="primary", base_url="http://gemini",
                                       model="gemini-3.6-flash", era="1888 london", requested_count=24, kept_count=20,
                                       dropped_count=4, usage={"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
                                       now="2026-09-22T00:00:00Z")
            sidecar = Path(d) / "jack-the-ripper.meta.json"
            self.assertTrue(sidecar.exists())
            on_disk = json.loads(sidecar.read_text())
            self.assertEqual(on_disk, meta)
            self.assertEqual(meta["provider"], "primary")
            self.assertEqual(meta["model"], "gemini-3.6-flash")
            self.assertEqual(meta["usage"]["total_tokens"], 140)
            self.assertEqual(meta["era"], "1888 london")

    def test_write_provenance_with_no_usage_block(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "x.txt"
            meta = mk.write_provenance(out, topic="x", provider="groq", base_url="http://backup", model="m", usage=None)
            self.assertIsNone(meta["usage"])
            self.assertIsNone(meta["era"])          # empty era stays None, not ""


if __name__ == "__main__":
    unittest.main()


class Retry(unittest.TestCase):
    def _fake_urlopen(self, script):
        """`script` maps host -> a callable(attempt_n) -> a urlopen-like context manager or raises. Simplifies
        setting up primary-then-backup behavior across the two different base URLs call_llm() may hit."""
        import urllib.request
        calls = {"n": 0}

        def fake(req, timeout=0):
            calls["n"] += 1
            return script(req.full_url, calls["n"])
        real = urllib.request.urlopen
        urllib.request.urlopen = fake
        self.addCleanup(setattr, urllib.request, "urlopen", real)
        return calls

    @staticmethod
    def _ok(content="ok", usage=None):
        import io
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps({"choices": [{"message": {"content": content}}], "usage": usage or {}}).encode()
        return R()

    def test_retries_503_then_succeeds(self):
        import io, urllib.error
        calls = {"n": 0}
        def script(url, n):
            calls["n"] = n
            if n < 3:
                raise urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b"busy"))
            return self._ok("ok", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self._fake_urlopen(script)
        slept = []
        text, usage, provider, model, base = mk.call_llm("http://x", "m", "k", "p", waits=(1, 2, 3), sleep=slept.append)
        self.assertEqual(text, "ok")
        self.assertEqual(usage, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual((provider, model, base), ("primary", "m", "http://x"))
        self.assertEqual((calls["n"], slept), (3, [1, 2]))

    def test_primary_exhausted_falls_back_to_backup_provider(self):
        import io, urllib.error
        def script(url, n):
            if "backup" in url:
                return self._ok("from-backup", {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28})
            raise urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
        self._fake_urlopen(script)
        fallback = {"provider": "groq", "base_url": "http://backup", "model": "backup-model", "api_key": "bk"}
        text, usage, provider, model, base = mk.call_llm("http://primary", "primary-model", "k", "p", waits=(), fallback=fallback)
        self.assertEqual(text, "from-backup")
        self.assertEqual((provider, model, base), ("groq", "backup-model", "http://backup"))
        self.assertEqual(usage["total_tokens"], 28)

    def test_no_fallback_configured_exits_as_before(self):
        import io, urllib.error
        def script(url, n):
            raise urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
        self._fake_urlopen(script)
        with self.assertRaises(SystemExit):
            mk.call_llm("http://primary", "m", "k", "p", waits=(), fallback=None)

    def test_both_providers_fail_exits_with_a_clear_message(self):
        import io, urllib.error
        def script(url, n):
            raise urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
        self._fake_urlopen(script)
        fallback = {"provider": "groq", "base_url": "http://backup", "model": "backup-model", "api_key": "bk"}
        with self.assertRaises(SystemExit) as ctx:
            mk.call_llm("http://primary", "m", "k", "p", waits=(), fallback=fallback)
        self.assertIn("Still busy", str(ctx.exception))
