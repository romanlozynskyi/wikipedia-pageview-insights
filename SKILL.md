---
name: wikipedia-pageview-insights
description: "Use when a user wants to understand or compare public interest in a topic using Wikipedia pageview data -- across time periods, across Wikipedia language editions, or both. Triggers include: Wikipedia pageviews/traffic, interest over time, trend comparison across languages, market/audience research for a product idea using Wikipedia data, 'is interest in X growing', 'compare interest in X across languages', or requests for a one-page report/chart on Wikipedia topic interest. Not for general Wikipedia content questions unrelated to pageview trends."
---

# Wikipedia Pageview Insights

Helps founders read Wikipedia pageview data as a *proxy* for public interest
in a topic -- across time and across language editions -- to decide which
topics or language audiences are worth investigating further. Pageviews are
never evidence of willingness to pay; this skill's job is to surface a
measured, verifiable signal plus its limitations, not to make that leap for
the user.

All data fetching, cross-language title resolution, trend math, chart
rendering, and PDF layout are implemented in `scripts/` and run through a
**single CLI command**. Never hand-compute growth rates, trends, or
aggregates -- always call the CLI and read its JSON.

## Setup (once per environment)

Requires Python 3.11-3.14 (the pinned numpy/matplotlib ship prebuilt
wheels for these; tested clean on 3.12 and 3.14).

```
pip install -r requirements.txt
```

Chinese/Japanese/Korean topic names are drawn with an installed system
CJK font (Windows and macOS ship one; on Linux e.g. `fonts-wqy-zenhei`).
Without one, the run still succeeds and `limitations` says which
characters the chart/PDF cannot display.

(`requirements-dev.txt` additionally installs `pytest`/`responses`/`pypdf`
for running the test suite -- not needed just to use the skill.)

## The command

```
python scripts/pageviews_cli.py \
  --topics "<topic1>[,<topic2>,...]" \
  --langs <lang1>[,<lang2>,...] \
  [--start YYYY-MM-DD] [--end YYYY-MM-DD] \
  [--granularity auto|daily|monthly] \
  [--criteria "min_growth_pct=20,min_trend_strength=strong"] \
  [--pin "<topic>:<lang>=<QID-or-exact-title>"] \
  [--intent "<the user's behavioral question, if not the topic itself>"] \
  [--notes "<short text appended to the PDF as-is>"] \
  [--outdir wpv-output] \
  [--data-source auto|direct|relay]
```

- `--langs` takes Wikipedia language codes (`en`, `pl`, `cs`, `uk`, `de`, ...).
- Omit `--start`/`--end` for the last 24 complete calendar months (state
  that default when you answer). Monthly periods always cover whole months
  and never include the in-progress month; `params.start`/`params.end` in
  the JSON are the period actually analyzed -- quote those.
- `--criteria` defaults to `min_growth_pct=15,min_trend_strength=moderate`
  if omitted -- always readable back from the JSON's `params.criteria_applied`.
- `--pin` is how you resolve an ambiguous topic (see below) or force an
  exact article once you know it.
- `--intent`: when the user asks about a behavior (learning, buying,
  using, doing X) and `--topics` is the article that stands in for it
  (e.g. `--topics "English language" --intent "learning English"`), pass
  their phrase here. The CLI then adds the proxy-measure caveat to
  `limitations` and the PDF automatically.
- Everything is written to `--outdir` (default `wpv-output/`): the raw-data
  cache, `chart.png`, `report.pdf`, and `latest.json` (the full result of
  the most recent run, used for follow-ups).
- `--data-source`: leave it at `auto`. Python fetches the official
  Wikimedia APIs itself. If your environment blocks that, the CLI returns
  `needs_data` (step 5) and asks you to fetch those same official URLs with
  your own tools. `relay` is set for you by `needs_data`'s `rerun_command`.

Full JSON shape: `references/json-schema.md`. Exact endpoints and confirmed
API quirks the code works around: `references/api-notes.md`. Trend-strength
formulas and thresholds: `references/methodology.md`. Worked transcripts
for typical requests, plus a regression case where the largest audience
is declining and nothing is promising: `references/examples.md`. How this
v1 could be extended (larger data volumes, broader comparisons, more
advanced analysis): `references/roadmap.md`.

## What to do, in order

1. **Parse the request** into topic(s), language edition(s), a time period,
   any user-defined bar for "promising" (growth %, trend strength), and --
   if the question is about a behavior rather than the topic itself -- the
   `--intent` phrase. If the topic is missing, ask one short clarifying
   question. Everything else has a stated default -- don't ask about things
   that already default sensibly.
