import datetime as dt
import json
import sys
import urllib.parse
from pathlib import Path

import pytest
import responses

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import datasource  # noqa: E402
import fetch  # noqa: E402
import util  # noqa: E402


def _pageviews_url(project, article, granularity, start, end):
    encoded = urllib.parse.quote(article.replace(" ", "_"), safe="")
    return f"{fetch.PAGEVIEWS_BASE}/{project}/all-access/user/{encoded}/{granularity}/{start}/{end}"


def _direct():
    return datasource.WikimediaSource(util.new_session(), mode="direct")


def _item(timestamp, views, article="Astronomy", project="uk.wikipedia", granularity="monthly"):
    return {"project": project, "article": article, "granularity": granularity, "timestamp": timestamp, "access": "all-access", "agent": "user", "views": views}


_RELAY_KW = dict(project="uk.wikipedia", article="Astronomy", granularity="monthly", start=dt.date(2024, 1, 1), end=dt.date(2024, 3, 31))
_RELAY_URL = _pageviews_url("uk.wikipedia", "Astronomy", "monthly", "20240101", "20240331")


def _save_relay_body(source, url, body):
    path = source.relay_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def test_relay_missing_body_queues_the_exact_official_url(tmp_path):
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    result = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)

    assert result["status"] == "pending"
    [request] = source.pending.values()
    assert request["url"] == _RELAY_URL
    assert request["save_as"] == str(source.relay_path(_RELAY_URL).resolve())
    assert request["problem"] is None


def test_relay_body_is_analyzed_like_a_direct_one_and_cached_with_its_origin(tmp_path):
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    _save_relay_body(source, _RELAY_URL, {"items": [_item("2024020100", 20), _item("2024010100", 10), _item("2024030100", 30)]})

    result = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)
    again = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)

    assert result["status"] == "ok"
    assert result["origin"] == "relay"
    assert result["points"] == [{"date": "2024-01-01", "views": 10}, {"date": "2024-02-01", "views": 20}, {"date": "2024-03-01", "views": 30}]
    assert again["from_cache"] is True and again["origin"] == "relay"
    assert not source.pending


def test_relay_body_wrapped_in_tool_text_is_still_read(tmp_path):
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    body = json.dumps({"items": [_item("2024010100", 10), _item("2024020100", 20), _item("2024030100", 30)]})
    _save_relay_body(source, _RELAY_URL, f"Page text:\n{body}\n")

    assert fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)["status"] == "ok"


@pytest.mark.parametrize(
    "items, problem",
    [
        ([_item("2024010100", 10, article="Astrology")], "article"),
        ([_item("2024010100", 10, project="en.wikipedia")], "project"),
        ([{**_item("2024010100", 10), "agent": "all-agents"}], "agent"),
        ([_item("2023120100", 10)], "outside"),
        ([_item("2024010100", 10), _item("2024010100", 11)], "duplicate"),
        ([_item("2024010100", "10")], "views"),
    ],
)
def test_relay_body_that_is_not_this_series_is_re_requested_not_analyzed(tmp_path, items, problem):
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    _save_relay_body(source, _RELAY_URL, {"items": items})

    result = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)

    assert result["status"] == "pending"
    assert problem in source.pending[_RELAY_URL]["problem"]


def test_relay_body_that_is_not_json_is_re_requested(tmp_path):
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    _save_relay_body(source, _RELAY_URL, "Astronomy had about 10 views in January 2024.")

    assert fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)["status"] == "pending"
    assert "not valid JSON" in source.pending[_RELAY_URL]["problem"]


def test_relay_404_body_is_no_data(tmp_path):
    # The pageviews API's real 404 body (captured live, 2026-09-25).
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    _save_relay_body(source, _RELAY_URL, {"detail": "The date(s) you used are valid, but we either do not have data for those date(s), or the project you asked for is not loaded yet.", "method": "get", "status": 404, "title": "Not Found", "type": "about:blank"})

    result = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)

    assert result["status"] == "no_data"
    assert result["origin"] == "relay"


