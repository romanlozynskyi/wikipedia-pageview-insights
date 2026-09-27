import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import analyze  # noqa: E402


def _series(views: list[int]) -> list[dict]:
    return [{"date": f"2024-{i+1:02d}-01", "views": v} for i, v in enumerate(views)]


def test_empty_series_is_insufficient():
    result = analyze.analyze_series([])
    assert result["metrics"]["data_points"] == 0
    assert result["trend_strength"] == "insufficient"
    assert result["metrics"]["growth_pct"] is None


def test_single_point_is_insufficient():
    result = analyze.analyze_series(_series([100]))
    assert result["metrics"]["data_points"] == 1
    assert result["trend_strength"] == "insufficient"


def test_all_zero_series():
    result = analyze.analyze_series(_series([0] * 8))
    assert result["metrics"]["total_views"] == 0
    assert result["metrics"]["growth_pct"] is None  # first_half_avg is 0, growth is undefined
    assert result["metrics"]["zero_periods"] == 8


def test_clear_strong_growth_trend():
    # Perfectly linear growth: R^2 should be 1.0, strong trend
    views = [10, 20, 30, 40, 50, 60, 70, 80]
    result = analyze.analyze_series(_series(views))
    m = result["metrics"]
    assert m["data_points"] == 8
    assert m["total_views"] == sum(views)
    assert m["growth_pct"] is not None and m["growth_pct"] > 0
    assert m["r_squared"] > 0.99
    assert result["trend_strength"] == "strong"


def test_clear_decline_trend():
    views = [80, 70, 60, 50, 40, 30, 20, 10]
    result = analyze.analyze_series(_series(views))
    m = result["metrics"]
    assert m["growth_pct"] < 0
    assert m["slope"] < 0
    assert result["trend_strength"] == "strong"


def test_flat_series_is_weak_trend():
    views = [50, 51, 49, 50, 50, 49, 51, 50]
    result = analyze.analyze_series(_series(views))
    assert result["metrics"]["growth_pct"] is not None
    assert abs(result["metrics"]["growth_pct"]) < 10
    assert result["trend_strength"] in ("weak", "insufficient")


def test_noisy_series_with_some_zero_gaps_forced_weak():
    # Enough points for a fit, but >30% zero periods should force "weak"
    # even if the non-zero points happen to trend upward.
    views = [0, 0, 0, 10, 20, 30, 40, 50]  # 3/8 = 37.5% zero
    result = analyze.analyze_series(_series(views))
    assert result["metrics"]["zero_periods"] == 3
    assert result["trend_strength"] == "weak"


def test_metrics_have_no_spike_periods():
    # Removed from v1: a median+4*MAD rule on raw levels flagged whole
    # high-level stretches of trending series as "spikes" (e.g. 8 of 24
    # months), which was misleading and never used in any conclusion.
    views = [10, 11, 9, 10, 500, 10, 11, 9]
    assert "spike_periods" not in analyze.analyze_series(_series(views))["metrics"]
    assert "spike_periods" not in analyze.analyze_series([])["metrics"]


def test_parse_criteria_defaults():
    criteria = analyze.parse_criteria(None)
    assert criteria == analyze.DEFAULT_CRITERIA


def test_parse_criteria_custom():
    criteria = analyze.parse_criteria("min_growth_pct=30,min_trend_strength=strong")
    assert criteria["min_growth_pct"] == 30.0
    assert criteria["min_trend_strength"] == "strong"


def test_parse_criteria_rejects_unknown_key():
    with pytest.raises(ValueError):
        analyze.parse_criteria("bogus_key=1")


def test_parse_criteria_rejects_bad_strength_value():
    with pytest.raises(ValueError):
        analyze.parse_criteria("min_trend_strength=super_strong")


def test_classify_promising_strong_growth():
    analysis = analyze.analyze_series(_series([10, 20, 30, 40, 50, 60, 70, 80]))
    result = analyze.classify_promising(analysis, analyze.DEFAULT_CRITERIA)
    assert result == "promising"


def test_classify_promising_decline_is_not_promising():
    analysis = analyze.analyze_series(_series([80, 70, 60, 50, 40, 30, 20, 10]))
    result = analyze.classify_promising(analysis, analyze.DEFAULT_CRITERIA)
    assert result == "not_promising"


