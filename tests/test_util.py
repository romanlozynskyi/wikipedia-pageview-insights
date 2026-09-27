import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import util  # noqa: E402


@pytest.mark.parametrize(
    "code,sitelink,mw_host,pv_project",
    [
        ("en", "enwiki", "en.wikipedia.org", "en.wikipedia"),
        ("pl", "plwiki", "pl.wikipedia.org", "pl.wikipedia"),
        ("cs", "cswiki", "cs.wikipedia.org", "cs.wikipedia"),
        ("uk", "ukwiki", "uk.wikipedia.org", "uk.wikipedia"),
        ("de", "dewiki", "de.wikipedia.org", "de.wikipedia"),
    ],
)
def test_normalize_project(code, sitelink, mw_host, pv_project):
    result = util.normalize_project(code)
    assert result["sitelink_key"] == sitelink
    assert result["mediawiki_host"] == mw_host
    assert result["pageviews_project"] == pv_project


def test_normalize_project_rejects_garbage():
    with pytest.raises(util.InvalidLanguageCode):
        util.normalize_project("not a lang code!")


def test_normalize_project_case_insensitive():
    assert util.normalize_project("EN")["sitelink_key"] == "enwiki"


def test_parse_date_valid():
    assert util.parse_date("2023-09-01") == dt.date(2023, 9, 1)


def test_parse_date_invalid():
    with pytest.raises(ValueError):
        util.parse_date("not-a-date")


def test_clamp_start_date_before_coverage():
    clamped, was_clamped = util.clamp_start_date(dt.date(2010, 1, 1))
    assert clamped == util.PAGEVIEWS_COVERAGE_START
    assert was_clamped is True


def test_clamp_start_date_within_coverage():
    d = dt.date(2023, 1, 1)
    clamped, was_clamped = util.clamp_start_date(d)
    assert clamped == d
    assert was_clamped is False


def test_validate_date_range_start_after_end():
    with pytest.raises(ValueError):
        util.validate_date_range(dt.date(2024, 1, 1), dt.date(2023, 1, 1))


def test_validate_date_range_future_end():
    with pytest.raises(ValueError):
        util.validate_date_range(dt.date(2020, 1, 1), dt.date(2099, 1, 1))


def test_validate_date_range_ok():
    util.validate_date_range(dt.date(2023, 1, 1), dt.date(2023, 6, 1))


def test_to_api_date():
    assert util.to_api_date(dt.date(2023, 9, 1)) == "20230901"


def test_choose_granularity_short_span_is_daily():
    assert util.choose_granularity(dt.date(2024, 1, 1), dt.date(2024, 3, 1)) == "daily"


def test_choose_granularity_long_span_is_monthly():
    assert util.choose_granularity(dt.date(2022, 1, 1), dt.date(2024, 1, 1)) == "monthly"


def test_run_record_roundtrip(tmp_path):
    record = {"topics": ["x"], "langs": ["en"]}
    util.write_run_record(tmp_path, record)
    loaded = util.read_latest(tmp_path)
    assert loaded == record


def test_read_latest_missing_returns_none(tmp_path):
    assert util.read_latest(tmp_path) is None


def test_snap_end_for_granularity_daily_unchanged():
    d = dt.date(2025, 9, 15)
    assert util.snap_end_for_granularity(d, "daily") == d


def test_snap_end_for_granularity_monthly_snaps_to_month_end():
    assert util.snap_end_for_granularity(dt.date(2025, 9, 1), "monthly") == dt.date(2025, 9, 30)
    assert util.snap_end_for_granularity(dt.date(2025, 9, 15), "monthly") == dt.date(2025, 9, 30)
    assert util.snap_end_for_granularity(dt.date(2025, 9, 30), "monthly") == dt.date(2025, 9, 30)


def test_snap_end_for_granularity_handles_december():
    assert util.snap_end_for_granularity(dt.date(2024, 12, 5), "monthly") == dt.date(2024, 12, 31)


def test_snap_end_for_granularity_handles_leap_february():
    assert util.snap_end_for_granularity(dt.date(2024, 2, 1), "monthly") == dt.date(2024, 2, 29)
    assert util.snap_end_for_granularity(dt.date(2023, 2, 1), "monthly") == dt.date(2023, 2, 28)


def test_run_record_handles_non_ascii(tmp_path):
    record = {"title": "Інтервальне голодування"}
    util.write_run_record(tmp_path, record)
    raw = (tmp_path / "latest.json").read_text(encoding="utf-8")
    assert "Інтервальне" in raw
    assert util.read_latest(tmp_path) == record


def test_normalize_project_hyphenated_code_uses_underscore_sitelink_key():
    # Wikidata's sitelink key for zh-yue.wikipedia is "zh_yuewiki" (confirmed
    # live); "zh-yuewiki" never matched, giving a false not_found.
    result = util.normalize_project("zh-yue")
    assert result["sitelink_key"] == "zh_yuewiki"
    assert result["mediawiki_host"] == "zh-yue.wikipedia.org"
    assert result["pageviews_project"] == "zh-yue.wikipedia"


def test_snap_start_for_granularity_monthly_goes_to_first_of_month():
    assert util.snap_start_for_granularity(dt.date(2025, 8, 15), "monthly") == dt.date(2025, 8, 1)
    assert util.snap_start_for_granularity(dt.date(2025, 8, 15), "daily") == dt.date(2025, 8, 15)


def test_last_complete_month_end():
    assert util.last_complete_month_end(dt.date(2026, 9, 25)) == dt.date(2026, 8, 31)
    assert util.last_complete_month_end(dt.date(2026, 1, 1)) == dt.date(2025, 12, 31)


def test_count_periods_daily():
    assert util.count_periods(dt.date(2024, 1, 1), dt.date(2024, 1, 1), "daily") == 1
    assert util.count_periods(dt.date(2024, 1, 1), dt.date(2024, 1, 31), "daily") == 31


def test_count_periods_monthly():
    assert util.count_periods(dt.date(2024, 1, 1), dt.date(2024, 1, 31), "monthly") == 1
    assert util.count_periods(dt.date(2024, 1, 1), dt.date(2024, 12, 31), "monthly") == 12
    assert util.count_periods(dt.date(2023, 11, 1), dt.date(2024, 2, 29), "monthly") == 4


def test_month_chunks_single_month():
    assert util.month_chunks(dt.date(2024, 1, 15), dt.date(2024, 1, 20)) == [
        (dt.date(2024, 1, 15), dt.date(2024, 1, 20))
    ]


def test_month_chunks_spans_several_months():
    chunks = util.month_chunks(dt.date(2024, 1, 15), dt.date(2024, 3, 10))
    assert chunks == [
        (dt.date(2024, 1, 15), dt.date(2024, 1, 31)),
        (dt.date(2024, 2, 1), dt.date(2024, 2, 29)),
        (dt.date(2024, 3, 1), dt.date(2024, 3, 10)),
    ]


def test_month_chunks_exact_month_boundaries():
    chunks = util.month_chunks(dt.date(2024, 1, 1), dt.date(2024, 2, 29))
    assert chunks == [
        (dt.date(2024, 1, 1), dt.date(2024, 1, 31)),
        (dt.date(2024, 2, 1), dt.date(2024, 2, 29)),
    ]


def test_month_chunks_single_day():
    assert util.month_chunks(dt.date(2024, 6, 1), dt.date(2024, 6, 1)) == [(dt.date(2024, 6, 1), dt.date(2024, 6, 1))]