@responses.activate
def test_auto_mode_falls_back_to_relay_when_python_cannot_connect(tmp_path):
    # No registered response: `responses` raises ConnectionError, like a sandbox without egress.
    source = datasource.WikimediaSource(util.new_session(), mode="auto", relay_dir=tmp_path / "relay")

    result = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)

    assert result["status"] == "pending"
    assert "ConnectionError" in source.direct_failure
    assert _RELAY_URL in source.pending


@responses.activate
def test_auto_mode_treats_a_proxy_block_page_as_blocked(tmp_path):
    responses.add(responses.GET, _RELAY_URL, body="<html>Host not in allowlist</html>", status=200)
    source = datasource.WikimediaSource(util.new_session(), mode="auto", relay_dir=tmp_path / "relay")

    assert fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)["status"] == "pending"
    assert "non-JSON" in source.direct_failure


@responses.activate
def test_auto_mode_treats_a_200_json_policy_denial_page_as_blocked_not_no_data(tmp_path):
    # Regression: a proxy that returns its own HTTP 200 + valid-JSON "policy
    # denial" page in place of the real API response used to sail straight
    # through undetected (right status code, parses as JSON) and get
    # silently treated as if Wikimedia had genuinely answered with no
    # "items" -- i.e. as real, confirmed "no data" for that series, not as
    # a blocked network. It must be detected as blocked and go to relay.
    responses.add(responses.GET, _RELAY_URL, json={"error": "policy denial", "code": "EGRESS_BLOCKED"}, status=200)
    source = datasource.WikimediaSource(util.new_session(), mode="auto", relay_dir=tmp_path / "relay")

    result = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)

    assert result["status"] == "pending"  # NOT "no_data" -- this was never Wikimedia's real answer
    assert "no 'items' field" in source.direct_failure
    assert _RELAY_URL in source.pending


@responses.activate
def test_a_200_json_body_that_has_an_error_object_is_not_treated_as_blocked(tmp_path):
    # The real action APIs legitimately answer some requests with HTTP 200
    # and a genuine {"error": ...} object (e.g. a bad QID). That must still
    # be accepted as a real, if unhelpful, direct answer -- not confused
    # with a proxy denial page, which is why the check excludes bodies that
    # already carry the real API's own "error" shape.
    wikidata_url = "https://www.wikidata.org/w/api.php"
    responses.add(responses.GET, wikidata_url, json={"error": {"code": "no-such-entity"}}, status=200)
    source = datasource.WikimediaSource(util.new_session(), mode="auto", relay_dir=tmp_path / "relay")

    resp = source.get(wikidata_url, params={"action": "wbgetentities", "ids": "Q0"}, purpose="test", expect_key="entities")

    assert resp.origin == "direct"
    assert resp.body == {"error": {"code": "no-such-entity"}}
    assert source.direct_failure is None


@responses.activate
def test_auto_mode_stops_probing_the_network_after_the_first_failure(tmp_path):
    source = datasource.WikimediaSource(util.new_session(), mode="auto", relay_dir=tmp_path / "relay")
    fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)
    calls_after_first = len(responses.calls)
    fetch.fetch_series(source, tmp_path / "cache", **{**_RELAY_KW, "article": "Physics"})

    assert len(responses.calls) == calls_after_first
    assert len(source.pending) == 2


@responses.activate
def test_auto_mode_failure_after_a_direct_success_is_an_error_not_a_relay(tmp_path):
    # Direct access demonstrably works, so a later failure is about that
    # request (bad host, server trouble), and relaying it would not help.
    responses.add(responses.GET, _RELAY_URL, json={"items": [_item("2024010100", 10)]}, status=200)
    source = datasource.WikimediaSource(util.new_session(), mode="auto", relay_dir=tmp_path / "relay")
    assert fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)["origin"] == "direct"

    result = fetch.fetch_series(source, tmp_path / "cache", **{**_RELAY_KW, "article": "Physics"})

    assert result["status"] == "error"
    assert not source.pending


