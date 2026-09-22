import unittest

from pipeline.core.models import Asset
from pipeline.vetting.tfidf_relevance import tfidf_scores


def mk(id_, title="", desc="", page=""):
    return Asset(id=id_, source="x", path="/x", title=title, description=desc, page_url=page, license="CC0",
                 source_url="https://x/y", author="a")


class TfidfScoresTests(unittest.TestCase):
    def test_deterministic_and_repeatable(self):
        assets = [mk("a1", title="Mitre Square, site of a Jack the Ripper murder"),
                  mk("a2", title="A basket of kittens")]
        first = tfidf_scores(assets, ["jack ripper whitechapel"])
        second = tfidf_scores(assets, ["jack ripper whitechapel"])
        self.assertEqual(first, second)

    def test_on_topic_scores_higher_than_off_topic(self):
        on = mk("on", title="Jack the Ripper Whitechapel murder site")
        off = mk("off", title="A basket of kittens")
        out = tfidf_scores([on, off], ["jack ripper whitechapel"])
        self.assertGreater(out["on"][0], out["off"][0])
        self.assertIn("TF-IDF cosine match on", out["on"][1])

    def test_no_shared_words_scores_zero(self):
        a = mk("a1", title="A basket of kittens")
        out = tfidf_scores([a], ["jack ripper whitechapel"])
        self.assertEqual(out["a1"][0], 0.0)
        self.assertIn("no words shared", out["a1"][1])

    def test_empty_caption_scores_zero_not_missing(self):
        a = mk("a1")
        out = tfidf_scores([a], ["jack ripper"])
        self.assertEqual(out["a1"], (0.0, "no usable words in title/description/tags/page-URL"))

    def test_no_topic_terms_returns_empty(self):
        self.assertEqual(tfidf_scores([mk("a1", title="x")], []), {})

    def test_no_assets_returns_empty(self):
        self.assertEqual(tfidf_scores([], ["jack ripper"]), {})

    def test_score_is_bounded_0_to_1(self):
        a = mk("a1", title="Jack Ripper Jack Ripper Jack Ripper Whitechapel Whitechapel")
        out = tfidf_scores([a], ["jack ripper whitechapel"])
        self.assertLessEqual(out["a1"][0], 1.0)
        self.assertGreaterEqual(out["a1"][0], 0.0)

    def test_page_url_slug_counts_like_text(self):
        a = mk("a1", page="https://www.pexels.com/photo/whitechapel-street-123/")
        out = tfidf_scores([a], ["whitechapel killer"])
        self.assertGreater(out["a1"][0], 0.0)


if __name__ == "__main__":
    unittest.main()
