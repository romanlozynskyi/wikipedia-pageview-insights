# Analysis methodology

This documents exactly what `scripts/analyze.py` computes, so every label in
the JSON output can be independently checked against the raw numbers. These
are transparent, documented heuristics chosen for a v1 that must stay
verifiable by a small model and a human reader -- not a peer-reviewed
statistical model.

## Metrics (measured facts, no judgment calls)

For a pageview series with points `(t_0, v_0) ... (t_n, v_n)`:

- `total_views` = sum of `v_i`
- `average_views` = `total_views / n`
- `first_half_avg` / `second_half_avg`: the series split at its midpoint by
  index; average views in each half
- `growth_pct` = `(second_half_avg - first_half_avg) / first_half_avg * 100`
  (undefined / `null` if `first_half_avg == 0`)
- `slope`, `r_squared`: ordinary least-squares fit of `views` against point
  index (not calendar time, to keep monthly/daily series comparable), via
  `numpy.polyfit(x, v, 1)` for the slope and `numpy.corrcoef(x, v)[0,1]**2`
  for R². `slope` is expressed as "views per period" (period = one row of
  the series, i.e. one day or one month depending on granularity).
- `data_points` = `n`
- `zero_periods` = count of periods with `views == 0`

v1 does not detect spikes/outliers: a single unusual month (e.g. a news
event) can move `growth_pct`. Check `sample_points` and the chart for
visibly isolated peaks before leaning on a growth figure.

## `trend_strength` (a heuristic evidence-strength label, *not* a
## statistical significance test)

`trend_strength` is one of `strong`, `moderate`, `weak`, `insufficient`,
derived only from `data_points` and `r_squared`:

| data_points | r_squared        | trend_strength |
|-------------|-------------------|----------------|
| < 6         | any               | insufficient   |
| >= 6        | < 0.15            | weak           |
| >= 6        | 0.15 -- 0.5       | moderate       |
| >= 6        | > 0.5             | strong         |

Additionally, if `zero_periods / data_points > 0.3` (more than 30% of
periods have zero recorded views), `trend_strength` is forced down to
`weak` regardless of R² -- sparse data does not support a strong claim
even if the few non-zero points happen to fit a line well.

**Why this is not "confidence":** R² measures how well the observed points
fit a straight line, not the probability that a real trend exists. Pageview
counts are autocorrelated (today's count is not independent of yesterday's)
and the series can be short (a handful of monthly points), so a proper
significance test (e.g. Mann-Kendall, or a regression with
autocorrelation-robust standard errors) is not attempted in v1 -- see
`references/roadmap.md` for how that could be added. `trend_strength` is best read as
"how clean is the evidence for this shape", not "how sure are we this is
real."

## `assessment.promising` classification

Computed against user-supplied criteria (`--criteria key=value,...`) or
these defaults if omitted:

- `min_growth_pct` = 15
- `min_trend_strength` = `moderate`

A series is classified `promising` when `growth_pct >= min_growth_pct` AND
`trend_strength` is at least `min_trend_strength` on the
`insufficient < weak < moderate < strong` ordering. Otherwise
`not_promising`. If `growth_pct` is `null` (no data) or `trend_strength` is
`insufficient`, the classification is `insufficient_data`, never
`not_promising` -- absence of evidence is reported as absence of evidence,
not as evidence of decline.

The exact criteria applied are always echoed back in the JSON's
`criteria_applied` field.
