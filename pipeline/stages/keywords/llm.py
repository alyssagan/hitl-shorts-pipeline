"""Keyword/ranking stage backed by any OpenAI-compatible chat endpoint.

That covers OpenAI, Gemini and Anthropic through their OpenAI-compatible
endpoints, OpenRouter, and local servers such as Ollama (`http://localhost:11434/v1`)
-- so you pick the provider purely through config. NOTE: an LLM has no live
search-volume data, so `search_volume` / `difficulty` here are the model's
estimates and are flagged `estimated` in `meta`. Plug a real SEO data source in
as another KeywordStage when you have one (see README, "Adding a provider").

The `term` a keyword returns is not an SEO phrase -- it's a SEARCH QUERY that
gets typed straight into whichever photo/video libraries this job pulls from
(job.providers.sources), at Gate 2's sourcing step. A phrase tuned for how
people search YouTube/TikTok ("dyatlov pass explained", "... 2024 update")
finds nothing there: it never appears in an actual photo's caption. PROMPT
below asks specifically for phrases a camera could show, tailored by
_source_guidance() to whichever kind of library is actually configured --
see docs/RUNNING.md "Better keywords for photo/video search".
"""
from __future__ import annotations

import json

import httpx

from ...core import joblog
from ...core.decisions import ai
from ..llm_http import post_chat
from ...core.models import Job, Keyword, Scene
from ...sources.groups import ARCHIVE_SOURCES, GROUP_DEFINITIONS, STOCK_SOURCES, useful_groups
from ...niches import NICHE_KEYWORD_GUIDANCE
from ..base import StageContext

# Same order the original hardcoded prompt always listed them in -- research (text-only) first, then the
# three visual-search groups roughly narrowest-to-broadest (case -> historical -> stock).
_GROUP_ORDER = ("research", "case", "historical", "stock")


def _group_definitions_block() -> str:
    """The "research"/"case"/"historical"/"stock" bullet list PROMPT shows the model, built from
    pipeline/sources/groups.py's GROUP_DEFINITIONS (itself read from config/query_groups.toml) instead of
    being hardcoded here -- so editing a group's wording in that one file changes this prompt (and Gate 2's
    "Suggest more search terms", which reuses this same stage) without touching Python."""
    label_width = max(len(f'"{n}"') for n in _GROUP_ORDER) + 1
    lines = []
    for name in _GROUP_ORDER:
        d = GROUP_DEFINITIONS.get(name, {})
        label = f'"{name}"'.ljust(label_width)
        desc = (d.get("description") or "").strip()
        example, why = (d.get("example") or "").strip(), (d.get("example_why") or "").strip()
        tail = ""
        if example:
            tail = f' Example: "{example}"' + (f" ({why})." if why else ".")
        lines.append(f"  {label}-- {desc}{tail}")
    return "\n".join(lines)


def _already_covered_guidance(already_covered: list[str] | None) -> str:
    """Gate 2's "Suggest more search terms" (unlike Gate 1's first batch) runs against a job that may
    already have keywords -- approved, or left unapproved from an earlier round -- so the model needs to
    know what NOT to repeat. Empty string (no extra prompt text at all) when there's nothing to list, so a
    Gate-1 run (which never passes this) gets byte-for-byte the same prompt as before this existed."""
    terms = sorted({t.strip() for t in (already_covered or []) if t.strip()})
    if not terms:
        return ""
    return ("\nThese search phrases already exist for this job, from an earlier round -- do not repeat any "
            "of them or offer a near-duplicate of one: " + "; ".join(terms) + "\n")


