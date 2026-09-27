# Worked examples

Three transcripts matching the example requests in the case, plus one
regression case (example 4). Each shows the exact command to run and how
to turn its JSON into an answer. Use these as a template for phrasing; the
underlying numbers always come from the JSON, never from memory of these
examples. Every answer has the same three labeled parts in the same order:
measured data, limitations, then interpretation (not measured).

## 1. "Compare the growth of interest in intermittent fasting in Polish and Czech Wikipedia over the last two years."

```
python scripts/pageviews_cli.py --topics "Intermittent fasting" --langs pl,cs --outdir wpv-output
```

(No `--start`/`--end` given -> defaults to the last 24 complete months, which is
exactly what was asked; state that default explicitly in the answer.)

Say the JSON comes back with `pl` as `not_found` (no dedicated Polish
article for this topic -- a real, correctly-reported outcome, not a bug)
and `cs` as `resolved`/`ok` with `growth_pct: -26.5`,
`trend_strength: "weak"` and `promising: "not_promising"`. A good answer:

> **Measured data.** Czech Wikipedia ("Přerušovaný půst"): average monthly
> pageviews in the second half of the last 24 months were 26.5% lower than
> in the first half; trend strength: weak; assessment: not_promising.
>
> **Limitations.** Period: the default last 24 complete months. Polish
> Wikipedia has no dedicated article on this topic, so no Polish
> comparison is possible -- a real data gap, not a missing data point.
> "Weak" means the points fit a trend line poorly, so the direction is a
> rough signal. Pageviews measure reading interest, not purchase intent.
>
> **Interpretation (not measured).** Czech interest appears to be falling
> rather than growing, but the weak fit means I wouldn't rule out noise.
> Nothing here supports Czech as a growth audience for this topic.

Then attach `wpv-output/chart.png` and `wpv-output/report.pdf` as downloadable
files using your environment's file-attach/send-file tool -- don't just
write their paths into the chat text as shown above for illustration; a
path string isn't something the user can click or download.

## 2. "We are considering adding an astronomy course to an educational app. Is interest in this topic growing in Ukrainian Wikipedia, and how much confidence should we have in that conclusion?"

```
python scripts/pageviews_cli.py --topics "Astronomy" --langs uk --outdir wpv-output
```

If the JSON comes back with `growth_pct: 22.1`, `r_squared: 0.61`,
`data_points: 24`, `trend_strength: "strong"` and
`promising: "promising"` (default criteria: at least 15% growth and at
least moderate trend strength):

> **Measured data.** "Астрономія" on Ukrainian Wikipedia: average monthly
> pageviews in the second half of the last 24 months were 22.1% higher
> than in the first half; 24 monthly points, R² 0.61, trend strength:
> strong; assessment: promising under the default criteria (≥15% growth,
> at least moderate trend strength).
>
> **Limitations.** Period: the default last 24 complete months. "Strong"
> describes how cleanly the points fit a line; it is not a statistical
> significance test or a confidence level. Pageviews measure reading
> interest, not enrollment intent or willingness to pay for a course.
>
> **Interpretation (not measured).** On this data, the course idea is
> worth investigating further: interest grew, the fit is strong, and it
> clears the default bar. How much confidence to have: the growth signal
> itself is as clean as this skill measures, but it says nothing about
> whether people would pay for a course, so I'd check that with
> prospective users before committing.

## 3. "We are building a language-learning app. Compare interest in learning English across selected Wikipedia language editions and prepare a short report explaining which audiences should be researched next and why."

First clarify (one short question) which language editions to compare if
the user hasn't said -- e.g. "Which language editions should I compare --
for example pl, de, ja, pt?" Once given:

```
python scripts/pageviews_cli.py --topics "English language" --langs pl,de,ja,pt --intent "learning English" --outdir wpv-output
```

`--intent` makes the CLI add a limitation that "English language"
pageviews are only a proxy for people learning English -- relay it with
the other limitations. With four languages, the response has a
`comparison` field -- read `comparison.by_average_views` and
`comparison.by_growth_pct` directly for "largest audience" / "fastest
declining" claims; do not sort or compare the four `metrics` rows
yourself (see the regression case below for what goes wrong when a model
does that). Also read `results.<topic>.<lang>.promising` for each
language, judged against `params.criteria_applied`. The PDF at
`wpv-output/report.pdf` lists every series' measured data (in the order
requested, not ranked) and is shareable as-is; attach it (and the chart)
as downloadable files rather than re-typing its table in chat or just
naming its path.

## 4. Regression case: the largest audience is declining and nothing is promising

"We're building an intermittent-fasting app. Compare interest across
English, German, Czech and Ukrainian Wikipedia and tell us which audience
to research next."

