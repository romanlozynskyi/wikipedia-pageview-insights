"""Trend math and evidence-strength / "promising" classification.

Implements exactly what references/methodology.md documents. Every number
here is deterministic and derived only from the raw pageview series --
never from LLM judgment.
"""
from __future__ import annotations

from typing import Any

import numpy as np

DEFAULT_CRITERIA = {"min_growth_pct": 15.0, "min_trend_strength": "moderate"}
_STRENGTH_ORDER = ["insufficient", "weak", "moderate", "strong"]


def analyze_series(points: list[dict[str, Any]]) -> dict[str, Any]:
    """points: [{"date": "YYYY-MM-DD", "views": int}, ...] in chronological order."""
    n = len(points)
    if n == 0:
        return {
            "metrics": {
                "data_points": 0,
                "total_views": 0,
                "average_views": 0.0,
                "growth_pct": None,
                "slope": None,
                "r_squared": None,
                "zero_periods": 0,
            },
            "trend_strength": "insufficient",
        }

    views = np.array([p["views"] for p in points], dtype=float)
    total = float(views.sum())
    average = total / n

    mid = n // 2
    first_half = views[:mid] if mid > 0 else views[:1]
    second_half = views[mid:] if mid > 0 else views[:1]
    first_half_avg = float(first_half.mean())
    second_half_avg = float(second_half.mean())
    growth_pct = (
        None
        if first_half_avg == 0
        else (second_half_avg - first_half_avg) / first_half_avg * 100.0
    )

    x = np.arange(n, dtype=float)
    if n >= 2 and views.std() > 0:
        slope, _intercept = np.polyfit(x, views, 1)
        corr = np.corrcoef(x, views)[0, 1]
        r_squared = float(corr**2) if not np.isnan(corr) else 0.0
        slope = float(slope)
    else:
        slope, r_squared = 0.0, 0.0

    zero_periods = int((views == 0).sum())

    trend_strength = _classify_trend_strength(n, r_squared, zero_periods)

    return {
        "metrics": {
            "data_points": n,
            "total_views": int(total),
            "average_views": round(average, 2),
            "first_half_avg": round(first_half_avg, 2),
            "second_half_avg": round(second_half_avg, 2),
            "growth_pct": round(growth_pct, 2) if growth_pct is not None else None,
            "slope": round(slope, 4),
            "r_squared": round(r_squared, 4),
            "zero_periods": zero_periods,
        },
        "trend_strength": trend_strength,
    }


def _classify_trend_strength(n: int, r_squared: float, zero_periods: int) -> str:
    if n < 6:
        return "insufficient"
    if zero_periods / n > 0.3:
        return "weak"
    if r_squared < 0.15:
        return "weak"
    if r_squared <= 0.5:
        return "moderate"
    return "strong"


def parse_criteria(criteria_str: str | None) -> dict[str, Any]:
    """Parse "--criteria min_growth_pct=20,min_trend_strength=strong" into a
    dict, layered on top of DEFAULT_CRITERIA."""
    criteria = dict(DEFAULT_CRITERIA)
    if not criteria_str:
        return criteria
    for pair in criteria_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"criteria entry '{pair}' is not key=value")
        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "min_growth_pct":
            criteria[key] = float(value)
        elif key == "min_trend_strength":
            if value not in _STRENGTH_ORDER:
                raise ValueError(f"min_trend_strength must be one of {_STRENGTH_ORDER}, got '{value}'")
            criteria[key] = value
        else:
            raise ValueError(f"unknown criteria key '{key}'")
    return criteria


def classify_promising(analysis: dict[str, Any], criteria: dict[str, Any]) -> str:
    """Returns "promising", "not_promising", or "insufficient_data"."""
    growth_pct = analysis["metrics"]["growth_pct"]
    trend_strength = analysis["trend_strength"]

    if growth_pct is None or trend_strength == "insufficient":
        return "insufficient_data"

    strength_ok = _STRENGTH_ORDER.index(trend_strength) >= _STRENGTH_ORDER.index(
        criteria["min_trend_strength"]
    )
    growth_ok = growth_pct >= criteria["min_growth_pct"]
    return "promising" if (strength_ok and growth_ok) else "not_promising"


