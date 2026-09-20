import unittest

from pipeline.core.models import Asset
from pipeline.vetting.rules import clean_term, relevance, vet_all


def mk(title="", desc="", page="", lic="CC0"):
    return Asset(source="x", path="/x", title=title, description=desc, page_url=page, license=lic,
                 source_url="https://x/y", width=1000, height=800, author="a")


class Relevance(unittest.TestCase):
    def test_on_topic(self):
        r, _ = relevance(mk("Mitre Square, Whitechapel, site of a Jack the Ripper murder"), ["jack ripper"])
        self.assertEqual(r, 1.0)

    def test_off_topic_and_stopwords(self):
        r, why = relevance(mk("Woman rip a paper"), ["true crime jack ripper"])
        self.assertEqual(r, 0.0)
        self.assertIn("0 of 2", why)          # 'true' and 'crime' are ignored

    def test_partial_and_page_slug(self):
        r, _ = relevance(mk("A letter", page="https://www.pexels.com/photo/whitechapel-street-123/"), ["whitechapel killer"])
        self.assertEqual(r, 0.5)

    def test_plural(self):
        r, _ = relevance(mk("Old letters"), ["letter"])
        self.assertEqual(r, 1.0)

    def test_flag_only_when_topic_given(self):
        a, b = mk("Cat"), mk("Cat")
        vet_all([a]); vet_all([b], ["jack ripper"])
        self.assertIsNone(a.vetting.relevance)
        self.assertNotIn("RELEVANCE_LOW", [f.rule for f in a.vetting.flags])
        self.assertIn("RELEVANCE_LOW", [f.rule for f in b.vetting.flags])
        self.assertEqual(b.vetting.risk, "low")          # flag never raises risk or blocks

    def test_threshold_is_configurable(self):
        a = mk("Whitechapel street")
        vet_all([a], ["whitechapel killer"], 0.5); self.assertNotIn("RELEVANCE_LOW", [f.rule for f in a.vetting.flags])
        vet_all([a], ["whitechapel killer"], 0.8); self.assertIn("RELEVANCE_LOW", [f.rule for f in a.vetting.flags])
        self.assertEqual(a.vetting.relevance, 0.5)

    def test_clean_term(self):
        self.assertEqual(clean_term("  'jack ripper' "), "jack ripper")
        self.assertEqual(clean_term('“whitechapel  killer”'), "whitechapel killer")


if __name__ == "__main__":
    unittest.main()
