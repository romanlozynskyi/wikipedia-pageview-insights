# Wikipedia Pageview Insights — Genesis AI case submission

An Agent Skill that helps founders read [Wikipedia pageview data](https://wikimedia.org/api/rest_v1/) as
a proxy for public interest in a topic — across time periods and language
editions — to decide which topics or audiences are worth investigating
further. Built for [`CASE.md`](CASE.md) (Genesis AI Product Engineering
School technical case).

## Where things are

- **[`CASE.md`](CASE.md)** — the original case prompt.
- **[`wikipedia-pageview-insights/`](wikipedia-pageview-insights/)** — the
  skill itself. Everything required to run it lives inside this one
  directory (code, tests, docs, dependency pins), per the case's
  requirement that the skill be self-contained.

## Quick start

```bash
cd wikipedia-pageview-insights
pip install -r requirements.txt
python scripts/pageviews_cli.py --topics "Intermittent fasting" --langs pl,cs
```

This fetches real Wikimedia pageview data, computes trend/growth metrics
in code (not by the model), and writes a chart (`chart.png`), a one-page
PDF report (`report.pdf`), and the full JSON result to `wpv-output/` (or
wherever `--outdir` points). `wikipedia-pageview-insights/SKILL.md` is the
entry point for how an agent should drive the CLI — arguments, the JSON
shape, and how to handle follow-ups, ambiguous topics, and a blocked
network.

## Running the tests

```bash
cd wikipedia-pageview-insights
pip install -r requirements-dev.txt
pytest tests/ -q
```

160 tests, no network required (Wikimedia responses are mocked; a couple
of tests hit the live API and are marked accordingly).

## What makes this more than "call an API and print a table"

- **Core logic lives in code, not prompts.** Title resolution across
  language editions, trend/growth math, ranking, and the report's prose
  are all deterministic functions with unit tests — the model reads
  computed JSON fields, it does not compute or compare numbers itself.
  See `references/methodology.md` and `references/json-schema.md`.
- **Works even when the sandbox blocks Wikimedia directly.** A data-access
  relay lets the agent fetch the exact official API URLs with its own
  tools and hand the raw response back to the skill, rather than the run
  failing or falling back to invented/substituted data. See
  `references/api-notes.md`.
- **Validated live against a cheap, fast model** (Claude Haiku 4.5), not
  just a strong one, including the network-blocked path — see
  `references/validation.md` for the actual runs, numbers, and the real
  bugs that testing against Haiku specifically surfaced and fixed.
- **Regression tests lock in real failures found during testing** (a
  ranking bug, an encoding bug, invented causal claims, a proxy-detection
  gap), each with a `references/examples.md` write-up of what went wrong
  and why the fix holds. Extending this skill should keep adding to that
  file, not just to the code.

## Roadmap

`references/roadmap.md` covers how this could grow: more complex research
questions, larger data volumes, broader comparisons, more advanced
analysis — deliberately not built yet, per the case's instruction to keep
the initial implementation focused.