def rank_series(results: dict[str, dict[str, dict[str, Any]]]) -> dict[str, list[dict[str, Any]]] | None:
    """Deterministically rank every successfully-fetched (topic, lang) series
    by audience size (average_views) and by growth_pct, rank 1 = largest/
    highest. Comparing more than a couple of numbers across a table is
    exactly the kind of "avoidable manual calculation" this skill exists to
    take off the model -- and a real observed failure, not a hypothetical
    one: a live run called a smaller audience "the largest" and, in the
    same answer, called the actual largest one "close behind" a smaller
    number. SKILL.md points the model at this field instead of letting it
    compare `metrics` entries itself.

    Returns None when fewer than two series succeeded (nothing to rank).
    Entries with growth_pct None (undefined -- see analyze_series) sort
    last in that ranking, with rank None, rather than being dropped.
    """
    entries = [
        {
            "topic": topic,
            "lang": lang,
            "average_views": entry["metrics"]["average_views"],
            "growth_pct": entry["metrics"]["growth_pct"],
        }
        for topic, lang_map in results.items()
        for lang, entry in lang_map.items()
        if entry.get("fetch_status") == "ok"
    ]
    if len(entries) < 2:
        return None

    def _ranked(key: str) -> list[dict[str, Any]]:
        with_value = sorted((e for e in entries if e[key] is not None), key=lambda e: e[key], reverse=True)
        without_value = [e for e in entries if e[key] is None]
        ranked = [
            {"topic": e["topic"], "lang": e["lang"], key: e[key], "rank": i}
            for i, e in enumerate(with_value, start=1)
        ]
        ranked += [{"topic": e["topic"], "lang": e["lang"], key: None, "rank": None} for e in without_value]
        return ranked

    return {"by_average_views": _ranked("average_views"), "by_growth_pct": _ranked("growth_pct")}


HALF_COMPARISON = "average views in the second half of the selected period vs. the first half"


def trend_phrase(growth_pct: float | None, trend_strength: str) -> str:
    """The one sentence fragment every surface (PDF, chat narrative) uses to
    describe a series' direction -- shared so both always agree, and so
    "grew"/"declined" can never drift from the sign of growth_pct. growth_pct
    is the half-vs-half change, not start-to-end or a per-year rate, so the
    phrase always says what it compares rather than leaving that implicit."""
    if growth_pct is None:
        return (
            "could not be measured (the change compares the second half of the selected period "
            "with the first half, and the first half had no views or no usable data)"
        )
    direction = "grew" if growth_pct >= 0 else "declined"
    return f"{direction} by {abs(growth_pct):.1f}% ({HALF_COMPARISON}; trend strength: {trend_strength})"


def flatten_results(results: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every successfully-fetched (topic, lang) series as one flat row --
    the shared shape `trend_phrase`-based sentences and `report.py`'s table
    are both built from."""
    rows = []
    for topic, lang_map in results.items():
        for lang, entry in lang_map.items():
            if entry.get("fetch_status") != "ok":
                continue
            m = entry["metrics"]
            rows.append(
                {
                    "topic": topic,
                    "lang": lang,
                    "data_points": m["data_points"],
                    "average_views": m["average_views"],
                    "growth_pct": m["growth_pct"],
                    "trend_strength": entry["trend_strength"],
                    "promising": entry["promising"],
                }
            )
    return rows


def build_narrative(
    results: dict[str, dict[str, dict[str, Any]]], criteria: dict[str, Any]
) -> dict[str, Any] | None:
    """One factual sentence per series plus one summary sentence, entirely
    template-generated from measured fields -- nothing here is an inference.
    This is the intended basis for the chat-facing "Interpretation" section:
    a model that relays or lightly rephrases these sentences cannot invent a
    ranking, a cause, a place, or a company that isn't in them, which
    prose instructions alone have not reliably prevented (see SKILL.md and
    references/examples.md example 5 for the real failures this closes).

    Returns None when no series has usable data (nothing to narrate).
    """
    rows = flatten_results(results)
    if not rows:
        return None

    per_series = [
        f"'{r['topic']}' on {r['lang']}.wikipedia {trend_phrase(r['growth_pct'], r['trend_strength'])}; "
        f"classification: {r['promising']}."
        for r in rows
    ]

    promising_rows = [r for r in rows if r["promising"] == "promising"]
    criteria_str = f"min_growth_pct={criteria['min_growth_pct']}, min_trend_strength={criteria['min_trend_strength']}"
    if promising_rows:
        names = ", ".join(f"'{r['topic']}' ({r['lang']})" for r in promising_rows)
        summary = (
            f"{len(promising_rows)} of {len(rows)} series meet the promising criteria ({criteria_str}): {names}."
        )
    else:
        summary = f"0 of {len(rows)} series meet the promising criteria ({criteria_str})."

    return {"per_series": per_series, "summary": summary}
