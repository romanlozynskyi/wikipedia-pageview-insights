import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import charts  # noqa: E402


def _points(n: int) -> list[dict]:
    return [{"date": f"2024-{i+1:02d}-01", "views": 100 + i * 10} for i in range(n)]


def test_render_chart_creates_nonempty_png(tmp_path):
    series = {"en": _points(6), "pl": _points(6)}
    out = tmp_path / "chart.png"
    result = charts.render_chart(series, title="Test topic", output_path=out)

    assert out.exists()
    assert out.stat().st_size > 0
    assert result["series_included"] == ["en", "pl"]
    assert result["series_omitted"] == []


def test_render_chart_caps_series_and_reports_omitted(tmp_path):
    series = {f"lang{i}": _points(6) for i in range(12)}
    out = tmp_path / "chart.png"
    result = charts.render_chart(series, title="Too many languages", output_path=out)

    assert len(result["series_included"]) == charts.MAX_SERIES_PER_CHART
    assert len(result["series_omitted"]) == 12 - charts.MAX_SERIES_PER_CHART
    assert out.exists()


def test_render_chart_handles_empty_series_dict(tmp_path):
    out = tmp_path / "chart.png"
    result = charts.render_chart({}, title="Nothing to plot", output_path=out)
    assert out.exists()  # still produces a (empty/titled) chart file, never crashes
    assert result["series_included"] == []


def test_render_chart_skips_label_with_no_points(tmp_path):
    series = {"en": _points(6), "de": []}
    out = tmp_path / "chart.png"
    result = charts.render_chart(series, title="Partial data", output_path=out)
    assert out.exists()
    assert "en" in result["series_included"]
    assert "de" in result["series_included"]  # included in the legend list, just has no line drawn


def test_render_chart_reports_glyphs_no_installed_font_can_draw(tmp_path, monkeypatch):
    monkeypatch.setattr(charts, "_CJK_FALLBACK_FONTS", [])  # simulate a machine with no CJK font
    result = charts.render_chart(
        {"英語 (ja)": [{"date": "2024-01-01", "views": 1}]}, title="Wikipedia pageviews: 英語", output_path=tmp_path / "c.png"
    )
    assert result["missing_glyphs"] == ["英", "語"]


def test_render_chart_latin_and_cyrillic_have_no_missing_glyphs(tmp_path):
    result = charts.render_chart(
        {"Астрономія (uk)": [{"date": "2024-01-01", "views": 1}]}, title="Astronomy", output_path=tmp_path / "c.png"
    )
    assert result["missing_glyphs"] == []
