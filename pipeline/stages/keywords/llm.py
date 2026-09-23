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
from ...core.models import Job, Keyword
from ...sources.groups import ARCHIVE_SOURCES, STOCK_SOURCES, useful_groups
from ..base import StageContext


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


PROMPT = """You are helping find real photos and video clips to show on screen in a short video about: {subject}
{feedback}
These phrases are SEARCH QUERIES for photo/video and reference-text libraries -- not SEO keywords for the
finished video's own title or description. A person will never read them; only a search box will. Libraries
being searched this round: {sources_line}

Every phrase belongs to exactly one GROUP, which decides which libraries it is even sent to (a term in the
wrong group for what it's asking for will search nothing useful). These are SEARCH EXAMPLES showing what a
phrase in each group looks like -- not a claim that matching material exists or will be found; do not invent
names, dates or addresses you are not confident are real just to fill a group:
  "research"   -- background reading for the narration itself, not a visual search. Sent only to Wikipedia.
                  Example: "Corazon Amurao Dyatlov" (a real full name + event, so an article can be found --
                  not a generic topic phrase like "the case explained").
  "case"       -- names a SPECIFIC real, verifiable person, place, document or moment tied directly to this
                  case: an actual name, a specific address, an interview, a court proceeding. Example:
                  "Corazon Amurao interview" (a specific person and moment -- not a guarantee a matching
                  photo exists, just an honest, specific search).
  "historical" -- the general era, place or everyday life the case happened in, WITHOUT claiming to show the
                  specific people or event. Example: "Chicago residential streets 1960s" (the era/place, not
                  a specific person or moment).
  "stock"      -- a generic, timeless visual description with no connection to this specific case -- a shot
                  that could illustrate many different stories. Example: "empty hospital hallway".

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

    async def run(self, job: Job, ctx: StageContext) -> list[Keyword]:
        feedback = ""
        if job.keyword_feedback:
            notes = "\n".join(f"- {f}" for f in job.keyword_feedback)
            feedback = f"The reviewer rejected earlier suggestions. Their notes:\n{notes}\n"
        sources = list(job.providers.sources)
        sources_line = ", ".join(sources) if sources else "none -- your own library/clips/ folder only"
        prompt = PROMPT.format(subject=job.subject, feedback=feedback, n=self.count, sources_line=sources_line,
                               source_guidance=_source_guidance(sources))

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
