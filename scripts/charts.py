"""Matplotlib chart generation. One PNG per call: a line per series label.

Used both for "one topic across languages" and "one language across
topics" comparisons -- the caller decides what the labels mean.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless: no display server required
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
from matplotlib import font_manager  # noqa: E402

MAX_SERIES_PER_CHART = 8

# matplotlib's bundled DejaVu Sans has no CJK glyphs and no pip-installable
# font reliably does, so any of these common system CJK fonts that is
# installed is used as a per-glyph fallback. Characters no installed font
# covers are returned as `missing_glyphs` so the caller can say so.
_CJK_FALLBACK_FONTS = [
    "Noto Sans CJK JP", "Noto Sans CJK SC", "Source Han Sans", "Microsoft YaHei", "Yu Gothic",
    "Malgun Gothic", "SimHei", "Hiragino Sans", "PingFang SC", "Apple SD Gothic Neo",
    "WenQuanYi Zen Hei", "Droid Sans Fallback",
]


def cjk_fallback_fonts() -> list[tuple[str, str]]:
    """(family name, font file path) of each installed _CJK_FALLBACK_FONTS
    entry, in priority order. Also used by report.py to embed CJK glyphs."""
    installed = {f.name for f in font_manager.fontManager.ttflist}
    return [
        (name, font_manager.findfont(font_manager.FontProperties(family=name)))
        for name in _CJK_FALLBACK_FONTS
        if name in installed
    ]


def _font_family() -> list[str]:
    return ["DejaVu Sans"] + [name for name, _path in cjk_fallback_fonts()]


def _missing_glyphs(texts: list[str], family: list[str]) -> list[str]:
    charmaps = [
        font_manager.get_font(font_manager.findfont(font_manager.FontProperties(family=name))).get_charmap()
        for name in family
    ]
    return sorted(
        {ch for text in texts for ch in text if not ch.isspace() and not any(ord(ch) in cm for cm in charmaps)}
    )


def render_chart(
    series_by_label: dict[str, list[dict[str, Any]]],
    *,
    title: str,
    output_path: Path,
) -> dict[str, Any]:
    """series_by_label: {label: [{"date": "YYYY-MM-DD", "views": int}, ...]}.
    Returns {"path": str, "series_included": [...], "series_omitted": [...],
    "missing_glyphs": [...]} -- series_omitted is populated (never silently
    dropped without a trace) when more than MAX_SERIES_PER_CHART labels are
    passed; missing_glyphs lists characters no installed font could draw.
    """
    labels = list(series_by_label.keys())
    included = labels[:MAX_SERIES_PER_CHART]
    omitted = labels[MAX_SERIES_PER_CHART:]
    family = _font_family()

    with plt.rc_context({"font.family": family}):
        _plot(series_by_label, included, title=title, output_path=output_path)

    return {
        "path": str(output_path),
        "series_included": included,
        "series_omitted": omitted,
        "missing_glyphs": _missing_glyphs([title, *included], family),
    }


def _plot(series_by_label: dict[str, list[dict[str, Any]]], included: list[str], *, title: str, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
    for label in included:
        points = series_by_label[label]
        if not points:
            continue
        dates = [_dt.date.fromisoformat(p["date"]) for p in points]
        views = [p["views"] for p in points]
        ax.plot(dates, views, marker="o", markersize=3, linewidth=1.5, label=label)

    ax.set_title(title, fontsize=12)
    ax.set_xlabel("Date")
    ax.set_ylabel("Pageviews")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    ax.grid(True, alpha=0.3)
    if included:
        ax.legend(loc="best", fontsize=9)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
