# End-to-end validation with a fast, inexpensive model

CASE.md requires the complete flow to be tested with a model such as
Claude Haiku 4.5. This is the record of that run.

## Setup

- **Model:** Claude Haiku 4.5, run as a Claude Code subagent
  (model alias `haiku`), 2026-09-25.
- **Given only:** the path to this skill directory, a ready Python
  environment, and five user messages in sequence (one conversation). It
  had no other instructions about how the skill works -- it had to read
  `SKILL.md` and drive `scripts/pageviews_cli.py` itself.
- **Real data:** every call hit the live Wikidata, Wikipedia and Wikimedia
  Pageviews APIs.
- **Cost:** 10 tool calls, ~67k tokens, ~3 minutes for all five messages.

## Results

| # | User message (abridged) | What Haiku ran | Outcome |
|---|---|---|---|
| 1 | Intermittent fasting, Polish vs Czech, last two years | `--topics "intermittent fasting" --langs pl,cs` | `ok`. Reported cs -53.64% (moderate) and pl `not_found` as an excluded language, not a silent drop. Stated the default 24-month period. |
| 2 | Follow-up: add Slovak and German, promising only at >=25% growth | Same topic, `--langs pl,cs,sk,de --criteria "min_growth_pct=25,min_trend_strength=moderate"` | `ok`. Reused prior parameters and changed only what was asked. de -43.24% (strong); pl and sk `not_found`. |
| 3 | Astronomy course: is interest growing in Ukrainian, and how confident? | `--topics "astronomy" --langs uk` | `ok`. uk -59.62%, moderate, R² 0.4844. Said "moderate trend fit", not "statistically significant". |
| 4 | Is interest in Mercury growing in German since Jan 2025? | First run returned `needs_disambiguation`; after the user said "the planet": `--topics "Mercury" --langs de --start 2025-01-01 --end 2026-08-31 --pin "Mercury:de=Q308"` | Listed planet/element/god with their QIDs and asked the user instead of guessing. Pinned re-run was `ok`: -15.05%, moderate. |
| 5 | Language-learning app: compare interest in learning English; prepare a short report | Asked which editions; after "Polish, German, Spanish and Japanese": `--topics "English language" --langs pl,de,es,ja` | `ok`. Ranked editions from the JSON and pointed to `report.pdf`. |

Checks on the transcript:

- The metrics Haiku quoted in message 5 matched `latest.json` value by
  value. Separately, the skill's metrics for a live series
  (en "Astronomy", 24 months) were recomputed from the raw API with
  independent code: growth, slope, R² and total views matched exactly.
- Answers followed the required order: measured data, then limitations,
  then interpretation. The `limitations` array was relayed each time.
- `trend_strength` was never described as a confidence level or a
  significance test.

## Deviations observed

- **Derived numbers:** in message 5 Haiku stated two figures it computed
  itself ("70% of combined traffic", "4.4x"). `SKILL.md` already says
  never to state a number that is not in the JSON, so this was a model
  compliance lapse, not a missing capability.
- **Mild speculation:** in message 5 it guessed at a reason for the
  differences between audiences ("possible price-sensitivity differences").
- **Proxy caveat missing:** message 5 measured the "English language"
  article, but Haiku did not say this is only a proxy for people *learning*
  English. That gap motivated the `--intent` flag: the CLI now adds this
  caveat to `limitations` and the PDF automatically, so it no longer
  depends on the model remembering it.

## Scope of this validation

The run exercised the skill after the audit fixes to date: whole-month
periods, the in-progress month excluded, JSON errors for invalid language
codes and unmatched pins, and the `zh-yue`-style sitelink fix. Changes made
afterwards were not re-run through Haiku; they are covered by the test
suite, a clean-install smoke test and live CLI runs instead:

- dependency pins moved to numpy 2.3.5 / matplotlib 3.10.8 (same numbers
  on a live re-run);
- `spike_periods` removed from the metrics;
- CJK text in the PDF and chart;
- the `--intent` proxy limitation;
- the agent relay data-access fallback (below);
- the tighter final-response rules in `SKILL.md` step 7, and regression
  example 4 in `references/examples.md` (the largest audience declining,
  nothing promising). They target the deviations listed above: derived
  numbers, speculation, and recommendations not labeled as interpretation.
  The data side of example 4 is locked by
  `test_regression_example_largest_audience_declining_nothing_promising`.
  How a model actually phrases its answer can only be checked by
  re-running Haiku, which has not been done yet.

## Relay fallback check (2026-09-25)

This check targets the data-access fallback for sandboxes where Python
cannot reach Wikimedia (`references/api-notes.md`). It was driven by the
developing agent (Claude Opus), not by Haiku, and has not yet been repeated
with Haiku.

