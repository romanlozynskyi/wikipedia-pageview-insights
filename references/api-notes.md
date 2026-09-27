# Wikimedia API notes

Findings below were confirmed against the live APIs during development
(2026-09-22), not assumed from documentation alone.

## Endpoints used

- **Wikidata** `https://www.wikidata.org/w/api.php`
  - `action=wbsearchentities&search=<topic>&language=en&format=json` -- fuzzy
    label/alias search, returns candidate QIDs ranked by relevance.
  - `action=wbgetentities&ids=<QID1>|<QID2>&props=sitelinks|descriptions|labels&languages=en&sitefilter=<lang>wiki|...&format=json`
    -- batched entity lookup; `sitelinks.<lang>wiki.title` gives the exact
    article title in that language, if one exists. `languages`/`sitefilter`
    keep the body to the English label/description and the requested
    editions' sitelinks. Measured 2026-09-25: unfiltered, two items
    (Q333, Q3232273) came to 64 KB; filtered, 716 bytes. The difference
    matters when an agent has to relay the body (see below).
  - `action=query&prop=pageprops&ppprop=wb-sitelinks&titles=<QID1>|<QID2>&format=json`
    -- each item's total Wikipedia sitelink count (`"wb-sitelinks": "318"`
    for Q333; `"0"` for an item with no articles, e.g. Q63574657). The
    filtered entities no longer show the total, and `resolve.py` needs it
    for the viability filter and the prominence rule below. It is requested
    alongside the entities, so both arrive in one relay round.
  - A nonexistent QID makes `wbgetentities` answer HTTP 200 with
    `{"error": {"code": "no-such-entity", ...}}`, not an entity. A `--pin`
    to such a QID resolves as `not_found`.
- **MediaWiki action API**, per language: `https://<lang>.wikipedia.org/w/api.php`
  - `action=query&list=search&srsearch=<topic>&format=json` -- full-text
    search fallback.
  - `action=query&titles=<title>&redirects=1&prop=pageprops&format=json`
    -- resolves redirects, and `pageprops.disambiguation` /
    `pageprops.wikibase_item` are used for disambiguation detection and
    cross-checking (see below).
- **Pageviews REST API**
  `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}`
  -- `access=all-access`, `agent=user`, dates as `YYYYMMDD`.

## Project identifier normalization

Confirmed live: `en.wikipedia` and `en.wikipedia.org` return byte-identical
200 results from the pageviews REST API. `util.normalize_project()` always
uses the shorter `en.wikipedia` form and no alternate-form retry is needed.
The three derived identifiers per language code:

| Wikidata sitelink key | MediaWiki API host   | Pageviews `{project}` |
|------------------------|-----------------------|-------------------------|
| `enwiki`               | `en.wikipedia.org`    | `en.wikipedia`          |
| `plwiki`                | `pl.wikipedia.org`    | `pl.wikipedia`          |

## Confirmed quirks and how the code handles them

- **404 does not distinguish "unknown project" from "unknown article".**
  Both return the identical generic `detail` message. `fetch.py` treats
  every 404 as `no_data` and does not attempt to disambiguate the cause --
  there is no signal in the response to do so.
- **Monthly granularity truncates the first and final buckets to the
  requested days.** Confirmed: requesting `.../monthly/20250801/20250901`
  returned `views: 439` for September 2025, while `.../20250930` returned
  the full month's `10860`; likewise `start=20250815` returned `15651` for
  August 2025 versus `27178` from `20250801` -- both under the same
  `2025-08-01` timestamp, so a partial month is indistinguishable from a
  full one in the response. The CLI and `fetch.py` snap `start` back to the
  1st and `end` forward to the last day of their months
  (`util.snap_start_for_granularity()` / `snap_end_for_granularity()`),
  and `params.start`/`params.end` report the snapped period.