def _source_guidance(sources: list[str]) -> str:
    """What makes a good search PHRASE depends on which libraries will actually be searched with it: an
    archive like Wikimedia Commons rewards a specific place/era/document, while a generic stock site like
    Pexels has nothing era-specific and rewards a generic visual subject instead. Returns the guidance line(s)
    for whichever of those this job actually has configured; falls back to guidance for the local clip
    library when the job has no web sources at all (job.providers.sources == [], README "no sources = your
    own clips only"). Also names which of the four GROUP labels (below in PROMPT) are even worth using --
    pipeline/sources/groups.py's GROUP_POOLS is what sourcing.py actually routes by, so a term in a group
    with nothing configured to receive it just sits unused this round."""
    names = set(sources)
    if not names:
        return ('- This job has no web sources configured -- these keywords are matched against filenames in '
                'your own library/clips/ folder instead. Phrase each one as a short, literal visual subject a '
                'clip might be named after ("snow covered mountain", "campfire at night"), not a topic or SEO '
                'phrase. Every term should use group "historical" (there is nothing here for "group" to route).')
    lines = []
    groups = useful_groups(names)
    if groups:
        lines.append(f"- Only these groups are useful this round, given the libraries configured: "
                      f"{', '.join(groups)}. A term in any other group won't be searched against anything -- "
                      f"don't spend a slot on one.")
    if names & ARCHIVE_SOURCES:
        lines.append('- Some of this round\'s libraries are historical archives or archive-heavy aggregators '
                      '(Wikipedia, Wikimedia Commons, Internet Archive, Library of Congress, Chronicling '
                      'America, Smithsonian, DPLA, Europeana, Openverse and/or Flickr): for "case"/"historical" '
                      'terms, include the year, decade, era or place where it helps ("whitechapel 1888", '
                      '"victorian london street", "handwritten court ledger"), and favor real documents, period '
                      'photographs, maps, buildings and clothing over generic modern stock.')
    if names & STOCK_SOURCES:
        lines.append('- Some of this round\'s libraries are modern, generic stock photo/video sites (Pexels, '
                      'Pixabay, Unsplash and/or NASA): they have NOTHING era- or event-specific, so "stock" '
                      'terms should favor generic, timeless visual subjects and moods instead ("snow-covered '
                      'mountain trail", "old wooden cabin interior", "campfire at night") rather than named '
                      'people, dates or a specific incident -- a phrase like that will simply find nothing there.')
    return "\n".join(lines) or '- Match the actual visual content these libraries hold, not the topic in the abstract.'


def _niche_guidance(niche: str | None) -> str:
    """One extra prompt line for the job's niche (requested directly, "MASTER NICHE PROMPT STRATEGIES"),
    steering phrasing toward that niche's aesthetic on top of _source_guidance() above -- empty string (no
    extra line at all) when the job has no niche set, so a job with niche=None gets byte-for-byte the same
    prompt as before this existed."""
    if not niche:
        return ""
    return NICHE_KEYWORD_GUIDANCE.get(niche, "")


PROMPT = """You are helping find real photos and video clips to show on screen in a short video about: {subject}
{feedback}
These phrases are SEARCH QUERIES for photo/video and reference-text libraries -- not SEO keywords for the
finished video's own title or description. A person will never read them; only a search box will. Libraries
being searched this round: {sources_line}

Every phrase belongs to exactly one GROUP, which decides which libraries it is even sent to (a term in the
wrong group for what it's asking for will search nothing useful). These are SEARCH EXAMPLES showing what a
phrase in each group looks like -- not a claim that matching material exists or will be found; do not invent
names, dates or addresses you are not confident are real just to fill a group:
{group_definitions}

Rules for a good phrase:
- 2 to 6 words, specific enough that a real photo or clip could actually match it. Never a single word (it
  matches too much random, unrelated material).
- Think like someone browsing a photo archive, not a marketer: what could a camera literally show -- a place,
  an object, a type of scene, an activity, weather, an era's clothing or technology? Not an abstract idea, a
  video title, or a platform name.
- Never use search-engine or social-media framing in the phrase itself: no "explained", "documentary",
  "TikTok", "short video", "2024 update", "facts", "top 10", "vs", or similar. Those describe a VIDEO ABOUT
  the topic, not a photo or clip OF something -- a photo/video library has nothing that will match them.
{source_guidance}
{niche_guidance}
{already_covered}
- Do not repeat yourself or offer near-duplicate phrases.

Return ONLY a JSON array of {n} objects, best first, each with:
  "term" (the search phrase itself -- everything above applies to this field specifically),
  "group" (one of "research"/"case"/"historical"/"stock", per the definitions above -- required),
  "entity" (the specific person/place/object/event this targets, if any; "" if none),
  "aliases" (array of other verified names/spellings for "entity"; [] if none),
  "dates" (array of specific dates/years this targets; [] if none),
  "locations" (array of specific places this targets; [] if none),
  "essential" (true if this is a hard requirement for the video, false if it's a nice-to-have detail),
  "visual_needed" (one short phrase: what should actually be visible on screen),
  "search_volume" (rough estimated monthly searches for the TOPIC in general, integer -- context only, never
  a reason to phrase "term" differently), "difficulty" (0-100 estimate), "why" (one short sentence on what it
  would actually show on screen).
"""


