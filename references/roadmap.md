# Iterative extension roadmap

The v1 implementation in this repo deliberately stays focused: one CLI
command, exact-key caching, linear-trend analysis with a heuristic
evidence-strength label, one chart, one self-contained one-page PDF, and
all-or-nothing disambiguation. Everything below is a documented direction
for extending it, not something already built -- each item names the
concrete file(s) it would touch so the next iteration doesn't have to
re-derive the architecture.

## More complex research questions

- **Seasonality-adjusted trend**: pageviews for many topics have a weekly
  (weekday vs. weekend) or annual (school-year, holiday) rhythm that a
  plain linear fit can't separate from real growth. An STL decomposition
  (or even a simple 7-day/12-month moving-average detrend) in `analyze.py`
  would let `trend_strength` describe the deseasonalized signal.
- **Cross-topic correlation**: given several topics already fetched in one
  run, compute pairwise correlation of their series (e.g. do "keto diet"
  and "intermittent fasting" move together?) -- useful for founders
  validating whether a cluster of related topics is one audience or several.
- **Spike detection and event annotation**: v1 has no spike detection (a
  raw-level median+MAD rule was tried and removed because it flagged whole
  stretches of trending series). A detector run on detrended residuals
  could flag genuine one-off spikes, then cross-reference those dates
  against Wikipedia's "Current events portal" or a news API to label *why*
  a spike happened.

## Larger data volumes

- **Interval-aware caching**: v1's cache (`fetch.py`) is exact-key only --
  a cache hit requires the identical `(project, article, granularity,
  start, end)` tuple as a prior fetch, so extending a date range re-fetches
  the whole new range rather than just the missing days/months (see the
  `fetch.py` module docstring). Storing
  per-period rows (e.g. one row per day/month per article) instead of one
  blob per request would let an extended range fetch only what's missing.
- **Bulk pageview dumps**: for category-scale scans (dozens+ of articles),
  Wikimedia's per-article REST endpoint means one HTTP call per article;
  switching to the pageview dump files for wide scans would cut request
  count dramatically at the cost of more local processing.
- **A real datastore**: the flat-file JSON cache under `<outdir>/raw-cache/`
  is adequate for a handful of series per run; at real scale (hundreds of
  topics/languages tracked over time) this would move to SQLite or DuckDB,
  which also unlocks ad hoc querying across historical runs.

## Broader comparisons

- **Category/related-topic expansion**: today the user supplies exact
  topics via `--topics`. A "find related topics" step using Wikidata's
  category/"instance of" links or Wikipedia's own "What links here" could
  let a founder ask "what else is like intermittent fasting?" and get a
  candidate topic list to compare, instead of naming every topic by hand.
- **Small-multiples charts beyond the v1 cap**: `charts.py` caps a single
  chart at `MAX_SERIES_PER_CHART` (8) lines for readability, omitting the
  rest with a reported limitation (see `references/api-notes.md`). A grid
  of small per-series charts (small multiples) would let a single report
  cover many more topics/languages at once without an unreadable single plot.

## More advanced analysis

- **A genuine trend-significance test**: `trend_strength` (see
  `references/methodology.md`) is an R²/data-span heuristic, explicitly
  *not* a statistical significance result, because pageview counts are
  autocorrelated and series are often short. A Mann-Kendall trend test, or
  a regression with autocorrelation-robust (Newey-West) standard errors,
  would let the skill make an actual significance claim where the data
  supports one, instead of only a fit-quality label.
- **Forecasting**: simple exponential smoothing or a seasonal-ARIMA model
  on top of the existing series could extend "is this growing" into "how
  much interest should we expect next quarter."
- **User-defined "promising" criteria beyond growth % and trend strength**:
  `analyze.py`'s `parse_criteria()` currently accepts `min_growth_pct` and
  `min_trend_strength`. Extending the criteria language (e.g. minimum
  absolute audience size, minimum data completeness) is a small, additive
  change to that one function and `references/json-schema.md`'s
  `criteria_applied` field.

None of the above changes the core division of labor: code computes and
verifies, the model narrates within the guardrails the JSON provides. Any
extension should keep that boundary rather than pushing more computation
back into the LLM.
