# Source error taxonomy

(Requirement #4: "Never silently convert a technical error into 'no results.'")

When a source can't be searched, or a particular query comes back empty, that can mean several very
different things -- and they call for different responses from a person reviewing the job. Earlier, the
pipeline collapsed all of them into one undifferentiated "skipped" or "error" bucket. It now distinguishes
them, both in code (so future logic can act on the difference) and in what's shown in `SOURCES.md` / the
job's decision log.

## The exception types (`pipeline/sources/base.py`)

All of these subclass `SourceUnavailable`, so any existing `except SourceUnavailable` still catches every
one of them -- adding a specific type is purely additive and doesn't require touching every call site.

| Exception | Means | Example |
|---|---|---|
| `CredentialsMissing` | No (or invalid) API key/auth configured. Fixable by the person; the message names the env var. | `FLICKR_API_KEY` not set; a 401/403 response. |
| `RateLimited` | The provider itself said to slow down. Carries `retry_after` (seconds) when the provider gave one via the `Retry-After` header. | HTTP 429. |
| `ProviderError` | A network failure or an unexpected HTTP status -- a technical failure of the provider or the connection to it. | Timeout, connection reset, HTTP 500. |
| `ResponseParseError` | The provider answered (HTTP 200) but its response didn't match the shape this adapter expects. | The provider changed its API; the adapter's field-name assumptions were wrong; invalid JSON. |
| `SourceUnavailable` (bare) | Something else stopped this source from running, not covered above. | Rare -- prefer a specific subclass when the reason is known. |

None of these means "searched and legitimately found nothing." That case doesn't raise at all -- see below.

## Per-query outcomes (`HttpSource.fetch()`'s trace notes)

A source's `search()` can succeed (no exception) and still not add anything to the project. `fetch()` records
which of these happened on every query's trace note (`SOURCES.md`'s "What was searched" table, and each
entry's `outcome` field in the job's `source_notes`):

- **`ok_kept`** -- found candidates and kept at least one.
- **`ok_zero_matches`** -- the search itself found nothing. A genuine zero-result search.
- **`ok_no_downloadable`** -- the search found candidates, but none could be kept (unsupported file format,
  every candidate already in the project, download failures, duplicate file hashes). Different from a real
  zero-match search: there was material there, just nothing usable this round.
- **`parse_error`** -- `search()` raised `KeyError`/`TypeError`/`AttributeError`/`ValueError`, which usually
  means the provider's response didn't match what the adapter expects (a changed field name, an unexpected
  null, a reshaped list) rather than a real "nothing found."
- **`unknown_error`** -- `search()` raised some other exception. Investigate rather than assume "no results."

(A `SourceUnavailable` raised from `search()` is not a per-query outcome -- it aborts the whole source, see
below, since it usually means every query against that source would fail the same way.)

## Per-source outcomes (`SourcingStage.run()`'s trace notes)

When a source's `fetch()` raises, `sourcing.py` records which kind of `SourceUnavailable` it was as the
source-level trace note's `outcome`:

`credentials_missing` / `rate_limited` (+ `retry_after` when known) / `network_error` / `parse_error` /
`skipped_other` (a bare `SourceUnavailable`) / `unknown_error` (anything not a `SourceUnavailable` at all --
still caught so one bad source can't sink the whole sourcing round, but worth a closer look).

## Retry-After handling

`LoggedHttp._send()` parses a `Retry-After` response header (`_parse_retry_after()`, handling both the
integer-seconds and the HTTP-date forms) and, when present, sleeps that long before its own internal retry
instead of guessing with exponential backoff. The same parsed value is attached to a `RateLimited` exception
so a caller further up (a future "search again in N seconds" UI, today just the trace note) can see the
provider's own number rather than a generic "it failed."

## Writing a new source

See `docs/ADD_A_SOURCE.md`. In short: `ctx.http.get_json()`/`ctx.http.download()` already raise the right
type for you for any HTTP-level failure. Raise one yourself only for something your own code detects before
the request goes out -- almost always `CredentialsMissing` for "no key configured."