- **The current calendar month is still accumulating views.** For monthly
  analysis the CLI ends the period at the last *complete* month and says so
  in `limitations`, rather than analyzing a partial month as if it were
  whole. Any series whose end date is today or later (e.g. a daily range
  ending today) is never written to the cache, so it cannot be served stale
  later.
- **Hyphenated language codes use underscores in Wikidata sitelink keys**
  (`zh-yue` -> `zh_yuewiki`), while the MediaWiki host and pageviews project
  keep the hyphen (`zh-yue.wikipedia`). `util.normalize_project()` handles
  this.
- **Wikidata's fuzzy entity search mixes in non-concept entities.**
  Searching "Intermittent fasting" ranks a real Wikipedia-backed concept
  (Q1666254) above/alongside clinical-trial and journal-article entities
  (e.g. Q63574657) that have zero Wikipedia sitelinks. `resolve.py` filters
  Wikidata candidates to those with at least one sitelink before doing any
  ambiguity/selection logic.
- **A language's full-text search can confidently return a wrong article
  for a topic it doesn't actually have.** Searching "Intermittent fasting"
  on pl.wikipedia's `list=search` top-hits "Stres oksydacyjny" (Oxidative
  stress, Wikidata Q898814) -- a different topic that merely shares
  keywords/context. Confirmed via `pageprops.wikibase_item` on the hit.
  When a topic's Wikidata QID is known (found via search in another
  language) but has no sitelink for the target language, `resolve.py`
  cross-checks the fallback search's top hit against that QID and reports
  `not_found` (true: no dedicated article exists) rather than a false
  `resolved`. Only when the topic has no Wikidata entity at all (nothing
  to cross-check against) is an unverified fallback hit accepted, and it is
  labeled `method: mediawiki_search_unverified` so this lower confidence is
  visible in the JSON.

- **Multiple Wikidata entities sharing the exact search label does not by
  itself mean genuine ambiguity.** Confirmed live via Haiku validation:
  searching "Astronomy" returns the science (Q333, 318 sitelinks) plus an
  "Astronomy" magazine (Q3232273, 9 sitelinks) and a fictional Hogwarts
  class (Q12012641, 2 sitelinks) -- naively flagging all exact-label matches
  as ambiguous forced an unnecessary disambiguation round-trip for an
  overwhelmingly obvious case. `resolve.py`'s `_choose_candidate()` instead
  only treats same-label candidates as genuine rivals when their sitelink
  counts are comparably prominent (within `_PROMINENCE_RATIO = 0.15` of the
  top one). A real collision like "Mercury" (planet 274, element 185, Roman
  god 87 sitelinks, all well above that bar) is still correctly flagged
  ambiguous; the minor astronomy entries are not.

## Restricted environments: the agent relay

Some agent sandboxes block outbound HTTP from Python but still let the
agent read URLs with its own web-fetch or browser tool, or ask the user to.
Every request in `resolve.py` and `fetch.py` goes through
`datasource.WikimediaSource`, which makes that a supported path instead of
a failure:

1. **Direct first** (`--data-source auto`). If the first direct request of a
   run fails, the network counts as blocked and the rest of the run makes
   no further direct attempts. A failure can be a connection error,
   timeout, proxy error, exhausted 429/5xx retries, HTTP 403/407, or a
   non-JSON body such as a proxy block page. If a direct request has
   already succeeded in the run, a later failure is specific to that host
   (e.g. a language edition that does not exist) and is reported as
   before.