# Reordered 2026-09-25 (docs/PIPELINE_STAGES.md): the script now exists BEFORE any keyword does, so instead
# of N subject-wide phrases assigned to scenes arbitrarily (the old round-robin `keywords[i % len(keywords)]`,
# pipeline/stages/scenes/mpt.py), this asks for a SHOT LIST per scene, anchored to that scene's own
# narration. Kept as a separate prompt/method (run_for_scenes, below) rather than replacing PROMPT/run() above,
# which Gate 2's "Suggest more search terms" still uses unchanged for subject-wide, scene-agnostic top-ups.
#
# NEW 2026-09-26 (docs/ROADMAP.md backlog item F, "richer per-scene shot list"): each scene now asks for 2-4
# ordered queries instead of exactly one, so a scene isn't sunk by one overly-specific phrase finding nothing.
# The queries still collapse onto ONE Keyword per scene (queries[0] -> Keyword.term, queries[1:] ->
# Keyword.alternatives) rather than becoming separate Keywords -- see parse_scene_keywords() below for why:
# it keeps every existing scene_index/routing/approval invariant (#F) intact instead of reopening them.
SCENE_PROMPT = """You are building a SHOT LIST of real-photo/video SEARCH QUERIES for each scene of a short
video's already-written narration, about: {subject}
{feedback}
These phrases are SEARCH QUERIES for photo/video and reference-text libraries -- not SEO keywords, and not a
paraphrase of the narration itself. A person will never read them; only a search box will. Libraries being
searched this round: {sources_line}

Every scene's shot list belongs to exactly one GROUP, which decides which libraries it is even sent to (a
phrase in the wrong group for what it's asking for will search nothing useful). These are SEARCH EXAMPLES
showing what a phrase in each group looks like -- not a claim that matching material exists or will be
found; do not invent names, dates or addresses you are not confident are real just to fill a group:
{group_definitions}

Rules for a good shot list:
- For EACH scene, return 2 to 4 queries, ordered from the ideal, highly specific shot to a broad, reliable
  fallback -- so if the specific query turns up nothing, the next one still has a real chance of matching
  something. The FIRST query is the single most concrete, picturable subject actually named in that scene's
  own narration below (a place, person, document, object, date or moment) -- not a summary or paraphrase of
  the sentence. Each query after it should widen the visual ask a step at a time (drop a specific name/date
  for the type of place or era it belongs to, then to the plainest generic visual that still fits the scene)
  while staying true to what the scene is actually about -- never a random or unrelated fallback.
- Every query: 2 to 6 words, specific enough that a real photo or clip could actually match it. Never a
  single word (it matches too much random, unrelated material).
- Think like someone browsing a photo archive, not a marketer: what could a camera literally show?
- Never use search-engine or social-media framing in any query: no "explained", "documentary", "TikTok",
  "2024 update", "facts", "top 10", or similar.
{source_guidance}
{niche_guidance}
{already_covered}
- Do not repeat yourself or offer near-duplicate phrases across different scenes.

Here is each scene's narration, in order:
{scenes_block}

Return ONLY a JSON array with exactly one object per scene above, in the same order, each with:
  "scene_index" (integer, matching the scene number shown above -- required, and must be unique across
  the array),
  "queries" (array of 2 to 4 strings, ordered specific-first then broadening -- everything above applies to
  this field specifically),
  "group" (one of "research"/"case"/"historical"/"stock", per the definitions above -- required; applies to
  every query in this scene's list),
  "why" (one short sentence on what the FIRST query would actually show on screen, tied to that scene's own
  narration).
"""


