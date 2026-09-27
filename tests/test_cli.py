import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch  # noqa: E402
import pageviews_cli  # noqa: E402
import resolve  # noqa: E402
import util  # noqa: E402


def _points(n: int, start_views: int = 100, step: int = 10) -> list[dict]:
    return [{"date": f"2024-{i+1:02d}-01", "views": start_views + i * step} for i in range(n)]


def test_ambiguous_pair_halts_whole_run_before_any_fetch(monkeypatch, tmp_path):
    def fake_resolve_all(session, topics, langs, pins=None):
        return {
            "Mercury": {
                "en": {"status": "ambiguous", "candidates": [{"qid": "Q308", "label": "Mercury (planet)", "description": "planet"}]},
            }
        }

    calls = {"fetch_all": 0}

    def fake_fetch_all(*a, **k):
        calls["fetch_all"] += 1
        return {}

    monkeypatch.setattr(resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(fetch, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    result = pageviews_cli.run(["--topics", "Mercury", "--langs", "en", "--outdir", str(tmp_path)])

    assert result["status"] == "needs_disambiguation"
    assert "Mercury" in result["needs_disambiguation"]
    assert calls["fetch_all"] == 0
    assert "artifacts" not in result  # no chart/report generated for a disambiguation response
    assert not (tmp_path / "chart.png").exists()
    assert not (tmp_path / "report.pdf").exists()


def test_successful_run_produces_full_pipeline_output(monkeypatch, tmp_path):
    def fake_resolve_all(session, topics, langs, pins=None):
        return {"Intermittent fasting": {"en": {"status": "resolved", "title": "Intermittent fasting", "qid": "Q1666254", "method": "wikidata_sitelink"}}}

    def fake_fetch_all(session, cache_dir, resolution, *, granularity, start, end):
        return {"Intermittent fasting": {"en": {"status": "ok", "points": _points(8), "from_cache": False}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    result = pageviews_cli.run([
        "--topics", "Intermittent fasting",
        "--langs", "en",
        "--start", "2024-01-01",
        "--end", "2024-08-01",
        "--outdir", str(tmp_path),
    ])

    assert result["status"] == "ok"
    assert result["results"]["Intermittent fasting"]["en"]["fetch_status"] == "ok"
    assert result["results"]["Intermittent fasting"]["en"]["trend_strength"] in ("strong", "moderate", "weak", "insufficient")
    assert "sample_points" in result["results"]["Intermittent fasting"]["en"]
    assert Path(result["artifacts"]["chart"]).exists()
    assert Path(result["artifacts"]["report"]).exists()
    assert util.read_latest(tmp_path) == result


def test_single_series_run_has_no_comparison_field(monkeypatch, tmp_path):
    # Nothing to rank against -- ranking a single series is meaningless and
    # SKILL.md should not point the model at an empty/trivial comparison.
    def fake_resolve_all(session, topics, langs, pins=None):
        return {"X": {"en": {"status": "resolved", "title": "X", "qid": "Q1", "method": "wikidata_sitelink"}}}

    def fake_fetch_all(session, cache_dir, resolution, *, granularity, start, end):
        return {"X": {"en": {"status": "ok", "points": _points(8), "from_cache": False}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--start", "2024-01-01", "--end", "2024-08-01", "--outdir", str(tmp_path)])

    assert "comparison" not in result


def test_multi_language_run_produces_a_correctly_ordered_comparison(monkeypatch, tmp_path):
    # Regression test for a real Cowork failure: the model's own prose called
    # a smaller audience "the largest" and the actual largest "close behind"
    # a smaller number, because nothing in the JSON told it the order
    # directly. Confirms `comparison.by_average_views` is present, sorted
    # correctly, and usable without the model comparing metrics itself.
    def fake_resolve_all(session, topics, langs, pins=None):
        return {
            "English language": {
                lang: {"status": "resolved", "title": "English language", "qid": "Q1860", "method": "wikidata_sitelink"}
                for lang in ["pl", "de", "es", "ja"]
            }
        }

    def fake_fetch_all(session, cache_dir, resolution, *, granularity, start, end):
        # Distinct, non-monotonic-with-request-order average views per
        # language, mirroring the real pl/de/es/ja scenario (ja largest,
        # es second, de third, pl smallest) so a naive "first in, first
        # ranked" bug would be caught.
        return {
            "English language": {
                "pl": {"status": "ok", "points": _points(8, start_views=9000, step=0), "from_cache": False},
                "de": {"status": "ok", "points": _points(8, start_views=24000, step=0), "from_cache": False},
                "es": {"status": "ok", "points": _points(8, start_views=31000, step=0), "from_cache": False},
                "ja": {"status": "ok", "points": _points(8, start_views=39000, step=0), "from_cache": False},
            }
        }

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    result = pageviews_cli.run([
        "--topics", "English language", "--langs", "pl,de,es,ja",
        "--start", "2024-01-01", "--end", "2024-08-01", "--outdir", str(tmp_path),
    ])

    assert result["status"] == "ok"
    assert "comparison" in result
    langs_in_order = [e["lang"] for e in result["comparison"]["by_average_views"]]
    assert langs_in_order == ["ja", "es", "de", "pl"]
    ranks = [e["rank"] for e in result["comparison"]["by_average_views"]]
    assert ranks == [1, 2, 3, 4]
    # the largest audience's own average_views must actually be the largest number present
    top = result["comparison"]["by_average_views"][0]
    assert top["average_views"] == max(e["average_views"] for e in result["comparison"]["by_average_views"])

    # narrative sentences must be present and their text must reappear verbatim
    # in the generated PDF -- the two surfaces are built from the same source,
    # so the chat-facing sentences a model relays cannot silently drift from
    # what the PDF (fully code-generated, never wrong) actually says.
    assert "narrative" in result
    assert len(result["narrative"]["per_series"]) == 4
    assert "0 of 4 series meet the promising criteria" in result["narrative"]["summary"]

    from pypdf import PdfReader

    pdf_text = " ".join(PdfReader(result["artifacts"]["report"]).pages[0].extract_text().split())
    for sentence in result["narrative"]["per_series"]:
        # the PDF wraps lines and drops the leading "- ", so compare on
        # normalized whitespace only, not an exact substring match
        assert " ".join(sentence.split()) in pdf_text


def test_not_found_language_is_reported_as_limitation_not_dropped_silently(monkeypatch, tmp_path):
    def fake_resolve_all(session, topics, langs, pins=None):
        return {
            "X": {
                "en": {"status": "resolved", "title": "X", "qid": "Q1", "method": "wikidata_sitelink"},
                "de": {"status": "not_found"},
            }
        }

    def fake_fetch_all(session, cache_dir, resolution, *, granularity, start, end):
        return {"X": {"en": {"status": "ok", "points": _points(8), "from_cache": False}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en,de", "--start", "2024-01-01", "--end", "2024-08-01", "--outdir", str(tmp_path)])

    assert any("de" in lim and "no Wikipedia article" in lim for lim in result["limitations"])


def test_invalid_date_range_rejected_before_any_network_call(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("should not be called")

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", boom)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", boom)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--start", "2024-06-01", "--end", "2024-01-01", "--outdir", str(tmp_path)])
    assert result["status"] == "error"


def test_start_date_before_coverage_is_clamped_and_reported(monkeypatch, tmp_path):
    def fake_resolve_all(session, topics, langs, pins=None):
        return {"X": {"en": {"status": "resolved", "title": "X", "qid": None, "method": "wikidata_sitelink"}}}

    def fake_fetch_all(session, cache_dir, resolution, *, granularity, start, end):
        assert start == util.PAGEVIEWS_COVERAGE_START
        return {"X": {"en": {"status": "ok", "points": _points(8), "from_cache": False}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--start", "2010-01-01", "--end", "2024-01-01", "--outdir", str(tmp_path)])

    assert any("clamped" in lim for lim in result["limitations"])


def test_pin_parsing_error_is_reported():
    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--pin", "malformed-pin"])
    assert result["status"] == "error"


def test_oversized_request_is_rejected():
    topics = ",".join(f"topic{i}" for i in range(10))
    langs = ",".join(f"l{i}" for i in range(10))  # 100 pairs > MAX_TOPICS_TIMES_LANGS
    result = pageviews_cli.run(["--topics", topics, "--langs", langs])
    assert result["status"] == "error"
    assert "over the v1 limit" in result["detail"]


def test_invalid_criteria_reported_as_error(monkeypatch, tmp_path):
    def fake_resolve_all(session, topics, langs, pins=None):
        return {"X": {"en": {"status": "resolved", "title": "X", "qid": None, "method": "wikidata_sitelink"}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--criteria", "bogus=1", "--outdir", str(tmp_path)])
    assert result["status"] == "error"


def test_pin_is_forwarded_to_resolver(monkeypatch, tmp_path):
    captured = {}

    def fake_resolve_all(session, topics, langs, pins=None):
        captured["pins"] = pins
        return {"Mercury": {"en": {"status": "resolved", "title": "Mercury (planet)", "qid": "Q308", "method": "pinned_qid"}}}

    def fake_fetch_all(session, cache_dir, resolution, *, granularity, start, end):
        return {"Mercury": {"en": {"status": "ok", "points": _points(8), "from_cache": False}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    pageviews_cli.run(["--topics", "Mercury", "--langs", "en", "--pin", "Mercury:en=Q308", "--start", "2024-01-01", "--end", "2024-08-01", "--outdir", str(tmp_path)])

    assert captured["pins"] == {("Mercury", "en"): "Q308"}


def _capture_fetch_period(monkeypatch):
    captured = {}

    def fake_resolve_all(session, topics, langs, pins=None):
        return {"X": {"en": {"status": "resolved", "title": "X", "qid": "Q1", "method": "wikidata_sitelink"}}}

    def fake_fetch_all(session, cache_dir, resolution, *, granularity, start, end):
        captured.update(granularity=granularity, start=start, end=end)
        return {"X": {"en": {"status": "ok", "points": _points(8), "from_cache": False}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)
    return captured


def test_default_period_is_24_complete_months(monkeypatch, tmp_path):
    captured = _capture_fetch_period(monkeypatch)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--outdir", str(tmp_path)])

    start, end = captured["start"], captured["end"]
    assert captured["granularity"] == "monthly"
    assert start.day == 1
    assert end == util.last_complete_month_end()  # in-progress month excluded, not analyzed as if complete
    assert (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1 == 24
    assert result["params"]["start"] == start.isoformat()
    assert result["params"]["end"] == end.isoformat()
    assert any("in-progress month" in lim for lim in result["limitations"])


def test_monthly_period_snaps_mid_month_start_and_excludes_current_month(monkeypatch, tmp_path):
    captured = _capture_fetch_period(monkeypatch)

    pageviews_cli.run([
        "--topics", "X", "--langs", "en", "--start", "2024-01-15",
        "--end", dt.date.today().isoformat(), "--outdir", str(tmp_path),
    ])

    assert captured["start"] == dt.date(2024, 1, 1)
    assert captured["end"] == util.last_complete_month_end()


def test_default_start_is_relative_to_explicit_end(monkeypatch, tmp_path):
    captured = _capture_fetch_period(monkeypatch)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--end", "2023-12-31", "--outdir", str(tmp_path)])

    assert result["status"] == "ok"
    assert captured["start"] == dt.date(2021, 12, 1)
    assert captured["end"] == dt.date(2023, 12, 31)


def test_invalid_language_code_is_json_error_not_traceback(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("should not be called")

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", boom)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en,english", "--outdir", str(tmp_path)])

    assert result["status"] == "error"
    assert "english" in result["detail"]


def test_network_failure_during_resolution_is_json_error(monkeypatch, tmp_path):
    import requests

    def unreachable(*a, **k):
        raise requests.ConnectionError("Failed to resolve 'xq.wikipedia.org'")

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", unreachable)

    result = pageviews_cli.run(["--topics", "X", "--langs", "xq", "--start", "2024-01-01", "--end", "2024-08-01", "--outdir", str(tmp_path)])

    assert result["status"] == "error"
    assert "xq.wikipedia.org" in result["detail"]


def test_pin_not_matching_any_requested_pair_is_rejected(monkeypatch, tmp_path):
    # A case-mismatched pin used to be silently ignored, looping the agent
    # back into needs_disambiguation with no hint why.
    def boom(*a, **k):
        raise AssertionError("should not be called")

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", boom)

    result = pageviews_cli.run(["--topics", "Mercury", "--langs", "en", "--pin", "mercury:en=Q308", "--outdir", str(tmp_path)])

    assert result["status"] == "error"
    assert "mercury:en" in result["detail"]


def test_intent_adds_proxy_measure_limitation(monkeypatch, tmp_path):
    _capture_fetch_period(monkeypatch)

    result = pageviews_cli.run([
        "--topics", "X", "--langs", "en", "--intent", "learning X",
        "--start", "2024-01-01", "--end", "2024-08-31", "--outdir", str(tmp_path),
    ])

    assert result["params"]["intent"] == "learning X"
    assert any(lim.startswith("proxy measure: 'X'") and "'learning X'" in lim for lim in result["limitations"])


def test_no_proxy_limitation_without_intent(monkeypatch, tmp_path):
    _capture_fetch_period(monkeypatch)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--start", "2024-01-01", "--end", "2024-08-31", "--outdir", str(tmp_path)])

    assert result["params"]["intent"] is None
    assert not any("proxy measure" in lim for lim in result["limitations"])


def test_relay_run_without_saved_responses_returns_needs_data(tmp_path):
    # Real resolve/fetch, no network: the run must say exactly what to fetch.
    result = pageviews_cli.run([
        "--topics", "Astronomy", "--langs", "uk", "--start", "2024-01-01", "--end", "2024-08-31",
        "--outdir", str(tmp_path), "--data-source", "relay",
    ])

    assert result["status"] == "needs_data"
    [request] = result["data_requests"]
    assert request["url"].startswith("https://www.wikidata.org/w/api.php?action=wbsearchentities&search=Astronomy")
    assert Path(request["save_as"]).parent == (tmp_path / "relay").resolve()
    assert result["rerun_command"].endswith("--data-source relay")
    assert result["rerun_command"].count("--data-source") == 1
    assert "artifacts" not in result and "results" not in result
    assert util.read_latest(tmp_path)["status"] == "needs_data"


def test_title_pin_in_relay_mode_asks_only_for_the_pageviews_url(tmp_path):
    result = pageviews_cli.run([
        "--topics", "Astronomy", "--langs", "uk", "--pin", "Astronomy:uk=Астрономія",
        "--start", "2024-01-01", "--end", "2024-08-31", "--outdir", str(tmp_path), "--data-source", "relay",
    ])

    [request] = result["data_requests"]
    assert request["url"].startswith(fetch.PAGEVIEWS_BASE + "/uk.wikipedia/all-access/user/")
    assert request["url"].endswith("/monthly/20240101/20240831")


def test_relay_series_origin_is_reported_in_results_and_limitations(monkeypatch, tmp_path):
    def fake_resolve_all(source, topics, langs, pins=None):
        return {"X": {"en": {"status": "resolved", "title": "X", "qid": "Q1", "method": "wikidata_sitelink"}}}

    def fake_fetch_all(source, cache_dir, resolution, *, granularity, start, end):
        return {"X": {"en": {"status": "ok", "points": _points(8), "origin": "relay", "from_cache": False}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--start", "2024-01-01", "--end", "2024-08-31", "--outdir", str(tmp_path)])

    assert result["status"] == "ok"
    assert result["results"]["X"]["en"]["data_origin"] == "relay"
    assert any(lim.startswith("data access:") and "'X' (en)" in lim for lim in result["limitations"])


def test_direct_series_has_no_relay_limitation(monkeypatch, tmp_path):
    _capture_fetch_period(monkeypatch)

    result = pageviews_cli.run(["--topics", "X", "--langs", "en", "--start", "2024-01-01", "--end", "2024-08-31", "--outdir", str(tmp_path)])

    assert result["results"]["X"]["en"]["data_origin"] == "direct"
    assert not any(lim.startswith("data access:") for lim in result["limitations"])


def test_rerun_command_replaces_an_existing_data_source_flag():
    command = pageviews_cli._rerun_command(["--topics", "A b", "--langs", "en", "--data-source=auto"])
    assert command.endswith("--topics 'A b' --langs en --data-source relay")


def test_regression_example_largest_audience_declining_nothing_promising(monkeypatch, tmp_path):
    # Real series from references/examples.md example 4: the biggest
    # audience (en) declines steeply, the only rising one (uk) has a weak
    # fit, and all four are not_promising. The final answer is written from
    # this JSON, so the facts that make the case a trap must stay exactly as
    # the example quotes them.
    case = json.loads((Path(__file__).resolve().parent / "fixtures" / "regression_declining_largest_audience.json").read_text(encoding="utf-8"))
    topic = case["topic"]

    def fake_resolve_all(source, topics, langs, pins=None):
        return {topic: case["resolution"]}

    def fake_fetch_all(source, cache_dir, resolution, *, granularity, start, end):
        return {topic: {lang: {"status": "ok", "points": pts, "origin": "direct", "from_cache": False} for lang, pts in case["series"].items()}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)

    result = pageviews_cli.run([
        "--topics", topic, "--langs", ",".join(case["langs"]),
        "--start", case["start"], "--end", case["end"], "--outdir", str(tmp_path),
    ])
    results = result["results"][topic]

    for lang, expected in case["expected"].items():
        entry = results[lang]
        assert {k: entry["metrics"][k] for k in ("average_views", "growth_pct", "r_squared", "data_points")} == {
            k: expected[k] for k in ("average_views", "growth_pct", "r_squared", "data_points")
        }, lang
        assert (entry["trend_strength"], entry["promising"]) == (expected["trend_strength"], expected["promising"]), lang

    assert {e["promising"] for e in results.values()} == {"not_promising"}
    largest = max(results, key=lambda lang: results[lang]["metrics"]["average_views"])
    assert largest == "en" and results["en"]["metrics"]["growth_pct"] < 0
    assert [lang for lang, e in results.items() if e["metrics"]["growth_pct"] > 0] == ["uk"]
    assert results["uk"]["trend_strength"] == "weak"

    # The PDF's own interpretation states the decline and never upgrades the assessment.
    from pypdf import PdfReader

    pdf_text = " ".join(PdfReader(result["artifacts"]["report"]).pages[0].extract_text().split())
    assert (
        "'Intermittent fasting' on en.wikipedia declined by 62.8% (average views in the second half of the "
        "selected period vs. the first half; trend strength: moderate); classification: not_promising."
    ) in pdf_text
    assert "classification: promising" not in pdf_text

    # The worked example must keep quoting exactly these values.
    examples = (Path(__file__).resolve().parent.parent / "references" / "examples.md").read_text(encoding="utf-8")
    section = examples.split("## 4. ")[1].split("\n## ")[0]
    for lang, expected in case["expected"].items():
        row = next(line for line in section.splitlines() if line.startswith(f"| {lang} "))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells == [lang, str(expected["average_views"]), str(expected["growth_pct"]), str(expected["r_squared"]), expected["trend_strength"], expected["promising"]], lang


def test_unrenderable_chart_glyphs_become_a_limitation(monkeypatch, tmp_path):
    import charts

    def fake_resolve_all(session, topics, langs, pins=None):
        return {"英語": {"ja": {"status": "resolved", "title": "英語", "qid": "Q1860", "method": "wikidata_sitelink"}}}

    def fake_fetch_all(session, cache_dir, resolution, *, granularity, start, end):
        return {"英語": {"ja": {"status": "ok", "points": _points(8), "from_cache": False}}}

    monkeypatch.setattr(pageviews_cli.resolve, "resolve_all", fake_resolve_all)
    monkeypatch.setattr(pageviews_cli.fetch, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(charts, "_CJK_FALLBACK_FONTS", [])

    result = pageviews_cli.run(["--topics", "英語", "--langs", "ja", "--start", "2024-01-01", "--end", "2024-08-31", "--outdir", str(tmp_path)])

    assert any("no CJK-capable font found" in lim for lim in result["limitations"])
