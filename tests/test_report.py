import re
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import analyze  # noqa: E402
import charts  # noqa: E402
import report  # noqa: E402


def _sample_run_result() -> dict:
    points = [{"date": f"2024-{i+1:02d}-01", "views": 100 + i * 15} for i in range(8)]
    analysis = analyze.analyze_series(points)
    promising = analyze.classify_promising(analysis, analyze.DEFAULT_CRITERIA)
    return {
        "params": {
            "topics": ["Intermittent fasting"],
            "langs": ["en", "cs"],
            "start": "2024-01-01",
            "end": "2024-08-01",
            "granularity": "monthly",
        },
        "results": {
            "Intermittent fasting": {
                "en": {
                    "fetch_status": "ok",
                    "metrics": analysis["metrics"],
                    "trend_strength": analysis["trend_strength"],
                    "promising": promising,
                },
                "cs": {
                    "fetch_status": "ok",
                    "metrics": analysis["metrics"],
                    "trend_strength": analysis["trend_strength"],
                    "promising": promising,
                },
            }
        },
        "limitations": ["pl: no article found for this topic on pl.wikipedia"],
    }


def test_generate_report_produces_single_page_pdf_without_notes(tmp_path):
    run_result = _sample_run_result()
    out = tmp_path / "report.pdf"

    result_path = report.generate_report(run_result, chart_path=None, output_path=out, notes=None)

    assert result_path == out
    assert out.exists()
    assert out.stat().st_size > 0

    reader = PdfReader(str(out))
    assert len(reader.pages) == 1

    text = reader.pages[0].extract_text()
    assert "Intermittent fasting" in text
    assert "en" in text
    assert "Wikimedia Pageviews API" in text
    assert "proxy for public interest" in text


def test_generate_report_includes_limitations():
    run_result = _sample_run_result()
    assert "no article found" in run_result["limitations"][0]


def test_generate_report_with_chart_image(tmp_path):
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import charts  # noqa: E402

    chart_out = tmp_path / "chart.png"
    charts.render_chart(
        {"en": [{"date": "2024-01-01", "views": 100}, {"date": "2024-02-01", "views": 150}]},
        title="Test",
        output_path=chart_out,
    )

    run_result = _sample_run_result()
    pdf_out = tmp_path / "report_with_chart.pdf"
    report.generate_report(run_result, chart_path=chart_out, output_path=pdf_out, notes=None)

    reader = PdfReader(str(pdf_out))
    assert len(reader.pages) == 1
    assert pdf_out.stat().st_size > 0


def test_generate_report_handles_no_usable_data(tmp_path):
    run_result = {
        "params": {"topics": ["Ghost topic"], "langs": ["en"], "start": "2024-01-01", "end": "2024-02-01", "granularity": "monthly"},
        "results": {"Ghost topic": {"en": {"fetch_status": "no_data"}}},
        "limitations": ["en: no pageview data available for this article/date range"],
    }
    out = tmp_path / "empty_report.pdf"
    report.generate_report(run_result, chart_path=None, output_path=out, notes=None)

    reader = PdfReader(str(out))
    text = reader.pages[0].extract_text()
    assert "No series had usable pageview data" in text


def test_generate_report_truncates_when_more_rows_than_fit_on_one_page(tmp_path):
    # Never previously tested: MAX_TABLE_ROWS (14) truncation path. Build a
    # run_result with 20 topic/lang rows -- more than fit -- and confirm the
    # PDF still renders as exactly one page and says something was omitted.
    points = [{"date": f"2024-{i+1:02d}-01", "views": 100 + i * 10} for i in range(8)]
    analysis = analyze.analyze_series(points)
    promising = analyze.classify_promising(analysis, analyze.DEFAULT_CRITERIA)
    topics = [f"Topic {i}" for i in range(20)]
    run_result = {
        "params": {"topics": topics, "langs": ["en"], "start": "2024-01-01", "end": "2024-08-01", "granularity": "monthly"},
        "results": {
            topic: {"en": {"fetch_status": "ok", "metrics": analysis["metrics"], "trend_strength": analysis["trend_strength"], "promising": promising}}
            for topic in topics
        },
        "limitations": [],
    }
    out = tmp_path / "report_truncated.pdf"
    report.generate_report(run_result, chart_path=None, output_path=out, notes=None)

    reader = PdfReader(str(out))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "more series in the full JSON output" in text
    # every row shown in the table must be one of the 20 topics
    assert "Topic 0" in text