@responses.activate
def test_direct_mode_never_relays(tmp_path):
    result = fetch.fetch_series(_direct(), tmp_path, **_RELAY_KW)
    assert result["status"] == "error"


@responses.activate
def test_fetch_series_success(tmp_path):
    url = _pageviews_url("en.wikipedia", "Python (programming language)", "monthly", "20240101", "20240430")
    responses.add(
        responses.GET,
        url,
        json={
            "items": [
                {"project": "en.wikipedia", "article": "Python_(programming_language)", "granularity": "monthly", "timestamp": "2024010100", "access": "all-access", "agent": "user", "views": 100},
                {"project": "en.wikipedia", "article": "Python_(programming_language)", "granularity": "monthly", "timestamp": "2024020100", "access": "all-access", "agent": "user", "views": 200},
            ]
        },
        status=200,
    )

    source = _direct()
    result = fetch.fetch_series(
        source,
        tmp_path,
        project="en.wikipedia",
        article="Python (programming language)",
        granularity="monthly",
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 4, 1),
    )

    assert result["status"] == "ok"
    assert result["from_cache"] is False
    assert result["points"] == [
        {"date": "2024-01-01", "views": 100},
        {"date": "2024-02-01", "views": 200},
    ]
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_series_exact_key_cache_hit_skips_network(tmp_path):
    url = _pageviews_url("en.wikipedia", "Python", "monthly", "20240101", "20240430")
    responses.add(responses.GET, url, json={"items": []}, status=200)

    source = _direct()
    kwargs = dict(project="en.wikipedia", article="Python", granularity="monthly", start=dt.date(2024, 1, 1), end=dt.date(2024, 4, 1))

    first = fetch.fetch_series(source, tmp_path, **kwargs)
    second = fetch.fetch_series(source, tmp_path, **kwargs)

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert len(responses.calls) == 1  # second call never hit the network


@responses.activate
def test_fetch_series_different_range_is_a_cache_miss(tmp_path):
    url_a = _pageviews_url("en.wikipedia", "Python", "monthly", "20240101", "20240430")
    url_b = _pageviews_url("en.wikipedia", "Python", "monthly", "20230101", "20230430")
    responses.add(responses.GET, url_a, json={"items": []}, status=200)
    responses.add(responses.GET, url_b, json={"items": []}, status=200)

    source = _direct()
    fetch.fetch_series(source, tmp_path, project="en.wikipedia", article="Python", granularity="monthly", start=dt.date(2024, 1, 1), end=dt.date(2024, 4, 1))
    fetch.fetch_series(source, tmp_path, project="en.wikipedia", article="Python", granularity="monthly", start=dt.date(2023, 1, 1), end=dt.date(2023, 4, 1))

    assert len(responses.calls) == 2  # different date range => full re-fetch, not a delta


@responses.activate
def test_fetch_series_404_is_no_data_not_an_error(tmp_path):
    url = _pageviews_url("xx.wikipedia", "Nonexistent", "monthly", "20240101", "20240430")
    responses.add(responses.GET, url, json={"detail": "not found"}, status=404)

    source = _direct()
    result = fetch.fetch_series(source, tmp_path, project="xx.wikipedia", article="Nonexistent", granularity="monthly", start=dt.date(2024, 1, 1), end=dt.date(2024, 4, 1))

    assert result["status"] == "no_data"