```
python scripts/pageviews_cli.py --topics "Intermittent fasting" --langs en,de,cs,uk --start 2023-09-01 --end 2025-09-01 --outdir wpv-output
```

Real output (live run, 2026-09-25; `params.end` is `2025-09-30`
because monthly periods are whole months; default criteria: at least 15%
growth and at least moderate trend strength):

| lang | `average_views` | `growth_pct` | `r_squared` | `trend_strength` | `promising` |
|------|----------------:|-------------:|------------:|------------------|-----------------|
| en   | 34213.28        | -62.82       | 0.4759      | moderate         | not_promising   |
| de   | 3277.6          | -32.3        | 0.3436      | moderate         | not_promising   |
| cs   | 452.32          | -26.54       | 0.0998      | weak             | not_promising   |
| uk   | 579.64          | 50.77        | 0.0416      | weak             | not_promising   |

The case has two traps. The biggest audience (en) is the one declining
most steeply. The only rising series (uk) has a weak fit and fails the
criteria too.

**Wrong answer (do not do this):**

> English is clearly your best market: it accounts for 88% of all views
> and shows strong, sustained interest in fasting. Ukrainian is the
> fastest-growing and most promising audience (+50.8%), likely driven by
> rising health awareness. Recommendation: launch in English and expand to
> Ukrainian.

What is wrong with it:

- "88% of all views" is a number the agent computed; it is not in the JSON.
- "Strong, sustained interest" contradicts en's `growth_pct` of -62.82;
  size was passed off as momentum.
- "Most promising" contradicts uk's `promising: not_promising`, and
  "fastest-growing" is stated without the `weak` trend strength.
- "Rising health awareness" is a cause from outside the data, stated as
  fact.
- The recommendation is presented as a finding. There is no
  limitations section before it, and no "not measured" label.

**Right answer:**

> **Measured data** (Sept 2023 – Sept 2025, monthly; growth = average of
> the second half of the period vs. the first half):
> - English: average 34,213 views/month; growth -62.8%; trend strength:
>   moderate; assessment: not_promising.
> - German: average 3,278 views/month; growth -32.3%; moderate;
>   not_promising.
> - Czech: average 452 views/month; growth -26.5%; weak; not_promising.
> - Ukrainian: average 580 views/month; growth +50.8%; weak (R² 0.0416);
>   not_promising.
>
> **Limitations.** Period as requested (whole months, through Sept 2025).
> "Weak" means the points barely fit a trend line, so Czech's and
> Ukrainian's directions are rough signals at best. Trend strength is a
> fit-quality label, not a significance test. Pageviews measure reading
> interest, not willingness to pay for an app.
>
> **Interpretation (not measured).** None of the four audiences meets the
> bar of at least 15% growth with at least moderate trend strength. All
> four are assessed not_promising, so this data gives no audience a
> growth case. English has by far the largest readership, but it also has
> the steepest decline (-62.8%, moderate) and is not_promising. If you
> research English next, it should be because of its size, knowing that
> interest in the topic fell over this period. Ukrainian is the only
> series that rose (+50.8%), but on a weak fit it is a lead to re-check
> later or over a longer period, not evidence of growth.

Checks the right answer passes:

- Every number appears in the JSON.
- Every direction word matches the sign of `growth_pct`, with its
  `trend_strength` next to it.
- "Promising" is used only as the JSON uses it.
- The bottom line admits that nothing met the bar.
- Every recommendation sits under the "not measured" label and names the
  fields it rests on.

`tests/test_cli.py::test_regression_example_largest_audience_declining_nothing_promising`
replays this run's real series through the CLI. It fails if the metrics or
assessments stop matching this table, or if this example stops quoting
them.

## 5. Regression case: getting the ranking backwards

"Compare interest in learning English across Polish, German, Spanish, and
Japanese Wikipedia over the last 24 months, and create a short report with
a chart and recommendation on which audiences to research next."

Real `results.*.metrics` from a live run (topic "English language",
2024-09 to 2026-08, monthly):

| lang | `average_views` | `growth_pct` | `trend_strength` |
|------|----------------:|-------------:|-------------------|
| pl   | 8,991           | -18.3        | strong            |
| de   | 24,496          | -8.0         | moderate          |
| es   | 31,296          | -29.1        | strong            |
| ja   | 39,560          | -14.9        | strong            |

**Wrong answer (do not do this -- this happened live):**

> Spanish has the largest audience but shows the steepest decline (29.1%,
> strong fit), while Japanese has the second-largest audience with a more
> moderate but still clear decline (14.9%, strong fit) [...] Spanish has
> shown the most readers on Wikipedia (31k/month average) despite the
> steepest decline. Japanese is close behind (39.6k/month) with a more
> moderate decline. The fact that all four language editions are declining
> simultaneously suggests a structural shift, not localized weakness.

