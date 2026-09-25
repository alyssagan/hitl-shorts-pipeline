# Script-writing styles

Requested directly: 4 ready-made scriptwriter "personas," each a system prompt plus a word-count target,
tuned for viral short-form retention mechanics (hooks, open loops, twists). This is a separate, independent
feature from [content niches](NICHES.md) -- see "How this relates to niches" below for why they don't line
up one-to-one, and "What this does NOT do" for the bigger script-first pipeline reorder this is a first step
toward, not a replacement for.

## Setting a script style

Optional, set once at job creation (`POST /jobs {"subject": ..., "script_style": "true_crime_mystery"}`, or
`scripts/poc.py --script-style true_crime_mystery`). A job with no `script_style` behaves exactly as it did
before this existed -- the scriptwriter (`pipeline/stages/scenes/writer.py`) uses its original prompt,
grounded in whatever source text was pulled during sourcing. Four values:

| Style | `script_style` value | Target length | Built for |
|---|---|---|---|
| History, Conspiracy & True Crime | `true_crime_mystery` | 130-160 words (~61-75s) | Historical mysteries, true crime cases, conspiracy topics |
| STEM (Science, Anatomy, Biology, Chemistry) | `stem_science` | 130-160 words (~61-75s) | Scientific concepts, bodily functions, cosmic/microscopic scale |
| DTC, Pet & Food Marketing | `dtc_marketing` | 70-110 words (~30-50s) | Product plugs, pet products, boutique food/bakery promotion |
| Math, Statistics & Computer Science | `math_cs` | 130-160 words (~61-75s) | Algorithms, data paradoxes, coding quirks, mathematical anomalies |

Every word above -- the system prompts, the retention-mechanics instructions, the exact word targets -- is
reproduced verbatim from what was requested; nothing was paraphrased or "improved." The full text of each is
in `pipeline/stages/scenes/script_styles.py`.

## What it actually changes

**Only `SCENES_RUNNING`** (Gate 3's machine stage, `pipeline/stages/scenes/writer.py`'s `ScriptWriter`).
When `job.script_style` is set to a known style:

- The chat call sends the style's system prompt as a `system` message (not folded into the user message the
  way the default prompt is) and a `user` message built from the style's task template with `job.subject`
  substituted in, plus reviewer notes from a rejected draft appended (the one thing every script path always
  honors -- see `docs/RUNNING.md`, rejecting at Gate 3).
- The word-count check that produces a `warning` in the decision log uses the style's own range (e.g.
  70-110 for `dtc_marketing`) instead of the config's single `target_words`.
- Everything downstream of the script text itself is unaffected: `clean_script()`, scene-splitting
  (`pipeline/stages/scenes/mpt.py`'s `split_scenes()`), clip matching, rendering -- all exactly as before.

Nothing else changes. Which sources get searched, how keywords are phrased, asset vetting/scoring, the state
machine -- none of it reads `job.script_style`.

## Accuracy trade-off

The default scriptwriter prompt is explicitly grounded: "use ONLY facts stated in the source text below,"
reading up to `grounding_chars` of real Wikipedia/archive text pulled during sourcing. **The 4 styles above
are not** -- as given, none of them reference source text at all; they're built to work from the topic name
alone, using the model's own general knowledge, in service of a specific scriptwriting voice. That's a
deliberate product trade-off in the spec as requested, not an oversight here, but it means:

- A styled script can state something a source wouldn't verify. This matters far more for
  `true_crime_mystery` (real people, real cases, specific claims that can be simply wrong) than for
  `stem_science` or `math_cs` (general, widely-known concepts) or `dtc_marketing` (your own product, which
  you already know).
- `job.references` (Wikipedia articles pulled during `SOURCING_RUNNING`) are read by the default prompt but
  deliberately ignored by every styled prompt -- confirmed by
  `tests/test_script_styles.py::ScriptWriterStyledPathTests::test_style_selection_ignores_the_grounded_default_prompt_entirely`.
- Nothing here fact-checks a styled script against anything. Gate 3 (`SCENES_REVIEW`) is still where a human
  reads it before it renders -- treat that review more carefully for `true_crime_mystery` jobs about real
  people than you would for the others.

If you want a styled voice AND source grounding together, that's a real, separate feature (splice source
text into the style's task message, defining exactly how "stay in this voice" and "only state sourced facts"
should resolve when they conflict) -- not built here; ask for it if you want it.

## How this relates to niches

[`Job.niche`](NICHES.md) (5 values: `true_crime`/`conspiracy`/`science`/`pet_product`/`food_bakery`) biases
**keyword phrasing** and **asset aesthetic scoring**. `Job.script_style` (this doc, 4 values) controls the
**script's own voice, structure and length**. They are read by completely unrelated code
(`pipeline/stages/keywords/llm.py` and `pipeline/vetting/niche.py` for niche;
`pipeline/stages/scenes/writer.py` for script_style) and neither one sets or implies the other.

The two lists don't map one-to-one on purpose:

- `true_crime_mystery` covers both niche's `true_crime` AND `conspiracy` -- the retention-mechanics structure
  (open loop, escalating evidence) is the same for a true-crime case and a conspiracy theory, even though
  their *sourcing* aesthetic differs (true crime wants case-specific documents; conspiracy still leans
  archival/atmospheric but isn't tied to one verifiable case).
- `dtc_marketing` covers both niche's `pet_product` AND `food_bakery` -- same "native UGC, soft-sell, CTA"
  voice either way.
- `math_cs` has no niche equivalent at all -- `pipeline/niches.py` doesn't have a Math/Stats/CS aesthetic,
  because the original "MASTER NICHE PROMPT STRATEGIES" spec never named one.

A job can set either, neither, or both. The obvious pairing is `niche="conspiracy"` (archival-leaning
sourcing/asset scoring) with `script_style="true_crime_mystery"` (the matching voice) -- but nothing
enforces that pairing (`tests/test_script_styles.py::CreateJobScriptStyleTests::test_niche_and_script_style_are_independent`
covers a deliberately mismatched pair working fine), since a mismatch might be exactly what you want (e.g. a
`stem_science`-voiced script over `pet_product`-aesthetic imagery).

## What this does NOT do

- **It does not reorder the pipeline to write the script before keywords/sourcing.** This is a first,
  additive step toward that larger idea (see `docs/NICHES.md`'s own "What this does NOT do" for why a full
  reorder is a bigger architectural call): the script text still becomes available at `SCENES_RUNNING`,
  after keywords and sourcing, exactly where it always has. Deriving a second, scene-targeted keyword pass
  from the finished script (to search sources for exactly what's being narrated, per source-group phrasing
  rules `pipeline/stages/keywords/llm.py`'s `_source_guidance()` already knows) is a natural next increment
  on top of this, feeding into the existing "search again" mechanism at Gate 2 -- not built here.
- **It does not fact-check or ground a styled script.** See "Accuracy trade-off" above.
- **It does not change the scriptwriter's provider/model/fallback behavior.** A styled call goes through the
  exact same `post_chat()` retry (same-provider, up to 4x) + one-fallback-to-backup-provider path as every
  other script call (`docs/LOGGING.md` "LLM fallback provider") -- styling only changes the prompt content
  and the word-count target, never the transport.