2. **Check `<outdir>/latest.json`** if it exists and the request is a
   follow-up ("also check German", "what if the bar were higher", "extend
   the range") -- reuse its topics/langs/pins/intent and change only what
   the user asked to change, then call the CLI again. The cache makes this cheap;
   you do not need to do anything special to "enable" reuse.
3. **Call the CLI once.**
4. **If `status` is `"needs_disambiguation"`**: the run did not fetch or
   compute anything. Show the user the candidates for each ambiguous
   topic/language pair (title + description) and ask them to pick. Then
   re-call the CLI with `--pin "<topic>:<lang>=<QID>"` for each choice.
   Never guess a candidate yourself.
5. **If `status` is `"needs_data"`**: Python could not reach Wikimedia
   (common in sandboxed agents), so nothing was analyzed yet. For **every**
   entry in `data_requests`:
   - open `url` exactly as given (it is an official Wikimedia API URL)
     with whatever you have: a web-fetch tool, a browser tool (read the page
     text), or a shell `curl`. If you have none, list the URLs for the user
     and ask them to open each one and paste back what it shows;
   - write the complete raw response body, unchanged, to `save_as` (a JSON
     object; if the API answered HTTP 404 with no readable body, write
     `{"status": 404, "title": "Not Found"}`). **Save it as UTF-8.** Most
     of what this skill fetches is non-English text (Polish, Ukrainian,
     Chinese article titles and search results are the normal case, not an
     edge case) -- a file-writing tool or shell redirect that uses your
     OS's default codepage instead of UTF-8 will silently turn correct
     characters into different, still-plausible-looking wrong ones (seen
     live: `Głodówka` saved as `GЕ‚ГіdГіwka` on a Windows box with a
     non-UTF-8 default codepage) rather than an obvious error. Prefer a
     tool that writes text files directly (its own file-write tool, not a
     hand-rolled shell/Python one-liner) so encoding is handled for you. If
     you can't be sure the write preserved the characters exactly, write
     `\uXXXX` JSON escapes for the non-ASCII characters instead of the
     literal characters -- equally valid JSON, and plain ASCII text cannot
     be corrupted this way.

   Then run `rerun_command` exactly as given. Repeat until the status is
   no longer `needs_data`. A run usually takes 2-4 rounds, because each
   round's answers determine the next URLs. An entry with a non-null
   `problem` was saved before but rejected: fetch and save it again. If the
   same URL is rejected twice, or a URL cannot be opened at all, stop and
   tell the user; do not work around it. A long daily-granularity request
   already arrives as several small monthly-sized URLs, not one big one --
   fetch each the same simple way, there is no need to shorten the request
   yourself. Whatever tool you open a URL with, copy its response exactly:
   do not summarize, reformat, or describe it -- a paraphrase will be
   rejected (its item count or fields won't match) and cost you a round.
6. **If `status` is `"error"`**: relay the `detail` message plainly and,
   if it's something the user can fix (bad dates, unknown language code,
   a `--pin` whose topic isn't spelled exactly as in `--topics`, too many
   topics x languages), suggest the fix.
7. **If `status` is `"ok"`**: the JSON is your only evidence. Every fact
   in the answer, and every reason behind a recommendation, must come from
   these fields:
   - `results.<topic>.<lang>.metrics`: the measurements;
   - `results.<topic>.<lang>.trend_strength` and `.promising`: the
     assessment, judged against `params.criteria_applied`;
   - `resolution`, `params` and `limitations`.

   Write three parts, labeled and **in this order**:
   - **Measured data**: for each series, give `growth_pct`,
     `trend_strength` and `promising`. When audiences are compared, also
     give `average_views`. Copy the numbers from the JSON. Rounding is
     fine (one decimal for percentages, whole views for counts); anything
     else is not. `growth_pct` compares the average of the second
     half of the period with the first half. Describe it that way, not as
     "since the start" or "per year". **When there are two or more series**
     (multiple languages and/or topics), a `comparison` field is present
     with two pre-sorted lists, `by_average_views` and `by_growth_pct`,
     rank 1 = largest/highest. Use these for any "largest", "smallest",
     "fastest-declining" or similar claim -- never sort or compare
     `metrics` rows yourself. This is not a formality: a real run called a
     smaller audience "the largest" and, in the same answer, called the
     actual largest one "close behind" that smaller number, because it
     compared four numbers by eye instead of reading the computed order.
     If `comparison` is absent (fewer than two series), make no ranking
     claim at all.
   - **Limitations**: the analyzed period (`params.start`–`params.end`,
     and say whether it was the default), languages that came back
     `not_found`, every entry in `limitations`, and the caveat that
     pageviews measure reading interest, not purchase intent. These come
     *before* any recommendation, never as a trailing footnote.
   - **Interpretation (not measured)**: open with a label such as
     "Interpretation -- my reading of the data above, not a measurement:".
     Recommendations belong only here. **Start from `narrative.per_series`
     and `narrative.summary`** (present whenever any series has usable
     data) -- they are pre-written, fully factual sentences built from the
     same fields the PDF uses. You may relay them close to verbatim or
     lightly smooth the wording for flow; you may not add a claim, a
     ranking word, a name, or a reason that is not already in them or
     directly traceable to `metrics`/`trend_strength`/`promising`/
     `comparison`. Every recommendation still names the series it rests on
     and the fields behind it, e.g. "de (growth -43.2%, strong,
     not_promising)".

   The interpretation must not contradict the measured data, and must not
   add anything the JSON doesn't contain. This has failed live, twice, in
   concrete ways -- treat every item below as something that actually
   happened, not a hypothetical:
   - **Direction is the sign of `growth_pct`.** Never describe a negative
     series as growing, rising, gaining momentum, or trending up, or a
     positive one as falling. Name the `trend_strength` next to every
     claim about direction. `weak` is a rough signal. `insufficient` or
     `insufficient_data` is no signal, and is not evidence of decline.
   - **"Promising" means `promising == "promising"`.** Don't use it or a
     synonym ("strong opportunity", "worth prioritizing", "clear winner")
     for any other series. If no series is `promising`, open the
     interpretation by saying so, and quote the criteria.
   - **Size is not promise, and decline is not stability.** `average_views`
     and `total_views` measure audience size, not the assessment. A large
     audience that is declining is still declining and still
     `not_promising` -- never call a declining series "stable", "reliable",
     "resilient", "consistent", or evidence of "sustained interest"; those
     words describe a shape `growth_pct`/`trend_strength` do not show.
     If you suggest looking at a series anyway, state its size, its
     decline and its assessment in the same sentence, and present the
     suggestion as your judgment, not a finding.
   - **Every comparative word maps to a specific `comparison` rank, or it
     doesn't get said.** "Largest", "smallest", "fastest-declining",
     "flattest decline", "second-largest", "close behind" -- each of these
     is a claim about `rank` in `comparison.by_average_views` or
     `by_growth_pct` (rank 1 in `by_growth_pct` is the least-negative /
     flattest decline). Read the rank; never estimate it by eye. This is
     not a formality: one real run called a smaller audience "the largest"
     and, in the same answer, called the actual largest one "close behind"
     that smaller number; another called the series with the *second*
     -flattest decline "flattest". If `comparison` is absent (fewer than
     two series), make no comparative claim at all.
   - **Nothing outside the JSON.** No causes (news, seasonality, AI tools,
     pricing, culture, competition, "a structural shift", "changing reader
     behavior", "migration to other platforms") -- not as fact, and not
     hedged as a guess either ("might reflect", "could suggest", "possibly
     due to"): a hedge doesn't turn a guess into evidence, it just makes an
     unfounded claim sound careful. No place or country names -- a
     language edition is not a country, and the JSON does not say where
     readers are. No company, app, or competitor names (Duolingo, TikTok,
     ChatGPT, or anything else) -- none of that is in the data. No
     specific value not present in `metrics`/`sample_points` (a "peak" or
     a particular month's figure you have not actually read from
     `sample_points`). No multi-step research plan, interview script, or
     list of "things to validate" -- one line pointing at a series and its
     numbers is a recommendation; a market-research agenda is invention.
     If the user asks why a trend happened, or wants named markets or
     competitors to research, say plainly that the data doesn't cover
     that, rather than filling the gap. Don't state numbers you computed
     yourself either (shares, ratios, sums, differences, per-year rates,
     forecasts). If a point needs anything the JSON doesn't have, leave
     the point out.
   - **Honest bottom line.** When nothing meets the user's bar, say so
     directly. "None of these audiences meets your criteria" is a valid
     answer; don't soften it into a positive recommendation, and don't
     pad it with speculation to sound more complete. Worked regression
     cases: `references/examples.md` examples 4, 5 and 6.
8. **Deliver the artifacts, don't just name them**: `artifacts.chart` and
   `artifacts.report` are already-complete files (the PDF needs no
   narrative from you to be useful) -- don't re-describe the whole table
   in chat if the PDF already has it. If your environment gives you a way
   to attach or send a local file to the user as a downloadable artifact
   (a "send file", "share file", or equivalent tool), **use it for both
   files**. Printing the path or filename in chat text is not delivering
   the file -- the user cannot click or download a string, and a run is
   not complete until both are actually attached, not just mentioned. Only
   fall back to stating the path in text if your environment genuinely has
   no such capability.

## Never do this

- Never invent a pageview number, growth rate, or trend label -- everything
  comes from the JSON.
- Never make a claim or recommendation that contradicts `growth_pct`,
  `trend_strength` or `promising`, and never present a recommendation as a
  measured finding. It goes under "Interpretation (not measured)".
- Never resolve an ambiguous topic by guessing the first candidate.
- Never call `trend_strength` a "confidence level," "statistically
  significant," "statistically strong," or any other phrase implying a
  significance test was run -- it's a heuristic fit-quality score derived
  from R² and data span, nothing more (see `references/methodology.md`).
  Say "the data fits a [strong/moderate/weak] trend line" or "trend
  strength: X," not "statistically X."
- Never omit the `limitations` array from your answer.
- Never substitute anything for Wikimedia pageview data: no web search
  results, news, Google Trends, third-party traffic or market figures, no
  pageview tools other than the URLs in `data_requests`, and no values from
  memory. For `needs_data`, save only the body of the exact URL requested,
  and never edit, trim, reformat or write any of it yourself. Do not scrape
  Wikipedia article pages for this data either. If the official responses
  cannot be obtained, say so; do not answer the question another way.
- Never re-run the full pipeline from scratch for a follow-up when reusing
  `latest.json`'s parameters and letting the cache do its job would do.
