#!/usr/bin/env python3
"""The single entry point for the wikipedia-pageview-insights skill.

One command, always the same pipeline: resolve -> fetch (cached) ->
analyze -> chart -> report -> one JSON document (stdout + <outdir>/latest.json).

If any requested topic/language pair is ambiguous, the run halts before
any fetching or analysis and returns status "needs_disambiguation" with
candidates for every ambiguous pair -- never a mix of partial results and
an ambiguity request.

If Python cannot reach Wikimedia (restricted agent sandboxes), the run
returns status "needs_data": the exact official API URLs the agent must
fetch with its own web/browser tool, where to save each raw body, and the
command to re-run (see datasource.py). See references/json-schema.md for the
full output shape and SKILL.md for how an agent should drive this command.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shlex
import sys
from pathlib import Path
from typing import Any

import requests

import analyze
import charts
import datasource
import fetch
import report
import resolve
import util

DEFAULT_MONTHS_BACK = 24
MAX_TOPICS_TIMES_LANGS = 40  # a "large request" guardrail, see references/api-notes.md


def _parse_pins(pin_args: list[str]) -> dict[tuple[str, str], str]:
    pins: dict[tuple[str, str], str] = {}
    for raw in pin_args:
        try:
            topic_lang, value = raw.split("=", 1)
            topic, lang = topic_lang.split(":", 1)
        except ValueError as exc:
            raise ValueError(f"--pin '{raw}' must look like 'Topic:lang=QID-or-Title'") from exc
        pins[(topic.strip(), lang.strip())] = value.strip()
    return pins


def _default_start(end: _dt.date) -> _dt.date:
    """1st of the month DEFAULT_MONTHS_BACK calendar months before `end`'s
    month. With the default end (today) and the monthly granularity this
    selects, the in-progress month is excluded, leaving exactly
    DEFAULT_MONTHS_BACK complete months."""
    months = end.year * 12 + (end.month - 1) - DEFAULT_MONTHS_BACK
    return _dt.date(months // 12, months % 12 + 1, 1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pageviews_cli.py", description=__doc__)
    p.add_argument("--topics", required=True, help="Comma-separated topics, e.g. 'Intermittent fasting'")
    p.add_argument("--langs", required=True, help="Comma-separated Wikipedia language codes, e.g. 'pl,cs'")
    p.add_argument("--start", default=None, help=f"YYYY-MM-DD (default: {DEFAULT_MONTHS_BACK} months before --end)")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--granularity", default="auto", choices=["auto", "daily", "monthly"])
    p.add_argument("--criteria", default=None, help="e.g. 'min_growth_pct=20,min_trend_strength=strong'")
    p.add_argument("--pin", action="append", default=[], help="'Topic:lang=QID-or-exact-title', repeatable")
    p.add_argument(
        "--intent",
        default=None,
        help="The user's behavioral question when it differs from the article topic, e.g. 'learning English' "
        "for --topics 'English language'; adds a proxy-measure limitation",
    )
    p.add_argument("--notes", default=None, help="Optional short text appended to the PDF verbatim")
    p.add_argument("--outdir", default="wpv-output", help="Working directory for cache and artifacts")
    p.add_argument(
        "--data-source",
        default="auto",
        choices=list(datasource.MODES),
        help="auto: Python fetches Wikimedia directly and falls back to the agent relay if it cannot; "
        "direct: never relay; relay: use only responses the agent saved (what needs_data's rerun_command uses)",
    )
    return p


def _rerun_command(argv: list[str]) -> str:
    """The same invocation, switched to --data-source relay."""
    kept: list[str] = []
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
        elif arg == "--data-source":
            skip_next = True
        elif not arg.startswith("--data-source="):
            kept.append(arg)
    script = Path(__file__).resolve().as_posix()
    return shlex.join(["python", script, *kept, "--data-source", "relay"])


def _needs_data_result(
    source: datasource.WikimediaSource,
    argv: list[str],
    params: dict[str, Any],
    resolution: dict[str, Any],
    limitations: list[str],
) -> dict[str, Any]:
    if source.direct_failure:
        why = f"Python could not reach the Wikimedia APIs directly ({source.direct_failure})."
    else:
        why = "Running with --data-source relay, so Python makes no network requests."
    requests_list = list(source.pending.values())
    return {
        "status": "needs_data",
        "reason": why,
        "params": params,
        "data_requests": requests_list,
        "next_step": (
            f"Fetch each of the {len(requests_list)} data_requests[].url -- official Wikimedia API URLs, "
            "exactly as given -- with your own web-fetch/browser tool (or ask the user to open them), save "
            "each raw response body verbatim to its save_as path, then run rerun_command. Entries with a "
            "`problem` were saved before but rejected; fetch and save them again. Never substitute data "
            "from any other source. Save as UTF-8 -- many of these responses are non-English (this skill's "
            "whole point) and writing them with your OS's default (non-UTF-8) codepage silently corrupts "
            "accented/Cyrillic/CJK characters into different, wrong-but-valid-looking text; if your save tool "
            "does not let you set the encoding, write \\uXXXX escapes for non-ASCII characters instead of the "
            "literal characters -- still valid JSON, immune to codepage corruption."
        ),
        "rerun_command": _rerun_command(argv),
        "resolution": resolution,
        "limitations": limitations
        + ["run halted before any analysis: the requested official Wikimedia responses have not been supplied yet"],
    }


def run(argv: list[str]) -> dict[str, Any]:
    args = build_parser().parse_args(argv)

    topics = [t.strip() for t in args.topics.split(",") if t.strip()]
    langs = [l.strip().lower() for l in args.langs.split(",") if l.strip()]
    if not topics:
        return {"status": "error", "detail": "--topics must contain at least one topic"}
    if not langs:
        return {"status": "error", "detail": "--langs must contain at least one language code"}

    try:
        pins = _parse_pins(args.pin)
    except ValueError as exc:
        return {"status": "error", "detail": str(exc)}
    pins = {(topic, lang.lower()): value for (topic, lang), value in pins.items()}
    unmatched = [f"{t}:{l}" for (t, l) in pins if t not in topics or l not in langs]
    if unmatched:
        return {
            "status": "error",
            "detail": (
                f"--pin {', '.join(unmatched)} does not match any requested topic/language pair; "
                "the topic must be spelled exactly as in --topics (case-sensitive) and the language "
                "must be one of --langs"
            ),
        }

    try:
        end = util.parse_date(args.end) if args.end else _dt.date.today()
        start = util.parse_date(args.start) if args.start else _default_start(end)
        util.validate_date_range(start, end)
    except ValueError as exc:
        return {"status": "error", "detail": str(exc)}

    limitations: list[str] = []
    clamped_start, was_clamped = util.clamp_start_date(start)
    if was_clamped:
        limitations.append(
            f"start date clamped to {clamped_start.isoformat()} -- Wikimedia pageview data begins {util.PAGEVIEWS_COVERAGE_START.isoformat()}"
        )
        start = clamped_start

    intent = (args.intent or "").strip() or None
    if intent:
        for topic in topics:
            if topic.casefold() != intent.casefold():
                limitations.append(
                    f"proxy measure: '{topic}' pageviews count people reading about that topic, not people "
                    f"'{intent}' -- an indirect signal of that intent, not a measure of it"
                )

    if len(topics) * len(langs) > MAX_TOPICS_TIMES_LANGS:
        return {
            "status": "error",
            "detail": (
                f"{len(topics)} topics x {len(langs)} languages = {len(topics) * len(langs)} series, "
                f"over the v1 limit of {MAX_TOPICS_TIMES_LANGS}. Narrow the topics/languages or split into "
                "multiple runs."
            ),
        }

    bad_langs = []
    for lang in langs:
        try:
            util.normalize_project(lang)
        except util.InvalidLanguageCode:
            bad_langs.append(lang)
    if bad_langs:
        return {
            "status": "error",
            "detail": f"not Wikipedia language codes: {', '.join(bad_langs)} (use codes like en, pl, cs, uk, zh-yue)",
        }

    try:
        granularity = args.granularity if args.granularity != "auto" else util.choose_granularity(start, end)
        criteria = analyze.parse_criteria(args.criteria)
    except ValueError as exc:
        return {"status": "error", "detail": str(exc)}

    if granularity == "monthly":
        # Every monthly point must be a whole month (see util.snap_*), and the
        # in-progress month is excluded rather than analyzed as if complete.
        start = util.snap_start_for_granularity(start, granularity)
        end = util.snap_end_for_granularity(end, granularity)
        last_complete = util.last_complete_month_end()
        if end > last_complete:
            limitations.append(
                f"the in-progress month {end.strftime('%Y-%m')} is excluded -- monthly analysis ends at the "
                f"last complete month ({last_complete.strftime('%Y-%m')})"
            )
            end = last_complete
        if end < start:
            return {
                "status": "error",
                "detail": "no complete month in the requested period; use --granularity daily or an earlier --start",
            }

    outdir = Path(args.outdir)
    cache_dir = outdir / "raw-cache"
    source = datasource.WikimediaSource(util.new_session(), mode=args.data_source, relay_dir=outdir / "relay")

    try:
        resolution = resolve.resolve_all(source, topics, langs, pins=pins)
    except (requests.RequestException, RuntimeError) as exc:
        return {
            "status": "error",
            "detail": (
                f"could not reach Wikidata/Wikipedia while resolving topics ({exc}). Check the language "
                "codes exist as Wikipedia editions and retry."
            ),
        }

    ambiguous = resolve.has_ambiguity(resolution)
    # Without ambiguity, pageviews for the already-resolved pairs are requested
    # now, so a relay round covers them together with any pending resolution.
    fetch_results = (
        fetch.fetch_all(source, cache_dir, resolution, granularity=granularity, start=start, end=end)
        if not ambiguous
        else {}
    )
    if source.pending:
        result = _needs_data_result(
            source,
            argv,
            {"topics": topics, "langs": langs, "start": start.isoformat(), "end": end.isoformat(), "granularity": granularity, "intent": intent},
            resolution,
            limitations,
        )
        util.write_run_record(outdir, result)
        return result

    if ambiguous:
        candidates = {
            topic: {lang: entry for lang, entry in lang_map.items() if entry.get("status") == "ambiguous"}
            for topic, lang_map in resolution.items()
            if any(entry.get("status") == "ambiguous" for entry in lang_map.values())
        }
        result = {
            "status": "needs_disambiguation",
            "params": {"topics": topics, "langs": langs, "start": start.isoformat(), "end": end.isoformat(), "granularity": granularity, "intent": intent},
            "resolution": resolution,
            "needs_disambiguation": candidates,
            "limitations": limitations
            + ["run halted before fetching any pageview data: resolve the ambiguous topic(s)/language(s) with --pin, then re-run"],
        }
        util.write_run_record(outdir, result)
        return result

    for topic, lang_map in resolution.items():
        for lang, entry in lang_map.items():
            if entry.get("status") == "not_found":
                limitations.append(f"'{topic}' ({lang}): no Wikipedia article could be found -- excluded from analysis")

    results: dict[str, dict[str, dict[str, Any]]] = {}
    series_for_chart: dict[str, list[dict[str, Any]]] = {}
    for topic, lang_map in fetch_results.items():
        results[topic] = {}
        for lang, fetched in lang_map.items():
            if fetched["status"] == "ok":
                analysis = analyze.analyze_series(fetched["points"])
                promising = analyze.classify_promising(analysis, criteria)
                results[topic][lang] = {
                    "fetch_status": "ok",
                    "metrics": analysis["metrics"],
                    "trend_strength": analysis["trend_strength"],
                    "promising": promising,
                    "sample_points": fetched["points"][:3] + fetched["points"][-3:],
                    "data_origin": fetched.get("origin", "direct"),
                }
                chart_label = f"{topic} ({lang})" if len(topics) > 1 and len(langs) > 1 else (lang if len(topics) == 1 else topic)
                series_for_chart[chart_label] = fetched["points"]
            elif fetched["status"] == "no_data":
                results[topic][lang] = {"fetch_status": "no_data", "data_origin": fetched.get("origin", "direct")}
                limitations.append(f"'{topic}' ({lang}): no pageview data available for this article/date range")
            else:
                results[topic][lang] = {"fetch_status": "error", "detail": fetched.get("detail")}
                limitations.append(f"'{topic}' ({lang}): fetch error -- {fetched.get('detail')}")

    relayed = [
        f"'{topic}' ({lang})"
        for topic, lang_map in results.items()
        for lang, entry in lang_map.items()
        if entry.get("data_origin") == "relay"
    ]
    if relayed or source.relayed_count:
        limitations.append(
            "data access: Python could not query Wikimedia directly, so the agent fetched the official Wikimedia "
            "API responses itself"
            + (f" (pageviews for {', '.join(relayed)})" if relayed else " (topic-to-article resolution)")
            + "; each pageview response was checked against the requested article, project, period and "
            "user-traffic filter before analysis"
        )

    params = {
        "topics": topics,
        "langs": langs,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "granularity": granularity,
        "criteria_applied": criteria,
        "intent": intent,
    }

    artifacts: dict[str, str] = {}
    run_result: dict[str, Any] = {
        "status": "ok",
        "params": params,
        "resolution": resolution,
        "results": results,
        "limitations": limitations,
        "artifacts": artifacts,
    }
    comparison = analyze.rank_series(results)
    if comparison is not None:
        run_result["comparison"] = comparison
    narrative = analyze.build_narrative(results, criteria)
    if narrative is not None:
        run_result["narrative"] = narrative

    chart_title = f"Wikipedia pageviews: {', '.join(topics)} ({', '.join(langs)})"
    chart_info = charts.render_chart(series_for_chart, title=chart_title, output_path=outdir / "chart.png")
    artifacts["chart"] = chart_info["path"]
    if chart_info["series_omitted"]:
        limitations.append(
            f"chart shows only the first {charts.MAX_SERIES_PER_CHART} series; omitted: {', '.join(chart_info['series_omitted'])}"
        )
    if chart_info["missing_glyphs"]:
        limitations.append(
            f"no installed font can draw these characters (no CJK-capable font found): "
            f"{''.join(chart_info['missing_glyphs'])} -- they are missing from the chart and the PDF shows them "
            "only in viewers that substitute a CJK font; the names are exact in the JSON. Installing e.g. "
            "Microsoft YaHei, Malgun Gothic or WenQuanYi Zen Hei fixes both"
        )

    report_path = report.generate_report(
        run_result,
        chart_path=Path(chart_info["path"]),
        output_path=outdir / "report.pdf",
        notes=args.notes,
    )
    artifacts["report"] = str(report_path)

    util.write_run_record(outdir, run_result)
    return run_result


def main() -> None:
    result = run(sys.argv[1:])
    util.print_json(result)
    if result.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