- **Blocked network, simulated:** `HTTPS_PROXY`/`HTTP_PROXY` pointed at a
  closed local port, so every Python request failed with `ProxyError`,
  as it would behind a sandbox egress block.
- **Request:** `--topics "Intermittent fasting" --langs en,cs --start 2023-09-01 --end 2025-09-01`
  in the default `auto` mode.
- **Flow:** the first run noticed the failure (about 11 s including
  retries) and returned `needs_data` with one Wikidata search URL. Each
  requested URL was then opened in the Claude desktop app's built-in
  browser. The page text was saved verbatim to `save_as`, and
  `rerun_command` was run. The three relay rounds asked for 1, 2 (entities
  + sitelink counts together) and 2 (both pageview series) URLs; the fourth
  run returned `ok` with chart and PDF.
- **Result:** `results` were identical to a direct (unblocked) run of the
  same command, apart from the new `data_origin` field (`relay` vs.
  `direct`): en -62.82%, 855,332 views, R² 0.4759, moderate; cs -26.54%,
  weak. `limitations` carried the `data access` entry.
- **Direct path unchanged:** the same command without the proxy, plus
  "Astronomy" (uk, -59.62%, moderate) and "Mercury" (de, still
  `needs_disambiguation` between planet, element and god), reproduced the
  figures recorded above. This confirms that the smaller entity requests
  plus separate sitelink counts keep the ambiguity rule's behavior.
- **Rejection paths** (wrong article/project/agent, out-of-period or
  duplicate items, non-JSON text, the real 404 body) are covered by
  `tests/test_fetch.py`, and multi-round resolution by
  `tests/test_resolve.py`.

## Relay fallback with Haiku 4.5 (2026-09-26)

The check above was driven by Opus, not Haiku, and the case requires the
skill to actually work on a fast, inexpensive model. Two live Haiku 4.5
subagents drove the relay end to end with `--data-source relay`, each with
a real WebFetch tool and no other guidance beyond `SKILL.md`. Every number
either produced was independently cross-checked against a direct
(unblocked) run of the identical command; both matched exactly.

| Run | Request | Rounds | Result | Cross-check |
|---|---|---|---|---|
| Basic | `--topics "intermittent fasting" --langs pl,cs` | 8 (2 extra -- see below) | cs -53.64%, moderate, 6,939 total views; pl `not_found` | Direct run: identical, value for value |
| Chunked daily | `--topics "Astronomy" --langs en --start 2024-01-01 --end 2024-03-10 --granularity daily` | 3 | 70/70 data points, -2.27%, weak, R² 0.0002; zero relayed bodies rejected | Direct run: identical, value for value |

**Chunking worked as designed.** The 70-day daily request (confirmed live
at ~22 KB unchunked) arrived to the agent as three separate monthly-sized
URLs (Jan, Feb, first 10 days of March) instead of one large one, per the
fix in `fetch.py` (`_MAX_RELAY_ITEMS`, `util.month_chunks`). All three were
copied correctly on the first attempt and merged into one continuous
70-point series with no gap or duplicate at the chunk boundaries.

**A real, previously unvalidated failure mode: write-side encoding
corruption.** In the basic run, Haiku's *own* save step corrupted a Polish
search result while saving it to `save_as`: `Głodówka` became
`GЕ‚ГіdГіwka` -- classic UTF-8-bytes-read-as-a-different-codepage mojibake,
not a Wikimedia API problem. This is not a hypothetical risk: this session
independently hit the identical `cp1251`-default-codepage issue printing
Polish/Ukrainian text on the same Windows machine. Impact if unnoticed:
the corrupted (but still well-formed, still plausible-looking) title would
fail to match any real article, and the topic would incorrectly resolve to
`not_found` instead of its real article -- a wrong result, but never
fabricated pageview numbers, because a garbled title 404s at the pageviews
step rather than returning data for the wrong page. Haiku noticed the
corruption itself, re-fetched with an explicit UTF-8-encoded write, and
the final numbers were correct -- but a fix that depends on the model
happening to notice its own mangled output is not something "must work on
Haiku 4.5" can rely on. Fixed at the source: both `SKILL.md` step 5 and
the CLI's own `needs_data.next_step` (read by the agent every relay round,
not just once at the start) now explicitly say to save as UTF-8 and offer
a `\uXXXX`-escape fallback that is immune to codepage corruption entirely.
This specific fix has not yet been re-validated with a fresh Haiku run
(would need to reproduce a non-UTF-8-default environment on demand, which
isn't scripted); the failure mode it targets, and that failures of this
kind resolve safely rather than fabricate data, are confirmed above.

**Both runs' final answers** followed the required measured-data /
limitations / interpretation order and never called `trend_strength` a
confidence or significance result.