def test_interpretation_says_every_percentage_compares_second_half_with_first_half(tmp_path):
    # Regression: the PDF said "declined by 62.8%", which reads as a
    # start-to-end change. growth_pct compares the average of the second half
    # of the period with the first half, and every percentage must say so.
    def entry(growth_pct):
        metrics = {"data_points": 24, "average_views": 1000.0, "growth_pct": growth_pct}
        return {"fetch_status": "ok", "metrics": metrics, "trend_strength": "moderate", "promising": "not_promising"}

    run_result = {
        "params": {"topics": ["Up", "Down", "Flat start"], "langs": ["en"], "start": "2023-09-01", "end": "2025-09-30", "granularity": "monthly"},
        "results": {"Up": {"en": entry(22.14)}, "Down": {"en": entry(-62.82)}, "Flat start": {"en": entry(None)}},
        "limitations": [],
    }
    out = tmp_path / "report.pdf"
    report.generate_report(run_result, chart_path=None, output_path=out, notes=None)

    text = " ".join(PdfReader(str(out)).pages[0].extract_text().split())
    interpretation = text[text.index("Interpretation") :]
    comparison = "(average views in the second half of the selected period vs. the first half; trend strength: moderate)"

    # The metric is printed unchanged (rounded to one decimal), with its comparison.
    assert f"'Up' on en.wikipedia grew by 22.1% {comparison}; classification: not_promising." in interpretation
    assert f"'Down' on en.wikipedia declined by 62.8% {comparison}; classification: not_promising." in interpretation
    # No percentage anywhere in the interpretation without the comparison right after it.
    percentages = re.findall(r"\d+(?:\.\d+)?%", interpretation)
    assert len(percentages) == 2
    assert len(re.findall(r"\d+(?:\.\d+)?% \(average views in the second half of the selected period vs\. the first half;", interpretation)) == 2
    # A series without a growth figure says why, in the same terms.
    assert "'Flat start' on en.wikipedia could not be measured (the change compares the second half of the selected period with the first half" in interpretation


def test_generate_report_limitations_appear_before_interpretation():
    # CASE.md requires communicating limitations before making recommendations.
    run_result = _sample_run_result()
    run_result["limitations"] = ["SPECIFIC_MARKER_LIMITATION"]
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as d:
        out = Path(d) / "order.pdf"
        report.generate_report(run_result, chart_path=None, output_path=out, notes=None)
        reader = PdfReader(str(out))
        text = reader.pages[0].extract_text()
        assert text.index("SPECIFIC_MARKER_LIMITATION") < text.index("Interpretation")


def test_generate_report_with_optional_notes_appended(tmp_path):
    run_result = _sample_run_result()
    out = tmp_path / "report_notes.pdf"
    report.generate_report(run_result, chart_path=None, output_path=out, notes="Founder is prioritizing EU markets.")

    reader = PdfReader(str(out))
    text = reader.pages[0].extract_text()
    assert "Founder is prioritizing EU markets" in text


def test_generate_report_renders_non_latin_text(tmp_path):
    # Built-in Helvetica rendered Cyrillic topics/notes as black boxes.
    run_result = _sample_run_result()
    run_result["params"]["topics"] = ["Астрономія"]
    run_result["results"] = {"Астрономія": run_result["results"]["Intermittent fasting"]}
    out = tmp_path / "report_uk.pdf"
    report.generate_report(run_result, chart_path=None, output_path=out, notes="Ринок: Україна")

    text = PdfReader(str(out)).pages[0].extract_text()
    assert "Астрономія" in text
    assert "Ринок: Україна" in text


def _render_cjk_report(tmp_path):
    run_result = _sample_run_result()
    run_result["params"]["topics"] = ["英語", "천문학"]
    base = run_result["results"]["Intermittent fasting"]
    run_result["results"] = {"英語": {"ja": base["en"]}, "천문학": {"ko": base["cs"]}}
    out = tmp_path / "report_cjk.pdf"
    report.generate_report(run_result, chart_path=None, output_path=out, notes="市場: 日本")

    page = PdfReader(str(out)).pages[0]
    text = page.extract_text().replace("\n", "")  # pypdf breaks lines at each font switch
    assert "英語" in text and "천문학" in text and "市場: 日本" in text
    return {str(f["/BaseFont"]) for f in page["/Resources"]["/Font"].values()}


@pytest.mark.skipif(not charts.cjk_fallback_fonts(), reason="no CJK system font installed")
def test_generate_report_embeds_installed_cjk_font(tmp_path):
    # DejaVu Sans has no CJK glyphs; with a CJK system font installed its
    # glyphs are embedded, so the text displays in any viewer.
    fonts = _render_cjk_report(tmp_path)
    assert any("+" in f and "DejaVu" not in f for f in fonts)  # an embedded subset of a system CJK font
    assert not any("STSong" in f or "HeiseiKakuGo" in f or "HYGothic" in f for f in fonts)


def test_generate_report_without_cjk_font_falls_back_to_cid_fonts(tmp_path, monkeypatch):
    monkeypatch.setattr(charts, "_CJK_FALLBACK_FONTS", [])  # simulate a machine with no CJK font
    fonts = _render_cjk_report(tmp_path)
    assert any("HeiseiKakuGo" in f or "STSong" in f for f in fonts)
    assert any("HYGothic" in f for f in fonts)


def test_cjk_runs_split_by_script(monkeypatch):
    monkeypatch.setattr(report, "_embedded_fallbacks", [])
    assert report._runs("Topic 英語 x", report.FONT) == [
        (report.FONT, "Topic "), (report._CJK_FONT_ZH, "英語"), (report.FONT, " x"),
    ]
    assert report._runs("えいご", report.FONT) == [(report._CJK_FONT_JA, "えいご")]