class LLMKeywordStage:
    def __init__(self, base_url: str, api_key: str, model: str, count: int = 10, timeout: float = 120,
                 fallback: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.count = count
        self.timeout = timeout
        self.fallback = fallback   # config/pipeline.toml [llm_fallback], via registry.build_llm_fallback()
        self.actor = ai("keyword-llm", model=model)
        self.last_trace: dict = {}

    async def run(self, job: Job, ctx: StageContext, *, already_covered: list[str] | None = None,
                  extra_feedback: str = "") -> list[Keyword]:
        """`already_covered` and `extra_feedback` are both optional and both default to Gate 1's exact
        original behavior when omitted -- Gate 2's "Suggest more search terms" (orchestrator.suggest_keywords)
        is the only caller that passes them, asking for MORE phrases mid asset-review without reopening
        Gate 1: `already_covered` (job.keywords' own terms at call time) stops the model repeating a phrase
        already searched or already sitting unapproved from an earlier round; `extra_feedback` is whatever
        the reviewer typed about what's specifically missing this time."""
        feedback = ""
        if job.keyword_feedback:
            notes = "\n".join(f"- {f}" for f in job.keyword_feedback)
            feedback = f"The reviewer rejected earlier suggestions. Their notes:\n{notes}\n"
        if extra_feedback.strip():
            feedback += f"The reviewer specifically asked for: {extra_feedback.strip()}\n"
        sources = list(job.providers.sources)
        sources_line = ", ".join(sources) if sources else "none -- your own library/clips/ folder only"
        prompt = PROMPT.format(subject=job.subject, feedback=feedback, n=self.count, sources_line=sources_line,
                               source_guidance=_source_guidance(sources), niche_guidance=_niche_guidance(job.niche),
                               group_definitions=_group_definitions_block(),
                               already_covered=_already_covered_guidance(already_covered))

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        joblog.info("keywords", f"asking {self.model} for {self.count} keywords about '{job.subject}'")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await post_chat(client, f"{self.base_url}/chat/completions", headers,
                                   {"model": self.model, "messages": [{"role": "user", "content": prompt}]},
                                   what="keyword model", fallback=self.fallback)
            resp.raise_for_status()
        # What actually answered -- the primary model unless post_chat() fell back to the backup provider.
        model_used = getattr(resp, "pipeline_model", self.model)
        endpoint_used = self.fallback["base_url"] if getattr(resp, "pipeline_fell_back", False) and self.fallback else self.base_url
        self.last_trace = {"prompt": prompt, "model": model_used, "endpoint": endpoint_used,
                           "feedback_used": list(job.keyword_feedback), "sources_this_round": sources,
                           "niche": job.niche, "already_covered": sorted({t.strip() for t in (already_covered or []) if t.strip()}),
                           "extra_feedback": extra_feedback.strip(),
                           "note": "phrases are aimed at photo/video archive search for these sources, not SEO; "
                                    "volume/difficulty are the model's estimates, not measured data"}
        if getattr(resp, "pipeline_fell_back", False):
            self.last_trace["fell_back_from"] = {"model": self.model, "endpoint": self.base_url}
            self.last_trace["provider"] = getattr(resp, "pipeline_provider", "backup")
            self.actor = ai("keyword-llm", model=model_used)   # the decision log's actor should name who actually answered
        text = resp.json()["choices"][0]["message"]["content"]
        kws = parse_keywords(text, source=f"llm:{model_used}")
        joblog.info("keywords", f"got {len(kws)} keywords", terms=[k.term for k in kws])
        return kws

    async def run_for_scenes(self, job: Job, ctx: StageContext, scenes: list[Scene], *,
                              already_covered: list[str] | None = None, extra_feedback: str = "") -> list[Keyword]:
        """The new keywords_running (docs/PIPELINE_STAGES.md): a shot list of 2-4 ordered search queries
        per scene, anchored to that scene's own narration (Keyword.scene_index/.term/.alternatives),
        instead of N subject-wide terms with no connection to any particular paragraph. `already_covered`/
        `extra_feedback` mean the same as on run() above, though nothing currently passes them here (Gate 1
        no longer reruns keyword generation directly -- it reruns write_script(), which reruns this after)."""
        feedback = ""
        if job.keyword_feedback:
            notes = "\n".join(f"- {f}" for f in job.keyword_feedback)
            feedback = f"The reviewer rejected earlier suggestions. Their notes:\n{notes}\n"
        if extra_feedback.strip():
            feedback += f"The reviewer specifically asked for: {extra_feedback.strip()}\n"
        sources = list(job.providers.sources)
        sources_line = ", ".join(sources) if sources else "none -- your own library/clips/ folder only"
        prompt = SCENE_PROMPT.format(subject=job.subject, feedback=feedback, sources_line=sources_line,
                                     source_guidance=_source_guidance(sources), niche_guidance=_niche_guidance(job.niche),
                                     group_definitions=_group_definitions_block(),
                                     already_covered=_already_covered_guidance(already_covered),
                                     scenes_block=_scenes_block(scenes))

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        joblog.info("keywords", f"asking {self.model} for a per-scene shot list ({len(scenes)} scenes) about '{job.subject}'")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await post_chat(client, f"{self.base_url}/chat/completions", headers,
                                   {"model": self.model, "messages": [{"role": "user", "content": prompt}]},
                                   what="keyword model", fallback=self.fallback)
            resp.raise_for_status()
        model_used = getattr(resp, "pipeline_model", self.model)
        endpoint_used = self.fallback["base_url"] if getattr(resp, "pipeline_fell_back", False) and self.fallback else self.base_url
        self.last_trace = {"prompt": prompt, "model": model_used, "endpoint": endpoint_used,
                           "feedback_used": list(job.keyword_feedback), "sources_this_round": sources,
                           "niche": job.niche, "already_covered": sorted({t.strip() for t in (already_covered or []) if t.strip()}),
                           "extra_feedback": extra_feedback.strip(), "scenes": len(scenes),
                           "note": "a 2-4 query shot list per scene (specific-first), anchored to that scene's own narration"}
        if getattr(resp, "pipeline_fell_back", False):
            self.last_trace["fell_back_from"] = {"model": self.model, "endpoint": self.base_url}
            self.last_trace["provider"] = getattr(resp, "pipeline_provider", "backup")
            self.actor = ai("keyword-llm", model=model_used)
        text = resp.json()["choices"][0]["message"]["content"]
        kws = parse_scene_keywords(text, source=f"llm:{model_used}", scenes=scenes)
        joblog.info("keywords", f"got {len(kws)} scene keyword(s)", terms=[k.term for k in kws])
        return kws


def _scenes_block(scenes: list[Scene]) -> str:
    return "\n".join(f"Scene {s.index}: {s.narration}" for s in scenes)


_VALID_GROUPS = {"research", "case", "historical", "stock"}


def _extract_json_array(text: str) -> str | None:
    """The first top-level `[...]` in `text`, found by actually walking brackets/string state rather than a
    greedy regex. `re.search(r"\\[.*\\]", text, re.DOTALL)` (the old approach) grabs from the FIRST `[` to the
    LAST `]` in the WHOLE response -- fine when the model returns nothing but the array, but a chattier model
    (seen from a backup/fallback provider after the primary one was busy, real production failure) often adds
    trailing commentary after it ("Let me know if you'd like more options [here]."), and any `[`/`]` in THAT
    text -- including inside a bracketed reference/footnote, completely unrelated to the JSON -- got swept
    into the "array" and broke json.loads with a confusing delimiter error nowhere near the real problem.
    This instead starts at the first `[` and tracks bracket depth (correctly ignoring `[`/`]` that appear
    inside a JSON string, e.g. inside a "why" field) until depth returns to zero, which is the array's own
    true close -- everything after that, however malformed, is simply not part of what gets parsed."""
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None   # never closed -- truncated response


def parse_keywords(text: str, source: str) -> list[Keyword]:
    array_text = _extract_json_array(text)
    if not array_text:
        raise ValueError("keyword model did not return a JSON array")
    items = json.loads(array_text)
    out = []
    for rank, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not item.get("term"):
            continue
        out.append(
            Keyword(
                term=str(item["term"]).strip(),
                source=source,
                search_volume=_int(item.get("search_volume")),
                difficulty=_float(item.get("difficulty")),
                rank=rank,
                meta={"why": item.get("why", ""), "estimated": True},
                # Structured search-plan fields (docs/SEARCH_PLANNING.md, #3/#18): tolerant of an older/
                # non-conforming model response -- a missing or invalid "group" falls back to Keyword's
                # own default ("historical") rather than failing the whole batch over one bad field, and
                # every other field already defaults sensibly (empty string/list, essential=True) when absent.
                group=_group(item.get("group")),
                entity=str(item.get("entity") or "").strip(),
                aliases=_strlist(item.get("aliases")),
                dates=_strlist(item.get("dates")),
                locations=_strlist(item.get("locations")),
                essential=bool(item.get("essential", True)),
                visual_needed=str(item.get("visual_needed") or "").strip(),
            )
        )
    if not out:
        raise ValueError("keyword model returned no usable keywords")
    return out


def _shot_list_queries(item: dict) -> list[str]:
    """The 2-4 ordered queries for one scene item -- SCENE_PROMPT's "queries" array normally, but
    tolerant of a model that ignores the new field and still answers with the old single "term"
    (a stale cached response, a weaker fallback model, or a non-conforming replay) so a format slip
    degrades to a 1-query shot list instead of dropping the scene's keyword entirely."""
    raw = item.get("queries")
    if not isinstance(raw, list) or not raw:
        raw = [item.get("term")] if item.get("term") else []
    # Dedup while preserving the model's own specific-to-broad order; blank/non-string entries dropped.
    return list(dict.fromkeys(str(q).strip() for q in raw if str(q or "").strip()))


def parse_scene_keywords(text: str, source: str, scenes: list[Scene]) -> list[Keyword]:
    """Like parse_keywords() above, but each item must carry a valid, unique `scene_index` matching
    one of `scenes` -- a malformed/duplicate/out-of-range one is skipped rather than failing the whole
    batch (Orchestrator._assign_keywords_to_scenes fills any scene this leaves uncovered).

    NEW 2026-09-26 (docs/ROADMAP.md backlog item F): each scene item now carries a "queries" shot list
    (2-4 strings, most-specific-first) instead of one "term". This still produces exactly ONE Keyword
    per scene -- queries[0] becomes Keyword.term (everything that already reads .term, e.g. sourcing's
    queries_for(), routing, the decision log, keeps working unchanged) and queries[1:] become
    Keyword.alternatives (a field that already existed for exactly this: "other phrasings to try if
    this one comes up empty"). Orchestrator._apply_keyword_terms_to_scenes() is what turns that back
    into Scene.search_terms' own ordered list for pipeline/stages/scenes/clips.py to try in order."""
    array_text = _extract_json_array(text)
    if not array_text:
        raise ValueError("keyword model did not return a JSON array")
    items = json.loads(array_text)
    valid_indices = {s.index for s in scenes}
    seen: set[int] = set()
    out = []
    for rank, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        queries = _shot_list_queries(item)
        if not queries:
            continue
        idx = _int(item.get("scene_index"))
        if idx is None or idx not in valid_indices or idx in seen:
            continue
        seen.add(idx)
        out.append(Keyword(term=queries[0], alternatives=queries[1:], source=source, rank=rank,
                           meta={"why": item.get("why", ""), "estimated": True, "shot_list": queries},
                           group=_group(item.get("group")), scene_index=idx))
    if not out:
        raise ValueError("keyword model returned no usable scene keywords")
    return out


def _group(v):
    g = str(v or "").strip().lower()
    return g if g in _VALID_GROUPS else "historical"


def _strlist(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x or "").strip()]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
