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
        self.assertIn("matched", out["on"][1])

    def test_no_shared_words_scores_zero(self):
        a = mk("a1", title="A basket of kittens")
        out = tfidf_scores([a], ["jack ripper whitechapel"])
        self.assertEqual(out["a1"][0], 0.0)
        self.assertIn("no words shared", out["a1"][1])

    def test_full_word_match_scores_1_regardless_of_other_keywords(self):
        """Regression test: an earlier version pooled every approved keyword into one combined query, which
        diluted a dead-on match under a dozen other keywords' unrelated words (a real asset that fully matched
        "jack the ripper suspects" scored ~0.1-0.2 once 9 other unrelated keywords were approved alongside it,
        instead of close to 1.0). Scoring must be per-keyword, best-wins, so adding more (different) approved
        keywords never lowers the score of an asset that's a full match for one of them."""
        a = mk("a1", title="Jack the Ripper suspects", desc="A gallery of Jack the Ripper suspects")
        few = tfidf_scores([a], ["jack the ripper suspects"])
        many = tfidf_scores([a], ["jack the ripper suspects", "jack the ripper victims", "jack the ripper letter",
                                   "jack the ripper dna", "whitechapel murders 1888", "aaron kosminski",
                                   "jack the ripper solved", "jack the ripper real face"])
        self.assertEqual(few["a1"][0], 1.0)
        self.assertEqual(many["a1"][0], 1.0)

    def test_long_document_is_not_penalized_for_extra_words(self):
        """Regression test: an earlier version used symmetric cosine similarity, which penalizes the *asset's*
        own extra words -- real Commons assets carry long, useful category lists, so a dead-on match still
        scored ~0.2-0.3 purely because the asset's text had many words the short keyword didn't. This should
        score as a full match: every one of the keyword's words is present, and the assets' other (also
        on-topic) words shouldn't count against it."""
        a = mk("a1", title="Charles Allen Lechmere",
               desc="Categories: 1920 deaths, Jack the Ripper, Whitechapel murders, Jack the Ripper suspects, "
                    "Studio photography, PD-old-70-expired, CC-PD-Mark, PD-old missing SDC copyright status")
        out = tfidf_scores([a], ["jack the ripper suspects"])
        self.assertEqual(out["a1"][0], 1.0)

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
