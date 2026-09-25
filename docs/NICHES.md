# Content niches

Requested directly, as a full architecture spec ("MASTER NICHE PROMPT STRATEGIES" + an "Evaluation Engine
Output Schema" for Stage 3). This is layered on top of the existing 3-gate pipeline (keywords → assets →
scenes, see the README's flow diagram) rather than a ground-up rearchitecture into that spec's own 5-stage
numbering -- see "What this does NOT do" at the bottom for why, and what to ask for if you actually want
that reordering.

## Setting a niche

Optional, set once at job creation (`POST /jobs {"subject": ..., "niche": "true_crime"}`, or
`scripts/poc.py --niche true_crime`). A job with no niche behaves exactly as it did before this existed --
nothing here changes any existing job. Five values, from the spec:

| Niche | `niche` value | Aesthetic |
|---|---|---|
| True Crime | `true_crime` | Gritty, dark, historical, atmospheric |
| Conspiracy | `conspiracy` | Same as True Crime |
| Science & Astronomy | `science` | Precise, high-tech, clean, data-driven |
| Pet Product | `pet_product` | Vibrant, warm, macro, high-energy, sensory |
| Food / Bakery | `food_bakery` | Same as Pet Product |

## What it actually changes

**Keyword phrasing (Stage 2 / Gate 1, `pipeline/stages/keywords/llm.py`).** One extra line is appended to
the keyword-writing prompt, per niche (`pipeline/niches.py`'s `NICHE_KEYWORD_GUIDANCE`) -- e.g. True Crime's
line asks for "gritty, dark, historical, atmospheric" phrasing and to penalize anything that reads as bright
modern stock; Pet Product's asks for "vibrant, warm, macro" phrasing built around texture and emotion. This
doesn't change which sources are searched (that's still `--sources`/`providers.sources`, unrelated to
niche) -- only how the LLM is asked to *phrase* the search terms sent to whichever sources you picked.

**Asset evaluation (Stage 3 / Gate 2, `pipeline/vetting/niche.py`).** Every vetting round, once a niche is
set, each asset gets a `niche_evaluation` alongside its existing risk/relevance vetting -- the exact schema
requested:

```json
{
  "niche_evaluated": "True Crime",
  "relevance_score": 8,
  "aesthetic_fit": "Excellent",
  "risk_assessment": "Risk LOW: no rule fired above 'info'. ...",
  "reasoning": "relevance 80%; risk low; source 'loc' is exactly the kind of material True Crime content should draw from",
  "action": "Approved"
}
```

- `relevance_score` (1-10) is the existing relevance score (`Vetting.relevance`, 0..1) scaled and floored at
  1 -- it's the SAME number already shown at Gate 2 as a percentage, just rescaled for this schema. `null`
  when the asset hasn't been relevance-scored yet (e.g. `defer_relevance`, docs/RUNNING.md).
- `aesthetic_fit` (`Excellent`/`Acceptable`/`Jarring`) is deterministic, based only on which source adapter
  the asset came from (`pipeline/niches.py`'s `NICHE_REWARD_SOURCES`/`NICHE_PENALIZE_SOURCES`) -- e.g. an
  archive source (Library of Congress, Chronicling America, Commons, ...) is `Excellent` for True Crime and
  `Jarring` for Pet Product; a generic stock source (Pexels/Pixabay/Unsplash) is the reverse. A source in
  neither list is `Acceptable`. This is a plain, explainable rule -- the same philosophy as
  `pipeline/vetting/rules.py`'s risk rules -- NOT a new LLM judgment call on the image itself; nothing here
  makes an additional paid API call.
- `risk_assessment` is `Vetting.summary`, carried over unchanged -- this never re-decides risk, only quotes
  it.
- `action` (`Approved`/`Flagged for Review`/`Rejected`) is a **suggested label only**. It is never applied to
  `Asset.status`. Gate 2 still requires an explicit human Approve/Reject on every asset, exactly as before --
  see `pipeline/vetting/rules.py`'s own opening line: "Nothing here approves or rejects an asset. Every asset
  still goes to a human." A niche evaluation doesn't change that.

See it per asset in the review page's "Why this score and risk" panel (an "aesthetic: ..." badge plus a
line in the panel), or as a flat report: `GET /jobs/{id}/niche-evaluation` returns
`{"niche": "...", "evaluations": [{"asset_id": ..., ...the schema above...}, ...]}` for every vetted asset.

## What this does NOT do

- **It doesn't add Instagram or TikTok as searchable sources.** The spec names them for Pet/Food. No adapter
  exists for either platform's search API in `pipeline/sources/` -- both would need paid/authenticated
  developer access this pipeline doesn't have configured (see `.env.example`), and nothing here purchases or
  wires up a new paid API without your say-so. A specific Instagram/TikTok URL can still be pulled one at a
  time with the existing "Add links" yt-dlp downloader (same path already used for YouTube/X/Vimeo/news
  links) -- it's just not an automated search source. Don't assume a niche guarantees automatically-
  searchable Instagram/TikTok material; it only ever has whatever specific links you paste in yourself.
- **It doesn't reorder or rename the pipeline's stages/gates to match the spec's own 5-stage numbering**
  (Script Generation → Keyword Generation → Asset Retrieval & Scoring → Assembly/Rough Cut → Final Export).
  This pipeline's existing 3-gate order -- keywords first (approved keywords drive both sourcing AND the
  script, which MoneyPrinterTurbo grounds in the approved Wikipedia research text at Gate 3) -- is a
  different, already-built and well-tested design (579+ tests) that a "script first" reordering would
  invert. That's a real architectural decision, not a routine one, so it wasn't made unilaterally here. If
  you actually want script-before-keywords, or a 5th gate splitting rough-cut polish from final sign-off,
  say so and it can be scoped as its own change.
- **It doesn't change which sources get searched.** `providers.sources`/`--sources` is unchanged and
  independent of niche; niche only biases phrasing and the evaluation schema.