@responses.activate
def test_fetch_series_404_is_cached_too(tmp_path):
    url = _pageviews_url("xx.wikipedia", "Nonexistent", "monthly", "20240101", "20240430")
    responses.add(responses.GET, url, json={"detail": "not found"}, status=404)

    source = _direct()
    kwargs = dict(project="xx.wikipedia", article="Nonexistent", granularity="monthly", start=dt.date(2024, 1, 1), end=dt.date(2024, 4, 1))
    fetch.fetch_series(source, tmp_path, **kwargs)
    result2 = fetch.fetch_series(source, tmp_path, **kwargs)

    assert result2["status"] == "no_data"
    assert result2["from_cache"] is True
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_series_retries_on_429_then_succeeds(tmp_path):
    url = _pageviews_url("en.wikipedia", "Python", "monthly", "20240101", "20240430")
    responses.add(responses.GET, url, json={"detail": "rate limited"}, status=429)
    responses.add(responses.GET, url, json={"items": []}, status=200)

    source = _direct()
    result = fetch.fetch_series(
        source, tmp_path, project="en.wikipedia", article="Python", granularity="monthly",
        start=dt.date(2024, 1, 1), end=dt.date(2024, 4, 1),
    )

    assert result["status"] == "ok"
    assert len(responses.calls) == 2


@responses.activate
def test_fetch_series_server_error_surfaces_as_error(tmp_path):
    url = _pageviews_url("en.wikipedia", "Python", "monthly", "20240101", "20240430")
    responses.add(responses.GET, url, json={"detail": "boom"}, status=500)
    responses.add(responses.GET, url, json={"detail": "boom"}, status=500)
    responses.add(responses.GET, url, json={"detail": "boom"}, status=500)

    source = _direct()
    result = fetch.fetch_series(
        source, tmp_path, project="en.wikipedia", article="Python", granularity="monthly",
        start=dt.date(2024, 1, 1), end=dt.date(2024, 4, 1),
    )

    assert result["status"] == "error"


@responses.activate
def test_fetch_series_monthly_request_snaps_end_to_month_boundary(tmp_path):
    # Requesting end=2025-09-01 (the 1st) must actually query through
    # 2025-09-30 so the final month isn't truncated by the API's own
    # partial-bucket behavior (confirmed live, see util.snap_end_for_granularity).
    url = _pageviews_url("en.wikipedia", "Python", "monthly", "20250801", "20250930")
    responses.add(responses.GET, url, json={"items": []}, status=200)

    source = _direct()
    result = fetch.fetch_series(
        source, tmp_path, project="en.wikipedia", article="Python", granularity="monthly",
        start=dt.date(2025, 8, 1), end=dt.date(2025, 9, 1),
    )

    assert result["status"] == "ok"
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == url


@responses.activate
def test_fetch_series_daily_request_does_not_snap_end(tmp_path):
    url = _pageviews_url("en.wikipedia", "Python", "daily", "20250901", "20250901")
    responses.add(responses.GET, url, json={"items": []}, status=200)

    source = _direct()
    fetch.fetch_series(
        source, tmp_path, project="en.wikipedia", article="Python", granularity="daily",
        start=dt.date(2025, 9, 1), end=dt.date(2025, 9, 1),
    )
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_all_skips_unresolved_pairs(tmp_path):
    url = _pageviews_url("en.wikipedia", "Python", "monthly", "20240101", "20240430")
    responses.add(responses.GET, url, json={"items": []}, status=200)

    resolution = {
        "Python": {
            "en": {"status": "resolved", "title": "Python", "qid": None, "method": "test"},
            "de": {"status": "not_found"},
        }
    }
    source = _direct()
    results = fetch.fetch_all(source, tmp_path, resolution, granularity="monthly", start=dt.date(2024, 1, 1), end=dt.date(2024, 4, 1))

    assert results["Python"]["en"]["status"] == "ok"
    assert "de" not in results["Python"]  # never fetched -- no article to fetch
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_series_monthly_request_snaps_start_to_month_boundary(tmp_path):
    # A mid-month start makes the API return a partial first month under the
    # full month's label (confirmed live: 15651 vs 27178 for 2025-08).
    url = _pageviews_url("en.wikipedia", "Python", "monthly", "20250801", "20250930")
    responses.add(responses.GET, url, json={"items": []}, status=200)

    source = _direct()
    fetch.fetch_series(
        source, tmp_path, project="en.wikipedia", article="Python", granularity="monthly",
        start=dt.date(2025, 8, 15), end=dt.date(2025, 9, 30),
    )

    assert responses.calls[0].request.url == url


