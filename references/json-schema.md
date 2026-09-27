# CLI output JSON schema

`python scripts/pageviews_cli.py ...` always prints exactly one JSON
document to stdout. Every shape except `error` is also written to
`<outdir>/latest.json`. There are four shapes, distinguished by `status`.

## `status: "error"`

Request rejected before any pageview data was fetched (bad dates, invalid
language code, bad or unmatched `--pin`, unknown criteria key, oversized
request, or Wikidata/Wikipedia failing during topic resolution after
direct access had already worked in that run, e.g. a nonexistent
language edition).

## `status: "needs_data"`

Python could not reach the Wikimedia APIs (or the run used
`--data-source relay`), so some official API responses must be fetched by
the agent. **Nothing was analyzed or rendered**, so there is no `results` or
`artifacts` key. Topic resolution is carried as far as the supplied
responses allow. Pairs still waiting are `"status": "pending"`.

```json
{
  "status": "needs_data",
  "reason": "Python could not reach the Wikimedia APIs directly (ProxyError: ...).",
  "params": {"topics": [...], "langs": [...], "start": "...", "end": "...", "granularity": "...", "intent": null},
  "data_requests": [
    {
      "url": "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/cs.wikipedia/all-access/user/P%C5%99eru%C5%A1ovan%C3%BD_p%C5%AFst/monthly/20230901/20250930",
      "save_as": "/abs/path/wpv-output/relay/6d60a91c05f5642c6ccd7200.json",
      "purpose": "pageviews of 'Přerušovaný půst' on cs.wikipedia, monthly, 2023-09-01 to 2025-09-30",
      "problem": null
    }
  ],
  "next_step": "Fetch each of the 1 data_requests[].url ... then run rerun_command. ...",
  "rerun_command": "python /abs/path/scripts/pageviews_cli.py --topics ... --data-source relay",
  "resolution": {"<topic>": {"<lang>": {"status": "resolved|ambiguous|not_found|pending", ...}}},
  "limitations": ["run halted before any analysis: the requested official Wikimedia responses have not been supplied yet"]
}
```

- `data_requests[].url`: always an official Wikidata, Wikipedia or
  Wikimedia pageviews API URL, and the exact URL Python would have
  requested itself.
- `data_requests[].save_as`: the only place the CLI reads that response
  from. The file name is a hash of `url`.
- `data_requests[].problem`: `null`, or why a previously saved body was
  rejected (not JSON, not this URL's response, or a pageview body whose
  items do not match the requested article/project/granularity/
  `all-access`/`user`/period).

```json
{"status": "error", "detail": "start date 2024-06-01 is after end date 2024-01-01"}
```

## `status: "needs_disambiguation"`

At least one topic/language pair was ambiguous. **Nothing was fetched,
analyzed, or rendered** -- there is no `results` or `artifacts` key in this
shape. Re-invoke with `--pin "<topic>:<lang>=<QID-or-exact-title>"` for each
entry under `needs_disambiguation`.

```json
{
  "status": "needs_disambiguation",
  "params": {"topics": [...], "langs": [...], "start": "...", "end": "...", "granularity": "..."},
  "resolution": { "<topic>": { "<lang>": {"status": "resolved|ambiguous|not_found", ...} } },
  "needs_disambiguation": {
    "<topic>": {
      "<lang>": {
        "status": "ambiguous",
        "candidates": [{"qid": "Q308", "label": "Mercury", "description": "first planet..."}]
      }
    }
  },
  "limitations": ["run halted before fetching any pageview data: ..."]
}
```

## `status: "ok"`