2. **Queue, don't guess.** A request that cannot be made directly becomes
   a `data_requests` entry: the exact URL Python would have sent, plus a
   `save_as` path named after a hash of that URL. The CLI returns
   `needs_data` once each topic/language pair has gone as far as it can,
   so independent requests (every topic's search; entities + sitelink
   counts; every resolved series' pageviews) share one round.
3. **Re-run with the saved bodies** (`--data-source relay`, no network).
   A saved body goes through the same parsing and caching as a direct one.
   Checks before use:
   - it must be a JSON object; surrounding tool text is tolerated, but the
     JSON must be complete;
   - it must carry the field the endpoint returns (`search`, `entities`,
     `query`), or an API `error` object, which the direct path would also
     have received;
   - a pageviews body must be the pageviews API's error body
     (`{"status": 404, "title": "Not Found", ...}` means `no_data`, as a
     direct 404 does) or have every item match the requested `project`,
     `article`, `granularity`, `access=all-access`, `agent=user`, with
     unique timestamps inside the requested (snapped) period.

   A body that fails a check is queued again with a `problem`, and is never
   analyzed. As of the fix below, this includes an item-count check: a
   pageviews body with every item individually valid but fewer (or more)
   of them than the requested period requires is also rejected, not
   silently analyzed as a shorter-than-requested series.

Scope: only the exact official URLs are ever requested, so no Wikimedia
account or API key is needed. Article HTML is never scraped, and no other
data source can enter: the CLI reads nothing but files named after the URLs
it asked for. The checks catch the wrong URL, the wrong series, truncation
and reformatting into something that is not the API's JSON. They cannot
detect a body whose view counts were hand-edited; `SKILL.md` forbids
editing the bodies at all. Relay use shows up in the output: each
series' `data_origin` is `relay`, and `limitations` gets a `data access`
entry, which the PDF also prints.

Typical round count: search, then entities + counts, then pageviews
(3 relay rounds). One more pair of rounds is needed only for languages that
fall back to MediaWiki search. A `--pin` with an exact title skips
resolution, leaving a single pageviews round. Saved resolution bodies are
reused on later runs; delete `<outdir>/relay/` to force fresh ones. Body
sizes to expect: search about 5 KB, filtered entities and counts a few KB,
a monthly series (any length) a few KB.

### Chunking large daily requests (must work on a cheap model, not just a strong one)

A relayed body is not read from the wire -- it is whatever the agent's own
web-fetch/browser tool hands back, and that tool's own description warns
it may summarize large content rather than reproduce it verbatim. A daily
series confirmed live at ~146 bytes/item: a 5-month window is already
~22 KB (153 items), and a full year would be 100+ KB. The skill has to run
on a fast, inexpensive model (Haiku 4.5 or comparable) per the case's
model-efficiency requirement, and typical web-fetch tools describe their
own content-processing step as using "a small, fast model" -- so a large
relayed body risks exactly the same summarization behavior the outer
skill is built to avoid, not a hypothetical concern specific to one tool.

`fetch.py` (`_MAX_RELAY_ITEMS = 31`) handles this at the point the request
is built, not by hoping the agent chooses a smaller range: once a run is
known to be relay-bound (`--data-source relay`, or `auto` after its first
direct failure), a daily request longer than one month is split into
calendar-month chunks (`util.month_chunks`) *before* any URL is queued, so
`data_requests` lists several small URLs (a few KB each) instead of one
large one. Every chunk still goes through the same per-item and
item-count checks and is cached individually, then merged in date order
once all chunks are present. A direct HTTP request is never chunked --
Python copies bytes exactly, so there is nothing this protects against
there, and chunking would only cost extra requests. Monthly-granularity
requests are not chunked either: even a 40-month span (the CLI's own
topic x language cap) is only 40 items, comfortably small on its own.

If a chunk's relayed body is rejected or still summarized despite this,
the `problem` field names the exact expected/actual item count, and
`SKILL.md` instructs re-fetching just that one URL -- a few KB, not the
whole series -- rather than treating it as a hard failure.

## Access policy

Wikimedia requires a descriptive `User-Agent` identifying the tool and a
contact method (see `util.USER_AGENT`) and asks clients to keep request
rates reasonable. `util.get_with_retry()` applies bounded exponential
backoff on `429`/`5xx` and the exact-key disk cache (see `fetch.py`)
minimizes repeat requests across runs and follow-ups.

## Data coverage

Pageview data is available from **2015-07-01** onward. Earlier start dates
are clamped by `util.clamp_start_date()` and reported as a limitation.