@responses.activate
def test_fetch_series_is_not_cached_when_period_is_still_in_progress(tmp_path):
    today = dt.date.today()
    start = today - dt.timedelta(days=3)
    url = _pageviews_url("en.wikipedia", "Python", "daily", util.to_api_date(start), util.to_api_date(today))
    responses.add(responses.GET, url, json={"items": []}, status=200)

    source = _direct()
    kwargs = dict(project="en.wikipedia", article="Python", granularity="daily", start=start, end=today)
    fetch.fetch_series(source, tmp_path, **kwargs)
    second = fetch.fetch_series(source, tmp_path, **kwargs)

    assert second["from_cache"] is False
    assert len(responses.calls) == 2  # today's views are still accumulating; never serve them stale


# --- Regression coverage: relay must still complete with real data when
# Wikimedia access is blocked, even when driven by a cheap model whose own
# web-fetch tool can silently truncate a large response. See fetch.py's
# module docstring and references/api-notes.md.


def test_relay_body_missing_items_is_rejected_as_truncated_not_analyzed(tmp_path):
    # Every remaining item is individually valid (right project/article/
    # granularity, right period) -- only the count is short. A per-item
    # check alone would accept this as a genuine (but incomplete) series.
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    _save_relay_body(source, _RELAY_URL, {"items": [_item("2024010100", 10), _item("2024020100", 20)]})  # 2 of 3 months

    result = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)

    assert result["status"] == "pending"
    assert "expected 3 items" in source.pending[_RELAY_URL]["problem"]
    assert "got 2" in source.pending[_RELAY_URL]["problem"]


def test_relay_body_missing_one_of_several_months_is_rejected(tmp_path):
    # 2 of 3 months present, all individually valid and unique -- still
    # short of the period, so still rejected.
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    _save_relay_body(source, _RELAY_URL, {"items": [_item("2024010100", 10), _item("2024030100", 30)]})

    result = fetch.fetch_series(source, tmp_path / "cache", **_RELAY_KW)

    assert result["status"] == "pending"
    assert "expected 3 items" in source.pending[_RELAY_URL]["problem"]
    assert "got 2" in source.pending[_RELAY_URL]["problem"]


_DAILY_LONG_KW = dict(project="en.wikipedia", article="Astronomy", granularity="daily", start=dt.date(2024, 1, 1), end=dt.date(2024, 3, 10))


def test_long_daily_relay_request_is_split_into_monthly_chunks(tmp_path):
    # 70 days of daily data (Jan 1 - Mar 10) is well past the verbatim-copy
    # safety threshold, so it must be queued as three small monthly-aligned
    # requests, never as one big one.
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")

    result = fetch.fetch_series(source, tmp_path / "cache", **_DAILY_LONG_KW)

    assert result["status"] == "pending"
    urls = set(source.pending.keys())
    assert _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240101", "20240131") in urls
    assert _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240201", "20240229") in urls
    assert _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240301", "20240310") in urls
    assert len(urls) == 3
    # the full, unchunked range must never itself be requested
    assert _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240101", "20240310") not in urls


