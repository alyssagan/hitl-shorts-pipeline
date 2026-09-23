# Search planning: query groups, routing and asset categories

(Requirement #3/#18: source-appropriate query groups, per-source routing, provenance preserved.)

The pipeline's main goal is finding real, relevant photos and footage -- especially material actually
connected to a specific case -- while keeping that clearly distinguished from historical context, generic
stock, and reconstructions. That distinction starts at the keyword stage: every search phrase now declares
what KIND of thing it's looking for, and that decides both which libraries it's sent to and what the
resulting asset is honestly labeled as.

## The four groups

`Keyword.group` (`pipeline/core/models.py`) is one of:

| Group | What it's for | Sent to | Resulting `Asset.category` |
|---|---|---|---|
| `research` | Background reading for the narration. Not a visual search. | Wikipedia only | (none -- produces a `TextRef`, never an `Asset`) |
| `case` | A specific, verifiable person/place/document/moment tied directly to the case. | Every archive-capable source except Wikipedia | `unverified_case_candidate` |
| `historical` | The general era/place/everyday life the case happened in, without claiming to show the specific people or event. | Same pool as `case` | `historical_context` |
| `stock` | A generic, timeless visual with no case-specific connection. | Generic stock sites only | `illustrative_stock` |

The example phrases used throughout the prompt and this doc ("Corazon Amurao interview", "Chicago
residential streets 1960s", "empty hospital hallway") are SEARCH EXAMPLES showing what a phrase in each
group looks like -- never a claim that matching material exists or will be found. The keyword LLM stage is
explicitly told not to invent names, dates or addresses just to fill a group.

**`category` is never auto-promoted to `verified_case`.** A `case`-group search finding something doesn't
mean the pipeline has confirmed it depicts the real person or event -- only a human reviewer moving it there
explicitly does that (see `identity_status`/`identity_reviewer` on `Asset`, requirement #7). The same goes
for `reconstruction`: nothing in search or vetting ever assigns it.

## Which sources are in which pool

`pipeline/sources/groups.py` is the single place this is defined:

- `ARCHIVE_SOURCES` (case/historical pool, plus Wikipedia for research): wikipedia, commons, archive, loc,
  smithsonian, chronicling_america, dpla, europeana, openverse, flickr.
- `STOCK_SOURCES` (stock pool): pexels, pixabay, unsplash, nasa.
- `QUERYLESS_SOURCES` (routing doesn't apply): folder (imports its whole configured folder), urls (pulls a
  per-job URL list) -- neither one searches by query at all.

Both the keyword-writing prompt (`pipeline/stages/keywords/llm.py`, so it tells the model which groups are
even worth suggesting terms in, given this job's configured sources) and the sourcing router
(`pipeline/stages/sourcing.py`) import from this one module, so the routing table can't drift out of sync
between "what the LLM was told" and "what actually happens."

## Routing: `route_queries()` in `pipeline/stages/sourcing.py`

Each round, every configured source gets only the approved-keyword terms whose group pools to it -- a
`case`-group term like "Corazon Amurao interview" never reaches Pexels; a `stock`-group term like "empty
hospital hallway" never reaches Wikimedia Commons.

**Backward compatibility (#1: preserve existing work).** Routing only activates when a job's approved
keywords actually carry group diversity -- i.e. the LLM keyword stage populated `group` from its structured
plan. `_routing_is_informative()` checks this: if every keyword is sitting at `Keyword`'s bare default
(`"historical"` -- true for the manual keyword stage, and for any job whose keywords predate this feature),
routing is skipped entirely and every configured source gets every term, exactly as it always did. This
avoids the alternative failure mode -- a manual-keyword job's stock/archive sources silently going quiet
because a field they never populated says everything is "historical."

**Extra queries always get through.** A search term a reviewer typed in by hand when rejecting a batch
(`extra_queries`, Gate 2 "search again") was never LLM-classified into a group. When routing is active, an
unrecognized term is sent to every configured source rather than silently dropped -- an explicit human
addition is never withheld by machinery built for machine-suggested terms.

**A source with nothing routed to it this round isn't silently quiet.** If routing filters a configured
source down to zero queries, it isn't called at all, and the round's trace records why
(`outcome: "no_queries_routed"`) rather than looking like a search that happened to find nothing (#4: never
silently convert "nothing to search for" into "searched and found nothing").

## Category stamping

After a source returns its results, each new asset's `category` is stamped from the group of the query that
found it (`Asset.query`, already recorded by every source), via `GROUP_ASSET_CATEGORY`:
`case -> unverified_case_candidate`, `historical -> historical_context`, `stock -> illustrative_stock`.
Stamping only happens when routing is active (see above) -- in legacy/manual mode, `category` stays `None`
("not yet categorized"), the same as before this feature existed. An asset that already has a `category`
(a human set it, or an earlier round already stamped it) is never overwritten.

## What this deliberately does NOT do yet

- **Per-provider query syntax.** Each source still sends the phrase as one plain-text query parameter to
  that provider's own search endpoint; this doesn't attempt provider-specific operators (exact-phrase
  quoting, boolean AND/OR, field-scoped search) beyond what each adapter already did. Requirement #3 asks
  for "each provider's real documented query behavior" to be used -- auditing and improving every existing
  adapter's query construction against each provider's actual docs is a larger, separate piece of work,
  tracked as a follow-up rather than bundled into this routing change.
- **Controlled broadening / alternative phrasings.** `Keyword.alternatives` exists on the model (a place to
  record other phrasings to try if the first one comes up empty) but nothing yet automatically retries a
  `case`-group search with a broader phrasing when it finds nothing. That's a deliberate deferral, not an
  oversight -- automatic broadening needs its own budget/stopping-condition design (requirement #15) so it
  doesn't quietly blur the case/historical/stock distinction this feature exists to keep sharp.
- **Case reference sheet integration.** `Keyword.entity`/`aliases`/`dates`/`locations` are populated by the
  LLM's structured plan now, but nothing yet cross-checks them against a case reference sheet (requirement
  #6) -- that's a separate, not-yet-built piece.
