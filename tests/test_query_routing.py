"""Query-group routing (#3/#18): research/case/historical/stock terms only reach the sources whose
pool they belong to -- but only once a job's keywords actually carry that information (the LLM's
structured plan); a manual-keyword job (every term at Keyword's bare default) keeps broadcasting every
term to every configured source exactly like before this feature existed (#1: preserve existing work).
Also covers #7's asset-category stamping from the originating query's group."""
import tempfile
import unittest
from pathlib import Path

from pipeline.core.models import Job, Keyword, ProviderChoice
from pipeline.sources.base import SourceResult
from pipeline.stages.base import StageContext
from pipeline.stages.sourcing import (
    SourcingStage, _routing_is_informative, _term_groups, route_queries,
)


def _job(sources, keywords):
    j = Job(subject="dyatlov pass", providers=ProviderChoice(keywords="fake", scenes="fake", render="fake", sources=sources))
    j.keywords = keywords
    return j


def kw(term, group="historical", approved=True):
    return Keyword(term=term, group=group, approved=approved)


class RoutingIsInformativeTests(unittest.TestCase):
    def test_all_default_historical_is_not_informative(self):
        j = _job(["commons"], [kw("cat photo"), kw("dog photo")])
        self.assertFalse(_routing_is_informative(j))

    def test_any_non_historical_group_makes_it_informative(self):
        j = _job(["commons", "pexels"], [kw("cat photo", "historical"), kw("empty hallway", "stock")])
        self.assertTrue(_routing_is_informative(j))

    def test_no_approved_keywords_is_not_informative(self):
        j = _job(["commons"], [kw("cat photo", approved=False)])
        self.assertFalse(_routing_is_informative(j))


class RouteQueriesTests(unittest.TestCase):
    def test_non_informative_sends_everything_everywhere(self):
        j = _job(["commons", "pexels"], [kw("cat photo", "historical"), kw("empty hallway", "stock")])
        term_group = _term_groups(j)
        terms = [k.term for k in j.keywords]
        self.assertEqual(route_queries(terms, "commons", term_group, informative=False), terms)
        self.assertEqual(route_queries(terms, "pexels", term_group, informative=False), terms)

    def test_informative_routes_case_only_to_archives(self):
        term_group = {"corazon amurao interview": "case", "empty hospital hallway": "stock"}
        terms = list(term_group)
        self.assertEqual(route_queries(terms, "commons", term_group, informative=True), ["corazon amurao interview"])
        self.assertEqual(route_queries(terms, "pexels", term_group, informative=True), ["empty hospital hallway"])

    def test_informative_routes_research_only_to_wikipedia(self):
        term_group = {"corazon amurao dyatlov": "research", "chicago streets 1960s": "historical"}
        terms = list(term_group)
        self.assertEqual(route_queries(terms, "wikipedia", term_group, informative=True), ["corazon amurao dyatlov"])
        self.assertEqual(route_queries(terms, "commons", term_group, informative=True), ["chicago streets 1960s"])

    def test_queryless_sources_ignore_routing_either_way(self):
        term_group = {"empty hospital hallway": "stock"}
        terms = list(term_group)
        self.assertEqual(route_queries(terms, "folder", term_group, informative=True), terms)
        self.assertEqual(route_queries(terms, "urls", term_group, informative=True), terms)

    def test_ungrouped_extra_query_reaches_every_source_even_when_informative(self):
        term_group = {"corazon amurao interview": "case"}   # "a hand-typed extra" has no entry
        terms = ["corazon amurao interview", "a hand-typed extra"]
        self.assertIn("a hand-typed extra", route_queries(terms, "pexels", term_group, informative=True))
        self.assertIn("a hand-typed extra", route_queries(terms, "commons", term_group, informative=True))


class _RecordingAdapter:
    """Minimal SourceAdapter that just records which queries it was asked and returns one Asset per
    query (query is preserved on the Asset, the same as every real HttpSource-based adapter does)."""
    label = "Recording"

    def __init__(self, name):
        self.name = name
        self.received: list[str] = []

    async def fetch(self, queries, ctx):
        self.received = list(queries)
        from pipeline.core.models import Asset
        assets = [Asset(source=self.name, path=f"/x/{self.name}-{i}", title=q, source_url=f"https://x/{self.name}/{i}",
                        query=q) for i, q in enumerate(queries)]
        trace = [{"source": self.name, "query": q, "found": 1, "kept": 1, "skipped": [], "kind": "media"} for q in queries]
        return SourceResult(assets=assets, trace=trace)


class SourcingStageRoutingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, sources, keywords):
        adapters = {n: _RecordingAdapter(n) for n in sources}
        stage = SourcingStage(adapters)
        job = _job(sources, keywords)
        with tempfile.TemporaryDirectory() as d:
            ctx = StageContext(assets_dir=Path(d), project_dir=Path(d))
            res = await stage.run(job, ctx)
        return adapters, res

    async def test_legacy_manual_job_broadcasts_every_term_to_every_source(self):
        adapters, res = await self._run(["commons", "pexels"], [kw("dyatlov pass mountain")])
        self.assertEqual(adapters["commons"].received, ["dyatlov pass mountain"])
        self.assertEqual(adapters["pexels"].received, ["dyatlov pass mountain"])
        # Legacy/non-informative mode never stamps a category -- same "not yet categorized" shape as before.
        self.assertTrue(all(a.category is None for a in res.assets))

    async def test_informative_job_routes_case_and_stock_to_their_own_pools_only(self):
        keywords = [kw("corazon amurao interview", "case"), kw("empty hospital hallway", "stock")]
        adapters, res = await self._run(["commons", "pexels"], keywords)
        self.assertEqual(adapters["commons"].received, ["corazon amurao interview"])
        self.assertEqual(adapters["pexels"].received, ["empty hospital hallway"])

    async def test_wikipedia_only_gets_research_terms(self):
        keywords = [kw("corazon amurao dyatlov", "research"), kw("empty hospital hallway", "stock")]
        adapters, res = await self._run(["wikipedia", "pexels"], keywords)
        self.assertEqual(adapters["wikipedia"].received, ["corazon amurao dyatlov"])
        self.assertEqual(adapters["pexels"].received, ["empty hospital hallway"])

    async def test_source_with_no_routed_queries_is_not_called_and_is_traced(self):
        keywords = [kw("empty hospital hallway", "stock")]
        adapters, res = await self._run(["commons", "pexels"], keywords)
        self.assertEqual(adapters["commons"].received, [])   # never called with an empty list either
        note = next(t for t in res.trace if t.get("source") == "commons")
        self.assertEqual(note["outcome"], "no_queries_routed")

    async def test_category_stamped_from_query_group_never_verified_case(self):
        keywords = [kw("corazon amurao interview", "case"), kw("chicago streets 1960s", "historical"),
                    kw("empty hospital hallway", "stock")]
        adapters, res = await self._run(["commons", "pexels"], keywords)
        by_query = {a.query: a.category for a in res.assets}
        self.assertEqual(by_query["corazon amurao interview"], "unverified_case_candidate")
        self.assertEqual(by_query["chicago streets 1960s"], "historical_context")
        self.assertEqual(by_query["empty hospital hallway"], "illustrative_stock")
        self.assertNotIn("verified_case", by_query.values())

    async def test_existing_category_is_never_overwritten(self):
        # Simulates a re-run (search again) where an asset from an earlier round was already categorized
        # (possibly by a human) -- routing/stamping must never clobber that.
        keywords = [kw("corazon amurao interview", "case")]
        adapters = {"commons": _RecordingAdapter("commons")}
        stage = SourcingStage(adapters)
        job = _job(["commons"], keywords)
        # Monkeypatch the adapter to return a pre-categorized asset.
        from pipeline.core.models import Asset
        async def fetch(queries, ctx):
            return SourceResult(assets=[Asset(source="commons", path="/x", title="t", source_url="https://x/1",
                                              query="corazon amurao interview", category="reconstruction")],
                                trace=[{"source": "commons", "query": "corazon amurao interview", "found": 1,
                                       "kept": 1, "skipped": [], "kind": "media"}])
        adapters["commons"].fetch = fetch
        with tempfile.TemporaryDirectory() as d:
            ctx = StageContext(assets_dir=Path(d), project_dir=Path(d))
            res = await stage.run(job, ctx)
        self.assertEqual(res.assets[0].category, "reconstruction")


if __name__ == "__main__":
    unittest.main()