def test_long_daily_relay_request_completes_and_merges_once_every_chunk_is_saved(tmp_path):
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    fetch.fetch_series(source, tmp_path / "cache", **_DAILY_LONG_KW)  # queues the 3 chunk URLs

    def _daily_items(year_month_days, views_start):
        return [_item(f"2024{ymd}00", views_start + i, article="Astronomy", project="en.wikipedia", granularity="daily") for i, ymd in enumerate(year_month_days)]

    jan_days = [f"01{d:02d}" for d in range(1, 32)]
    feb_days = [f"02{d:02d}" for d in range(1, 30)]
    mar_days = [f"03{d:02d}" for d in range(1, 11)]
    _save_relay_body(source, _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240101", "20240131"), {"items": _daily_items(jan_days, 100)})
    _save_relay_body(source, _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240201", "20240229"), {"items": _daily_items(feb_days, 200)})
    _save_relay_body(source, _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240301", "20240310"), {"items": _daily_items(mar_days, 300)})

    result = fetch.fetch_series(source, tmp_path / "cache", **_DAILY_LONG_KW)

    assert result["status"] == "ok"
    assert result["origin"] == "relay"
    assert len(result["points"]) == 31 + 29 + 10  # every day of Jan, Feb (2024 is a leap year), and 10 days of March
    assert result["points"][0] == {"date": "2024-01-01", "views": 100}
    assert result["points"][-1] == {"date": "2024-03-10", "views": 309}
    dates = [p["date"] for p in result["points"]]
    assert dates == sorted(dates)  # merged in chronological order across chunks


def test_long_daily_relay_request_stays_pending_if_only_some_chunks_are_saved(tmp_path):
    relay_dir = tmp_path / "relay"
    source1 = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=relay_dir)
    fetch.fetch_series(source1, tmp_path / "cache", **_DAILY_LONG_KW)
    _save_relay_body(
        source1, _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240101", "20240131"),
        {"items": [_item(f"202401{d:02d}00", 100 + d, article="Astronomy", project="en.wikipedia", granularity="daily") for d in range(1, 32)]},
    )

    # A fresh source, as a real re-run (a new CLI process) would use -- the
    # previous round's `pending` dict does not carry over.
    source2 = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=relay_dir)
    result = fetch.fetch_series(source2, tmp_path / "cache", **_DAILY_LONG_KW)

    assert result["status"] == "pending"  # Feb and Mar chunks are still missing
    assert len(source2.pending) == 2


@responses.activate
def test_short_daily_relay_request_is_not_chunked(tmp_path):
    # 10 days is under the chunking threshold -- one request, not three.
    source = datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")
    kw = dict(project="en.wikipedia", article="Astronomy", granularity="daily", start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 10))

    result = fetch.fetch_series(source, tmp_path / "cache", **kw)

    assert result["status"] == "pending"
    assert list(source.pending.keys()) == [_pageviews_url("en.wikipedia", "Astronomy", "daily", "20240101", "20240110")]


@responses.activate
def test_direct_mode_never_chunks_a_long_daily_request(tmp_path):
    # Python can copy bytes exactly -- chunking is a relay-only protection
    # and must never change what a direct request looks like.
    url = _pageviews_url("en.wikipedia", "Astronomy", "daily", "20240101", "20240310")
    responses.add(responses.GET, url, json={"items": []}, status=200)
    source = _direct()

    result = fetch.fetch_series(source, tmp_path / "cache", **_DAILY_LONG_KW)

    assert result["status"] == "ok"
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == url


@responses.activate
def test_auto_mode_chunks_a_long_daily_request_once_it_falls_back_to_relay(tmp_path):
    # In the real CLI, resolve.py always makes at least one Wikidata request
    # before fetch.py runs, so by the time a pageviews fetch happens, auto
    # mode already knows whether direct access works. Simulate that here
    # with an earlier failed call (no registered response -> ConnectionError,
    # same as a sandbox without egress) before the request under test.
    source = datasource.WikimediaSource(util.new_session(), mode="auto", relay_dir=tmp_path / "relay")
    fetch.fetch_series(source, tmp_path / "cache", project="en.wikipedia", article="Physics", granularity="monthly", start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 31))
    assert source.direct_failure is not None

    result = fetch.fetch_series(source, tmp_path / "cache", **_DAILY_LONG_KW)

    assert result["status"] == "pending"
    assert sum(1 for url in source.pending if "Astronomy" in url) == 3