def test_classify_promising_insufficient_data():
    analysis = analyze.analyze_series(_series([100]))
    result = analyze.classify_promising(analysis, analyze.DEFAULT_CRITERIA)
    assert result == "insufficient_data"


def test_classify_promising_respects_custom_threshold():
    # Growth of ~28% with a clean trend should pass a low bar and fail a high one.
    views = [100, 105, 110, 115, 120, 125, 128, 130]
    analysis = analyze.analyze_series(_series(views))
    lenient = analyze.classify_promising(analysis, {"min_growth_pct": 5, "min_trend_strength": "weak"})
    strict = analyze.classify_promising(analysis, {"min_growth_pct": 90, "min_trend_strength": "strong"})
    assert lenient == "promising"
    assert strict == "not_promising"


# --- rank_series: regression coverage for a real observed bug. A live Cowork
# run called Spanish (31,296 avg views) "the largest audience" and, in the
# same answer, called Japanese (39,560 avg views -- actually the largest)
# "close behind" that smaller number, because the model compared four rows
# of a table itself instead of reading a computed order. rank_series exists
# so the model never has to do that comparison.


def _ok_entry(average_views: float, growth_pct: float | None) -> dict:
    return {"fetch_status": "ok", "metrics": {"average_views": average_views, "growth_pct": growth_pct}}


def test_rank_series_matches_the_observed_pl_de_es_ja_regression():
    results = {
        "English language": {
            "pl": _ok_entry(8991, -18.3),
            "de": _ok_entry(24496, -8.0),
            "es": _ok_entry(31296, -29.1),
            "ja": _ok_entry(39560, -14.9),
        }
    }
    comparison = analyze.rank_series(results)

    by_views = comparison["by_average_views"]
    assert [(e["lang"], e["rank"]) for e in by_views] == [("ja", 1), ("es", 2), ("de", 3), ("pl", 4)]

    by_growth = comparison["by_growth_pct"]
    # rank 1 = highest (least negative) growth_pct: -8.0 > -14.9 > -18.3 > -29.1
    assert [(e["lang"], e["rank"]) for e in by_growth] == [("de", 1), ("ja", 2), ("pl", 3), ("es", 4)]


def test_rank_series_returns_none_for_a_single_series():
    results = {"Topic": {"en": _ok_entry(1000, 10.0)}}
    assert analyze.rank_series(results) is None


def test_rank_series_returns_none_when_nothing_succeeded():
    results = {"Topic": {"en": {"fetch_status": "no_data"}, "de": {"fetch_status": "error", "detail": "x"}}}
    assert analyze.rank_series(results) is None


def test_rank_series_excludes_failed_fetches_but_keeps_ok_ones():
    results = {
        "Topic": {
            "en": _ok_entry(500, 5.0),
            "de": _ok_entry(1500, 15.0),
            "fr": {"fetch_status": "no_data"},
        }
    }
    comparison = analyze.rank_series(results)
    langs = {e["lang"] for e in comparison["by_average_views"]}
    assert langs == {"en", "de"}


def test_rank_series_puts_null_growth_last_with_null_rank_not_dropped():
    results = {
        "Topic": {
            "en": _ok_entry(1000, 10.0),
            "de": _ok_entry(2000, None),  # e.g. zero views in the first half
        }
    }
    comparison = analyze.rank_series(results)
    by_growth = comparison["by_growth_pct"]
    assert by_growth[0] == {"topic": "Topic", "lang": "en", "growth_pct": 10.0, "rank": 1}
    assert by_growth[1] == {"topic": "Topic", "lang": "de", "growth_pct": None, "rank": None}
    # but it's still ranked by audience size, since that key has a value
    by_views = comparison["by_average_views"]
    assert [(e["lang"], e["rank"]) for e in by_views] == [("de", 1), ("en", 2)]


def test_rank_series_across_multiple_topics_not_just_languages():
    results = {
        "Topic A": {"en": _ok_entry(100, 1.0)},
        "Topic B": {"en": _ok_entry(9000, 50.0)},
    }
    comparison = analyze.rank_series(results)
    assert comparison["by_average_views"][0]["topic"] == "Topic B"
    assert comparison["by_average_views"][1]["topic"] == "Topic A"