```json
{
  "status": "ok",
  "params": {
    "topics": ["Intermittent fasting"],
    "langs": ["en", "cs"],
    "start": "2023-09-01",
    "end": "2025-09-30",
    "granularity": "monthly",
    "criteria_applied": {"min_growth_pct": 15.0, "min_trend_strength": "moderate"},
    "intent": null
  },
  "resolution": {
    "Intermittent fasting": {
      "en": {"status": "resolved", "title": "Intermittent fasting", "qid": "Q1666254", "method": "wikidata_sitelink"},
      "cs": {"status": "resolved", "title": "Přerušovaný půst", "qid": "Q1666254", "method": "wikidata_sitelink"}
    }
  },
  "results": {
    "Intermittent fasting": {
      "en": {
        "fetch_status": "ok",
        "metrics": {
          "data_points": 25, "total_views": 855332, "average_views": 34213.28,
          "first_half_avg": 50810.42, "second_half_avg": 18892.85,
          "growth_pct": -62.82, "slope": -1902.25, "r_squared": 0.4759,
          "zero_periods": 0
        },
        "trend_strength": "moderate",
        "promising": "not_promising",
        "sample_points": [
          {"date": "2023-09-01", "views": 25839}, {"date": "2023-10-01", "views": 44989},
          {"date": "2023-11-01", "views": 29530}, "... (middle points omitted) ...",
          {"date": "2025-07-01", "views": 14814}, {"date": "2025-08-01", "views": 12958},
          {"date": "2025-09-01", "views": 10860}
        ],
        "data_origin": "direct"
      }
    }
  },
  "limitations": ["'X' (de): no Wikipedia article could be found -- excluded from analysis"],
  "artifacts": {"chart": "wpv-output/chart.png", "report": "wpv-output/report.pdf"},
  "comparison": {
    "by_average_views": [
      {"topic": "English language", "lang": "ja", "average_views": 39560.4, "rank": 1},
      {"topic": "English language", "lang": "es", "average_views": 31296.2, "rank": 2}
    ],
    "by_growth_pct": [
      {"topic": "English language", "lang": "de", "growth_pct": -8.0, "rank": 1},
      {"topic": "English language", "lang": "es", "growth_pct": -29.1, "rank": 2}
    ]
  },
  "narrative": {
    "per_series": [
      "'English language' on ja.wikipedia declined by 14.9% (average views in the second half of the selected period vs. the first half; trend strength: strong); classification: not_promising.",
      "'English language' on es.wikipedia declined by 29.1% (average views in the second half of the selected period vs. the first half; trend strength: strong); classification: not_promising."
    ],
    "summary": "0 of 2 series meet the promising criteria (min_growth_pct=15.0, min_trend_strength=moderate)."
  }
}
```

(Live-verified example, run 2026-09-22: `--topics "Intermittent fasting" --langs en,cs --start 2023-09-01 --end 2025-09-01`.
Every number above comes directly from that run's actual output -- not hand-constructed.)

### Field notes

- `resolution.<topic>.<lang>.status`: `resolved` | `ambiguous` | `not_found`
  (plus `pending`, only ever in a `needs_data` response).
- `resolution.<topic>.<lang>.method` (only when resolved): `wikidata_sitelink`
  (cross-language-correct, highest trust) | `mediawiki_search_verified`
  (fallback search, cross-checked against a known Wikidata QID) |
  `mediawiki_search_unverified` (fallback search, no QID to check against --
  lower trust, treat cautiously) | `pinned_qid` | `pinned_title`.
- `results.<topic>.<lang>.fetch_status`: `ok` | `no_data` | `error`. Only
  `ok` entries have `metrics`/`trend_strength`/`promising`.
- `results.<topic>.<lang>.data_origin` (on `ok` and `no_data`): `direct`
  (Python fetched the official API) or `relay` (the agent fetched the same
  official URL and the CLI validated the saved body). Either way, the
  numbers are the Wikimedia pageviews API's. A series served from the cache
  keeps the origin it was fetched with. If any series or resolution step
  came through the relay, `limitations` includes a `data access: ...`
  entry that says so.
- `metrics.growth_pct`: `null` when undefined (e.g. zero views in the first
  half of the series) -- never coerced to 0.
- `trend_strength`: `strong` | `moderate` | `weak` | `insufficient` -- a
  heuristic evidence/fit-quality label, **not a statistical significance
  result**. See `references/methodology.md`.
- `promising`: `promising` | `not_promising` | `insufficient_data`, per the
  criteria in `params.criteria_applied`.
- `sample_points`: first 3 and last 3 raw `{date, views}` points of the
  fetched series -- enough to spot-check `growth_pct`/`slope` by hand
  without re-fetching.
- `params.intent`: the `--intent` value, or `null`. When set, `limitations`
  includes a "proxy measure" entry for each topic that differs from it.
- `limitations`: always present (possibly empty); plain-language, meant to
  be read/paraphrased directly to the user before any recommendation.
- `artifacts.chart` / `artifacts.report`: local file paths, only present
  on `status: "ok"`. These are paths on disk for your tools to read --
  attach both files to the user as downloadable artifacts (see
  `SKILL.md` step 8); a path is not itself a deliverable.
- `comparison` (only present with two or more successfully-fetched series):
  `by_average_views` and `by_growth_pct`, each every `ok` series sorted
  descending (`rank: 1` = largest/highest -- for `by_growth_pct` that means
  least negative, i.e. the flattest decline among decliners), computed
  once in code so nothing reading this JSON has to sort or compare
  `metrics` rows itself. A series whose `growth_pct` is `null` appears in
  `by_growth_pct` last, with `rank: null`, rather than being dropped.
  Absent when fewer than two series succeeded -- there is nothing to rank.
- `narrative` (present whenever at least one series succeeded):
  `per_series` (one fully factual, template-generated sentence per series,
  the same text `report.py` builds the PDF from) and `summary` (how many
  series meet `params.criteria_applied`, and which ones). This is the
  intended basis for the chat-facing "Interpretation" section -- see
  `SKILL.md` step 7. Nothing in it is an inference; relaying or lightly
  rephrasing these sentences cannot introduce a wrong ranking, an invented
  cause, place, or company name, because none of those are in the
  sentences to begin with.