What is wrong with it: ja's 39,560 is the largest number in the table, not
es's 31,296 -- the model compared four rows by eye and got the order
backwards, then called the actual largest number "close behind" the
smaller one it had just called biggest (39.6k cannot be behind 31k).
"Suggests a structural shift, not localized weakness" is a cause for the
decline that is not in the JSON, offered as an inference rather than
flagged as unfounded.

**Right answer:** read `comparison.by_average_views` directly instead of
comparing `metrics` rows -- for this data it is `[ja (39560, rank 1), es
(31296, rank 2), de (24496, rank 3), pl (8991, rank 4)]`. "Japanese has
the largest audience (39,560 views/month), followed by Spanish (31,296),
German (24,496), then Polish (8,991). All four are declining (-8.0% to
-29.1%) and all are `not_promising` against the default criteria. The
data doesn't say why interest is falling in all four editions at once --
that would need research outside Wikipedia pageviews."

`tests/test_analyze.py::test_rank_series_matches_the_observed_pl_de_es_ja_regression`
and `tests/test_cli.py::test_multi_language_run_produces_a_correctly_ordered_comparison`
lock in that `comparison.by_average_views` returns this exact order for
this exact data.

## 6. Regression case: inventing what isn't in the data

Same request and data as example 5 (pl/de/es/ja "English language",
average views 8,991 / 24,496 / 31,296 / 39,560; growth -18.3% / -8.0% /
-29.1% / -14.9%).

**Wrong answer (do not do this -- this also happened live, in the same
response as example 5's ranking error):**

> Japanese audience is stable and large. Highest average views (39.6k/
> month), flattest decline (-14.87%). More consistent reader base suggests
> sustained interest [...] The decline could reflect: Migration to other
> resources (TikTok, YouTube, AI tutors, specialized SaaS); Seasonal or
> cyclical patterns (schools back in session) [...] Priority 1:
> Spanish-speaking audience — Interview learners in Mexico, Spain,
> Colombia, Argentina. Validate whether they moved to competitor apps
> (Duolingo, Babbel, ChatGPT) [...] Priority 2: Japanese-speaking audience
> — Research Japan, Taiwan [...] Cultural factors (entrance exams,
> business English, anime/tech interest).

What is wrong with it:

- "Flattest decline" for ja (-14.9%) is false even on the model's own
  numbers: de's -8.0% is closer to zero. This is example 5's ranking bug
  again, on a different field (`by_growth_pct` rank 1, not
  `by_average_views` rank 1) -- ranking mistakes are not limited to
  "largest audience."
- "Stable", "consistent reader base", "sustained interest" describe a
  series with `growth_pct: -14.9` and `trend_strength: strong` -- strong
  here means a *clean decline*, not stability. None of these words are
  supported by any field in the JSON.
- "TikTok, YouTube, AI tutors, specialized SaaS", "seasonal patterns",
  "cultural factors (entrance exams...)" are causes for the decline
  invented wholesale. The JSON does not measure why pageviews changed.
- "Mexico, Spain, Colombia, Argentina", "Japan, Taiwan" are countries.
  The data is about Wikipedia *language editions* (es, ja), which are not
  countries and do not imply where readers are.
- "Duolingo, Babbel, ChatGPT" are named competitors nowhere in the data.
- The multi-bullet "Priority 1 / Priority 2" research plan with interview
  targets is a fabricated market-research agenda, not a reading of four
  numbers.

**Right answer:** build the interpretation from `narrative.per_series` and
`narrative.summary` directly -- for this data that is four sentences each
naming a language, its `growth_pct`, its `trend_strength`, and
`not_promising`, plus "0 of 4 series meet the promising criteria
(min_growth_pct=15.0, min_trend_strength=moderate)." A recommendation may
add "ja has the largest audience (comparison rank 1) despite declining"
or "de has the flattest decline (comparison rank 1 in `by_growth_pct`)"
-- both traceable to a specific field -- but stops there. If the user
wants to know *why*, or wants named markets or competitors, say the data
doesn't cover that rather than filling the gap.

`tests/test_analyze.py::test_build_narrative_matches_the_observed_pl_de_es_ja_regression`
and `test_build_narrative_never_calls_a_decline_stable_or_omits_direction`
lock in that the generated sentences state the real numbers and never
contain words like "stable" for a decline.

## Follow-up / revised-assumption pattern

For "now also check German" or "what if we need 30% growth to call it
promising", read `wpv-output/latest.json` to recover the prior `--topics`/
`--langs`/`--pin` values, then call the CLI again with only the changed
flag(s) added or updated (e.g. `--langs pl,cs,de` or
`--criteria min_growth_pct=30`). Do not restart from a blank set of
parameters, and do not recompute anything by hand -- the exact-key cache
makes the unchanged parts of the request cheap automatically.