# --- build_narrative: regression coverage for a second real Cowork failure.
# The chat answer invented countries (Mexico, Taiwan), competitor apps
# (Duolingo, ChatGPT), causal theories ("migration to other platforms"),
# and mislabeled a decline "stable"/"flattest" when it measurably was not
# the flattest -- none of that is in the JSON. build_narrative gives the
# model pre-written, fully factual sentences with nothing left to invent.


def _full_entry(average_views: float, growth_pct: float | None, trend_strength: str, promising: str, data_points: int = 24) -> dict:
    return {
        "fetch_status": "ok",
        "metrics": {"average_views": average_views, "growth_pct": growth_pct, "data_points": data_points},
        "trend_strength": trend_strength,
        "promising": promising,
    }


def test_build_narrative_matches_the_observed_pl_de_es_ja_regression():
    results = {
        "English language": {
            "pl": _full_entry(8991, -18.3, "strong", "not_promising"),
            "de": _full_entry(24496, -8.0, "moderate", "not_promising"),
            "es": _full_entry(31296, -29.1, "strong", "not_promising"),
            "ja": _full_entry(39560, -14.9, "strong", "not_promising"),
        }
    }
    narrative = analyze.build_narrative(results, analyze.DEFAULT_CRITERIA)

    assert len(narrative["per_series"]) == 4
    de_sentence = next(s for s in narrative["per_series"] if "de.wikipedia" in s)
    assert "declined by 8.0%" in de_sentence
    assert "trend strength: moderate" in de_sentence
    assert "not_promising" in de_sentence
    # every sentence states a real classification -- none upgraded to "promising"
    assert all("classification: not_promising" in s for s in narrative["per_series"])
    assert narrative["summary"] == "0 of 4 series meet the promising criteria (min_growth_pct=15.0, min_trend_strength=moderate)."
    # nothing invented can appear in template-generated text: no country or
    # company names exist anywhere in the source data to leak into it
    joined = " ".join(narrative["per_series"]) + narrative["summary"]
    for invented in ("Mexico", "Taiwan", "Duolingo", "ChatGPT", "TikTok", "structural shift"):
        assert invented not in joined


def test_build_narrative_never_calls_a_decline_stable_or_omits_direction():
    results = {"Topic": {"en": _full_entry(1000, -0.5, "weak", "not_promising")}}
    narrative = analyze.build_narrative(results, analyze.DEFAULT_CRITERIA)
    sentence = narrative["per_series"][0]
    assert "declined" in sentence
    for forbidden in ("stable", "reliable", "resilient", "consistent", "sustained"):
        assert forbidden not in sentence.lower()


def test_build_narrative_summary_names_promising_series_when_any_exist():
    results = {
        "Topic": {
            "en": _full_entry(1000, 20.0, "strong", "promising"),
            "de": _full_entry(500, -5.0, "weak", "not_promising"),
        }
    }
    narrative = analyze.build_narrative(results, analyze.DEFAULT_CRITERIA)
    assert "1 of 2" in narrative["summary"]
    assert "'Topic' (en)" in narrative["summary"]
    assert "de" not in narrative["summary"].split("'Topic' (en)")[1]  # only the promising one is named


def test_build_narrative_returns_none_when_nothing_succeeded():
    results = {"Topic": {"en": {"fetch_status": "no_data"}}}
    assert analyze.build_narrative(results, analyze.DEFAULT_CRITERIA) is None


def test_build_narrative_handles_null_growth_pct_without_crashing():
    results = {"Topic": {"en": _full_entry(1000, None, "insufficient", "insufficient_data")}}
    narrative = analyze.build_narrative(results, analyze.DEFAULT_CRITERIA)
    assert "could not be measured" in narrative["per_series"][0]


def test_trend_phrase_direction_matches_sign():
    assert "grew" in analyze.trend_phrase(5.0, "moderate")
    assert "declined" in analyze.trend_phrase(-5.0, "moderate")
    assert "could not be measured" in analyze.trend_phrase(None, "insufficient")


def test_flatten_results_excludes_non_ok_entries():
    results = {"Topic": {"en": _full_entry(1000, 1.0, "weak", "not_promising"), "de": {"fetch_status": "error", "detail": "x"}}}
    rows = analyze.flatten_results(results)
    assert len(rows) == 1
    assert rows[0]["lang"] == "en"
