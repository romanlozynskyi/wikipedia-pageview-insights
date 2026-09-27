"""One-page PDF report, fully generated from structured data.

The report never requires (or accepts as a source of numbers) LLM-authored
prose: every sentence is built from a template filled in with values taken
directly from the analysis JSON. An optional short `notes` string may be
appended verbatim at the bottom as a labeled "additional context from the
requester" block, but the report is complete and correct without it.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from matplotlib import get_data_path
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

import analyze
import charts

PAGE_W, PAGE_H = LETTER
MARGIN = 42
MAX_TABLE_ROWS = 14

# reportlab's built-in Helvetica has no Cyrillic/Greek glyphs (non-Latin
# topic names and notes rendered as black boxes). DejaVu Sans ships with
# matplotlib, already a dependency, and covers Latin, Cyrillic and Greek.
_FONT_DIR = Path(get_data_path()) / "fonts" / "ttf"
FONT, FONT_BOLD, FONT_ITALIC = "DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique"
for _name in (FONT, FONT_BOLD, FONT_ITALIC):
    if _name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_name, str(_FONT_DIR / f"{_name}.ttf")))

# DejaVu has no CJK glyphs. Characters it lacks are drawn with the first
# installed system CJK font that has them (the same list the chart uses,
# see charts.cjk_fallback_fonts), embedded as a subset so any viewer shows
# them. Only if no installed font covers a CJK character does it fall back
# to reportlab's built-in, non-embedded Adobe CID fonts: the text stays
# correct and extractable, but display then depends on the viewer, and the
# CLI reports that case as a limitation (charts' missing_glyphs).
_CJK_FONT_ZH, _CJK_FONT_JA, _CJK_FONT_KO = "STSong-Light", "HeiseiKakuGo-W5", "HYGothic-Medium"
for _name in (_CJK_FONT_ZH, _CJK_FONT_JA, _CJK_FONT_KO):
    if _name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(_name))

_embedded_fallbacks: list[str] = []  # set per report by _load_embedded_fallbacks()


def _load_embedded_fallbacks() -> list[str]:
    names = []
    for family, path in charts.cjk_fallback_fonts():
        name = f"Fallback-{family.replace(' ', '')}"
        if name not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
            except Exception:  # e.g. CFF/PostScript outlines or a no-embedding licence
                continue
        names.append(name)
    return names


def _covers(font: str, ch: str) -> bool:
    return ord(ch) in pdfmetrics.getFont(font).face.charToGlyph
_CJK_RANGES = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x2E80, 0x9FFF),  # CJK radicals, punctuation, kana, Hangul compat jamo, ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF00, 0xFFEF),  # full-width forms
)


def _is_cjk(ch: str) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES)


def _cjk_font_for(text: str) -> str:
    if any(0xAC00 <= ord(ch) <= 0xD7AF or 0x1100 <= ord(ch) <= 0x11FF for ch in text):
        return _CJK_FONT_KO
    if any(0x3040 <= ord(ch) <= 0x30FF for ch in text):
        return _CJK_FONT_JA
    return _CJK_FONT_ZH


def _font_for_char(ch: str, font: str, text: str) -> str:
    if _covers(font, ch):
        return font
    for fallback in _embedded_fallbacks:
        if _covers(fallback, ch):
            return fallback
    return _cjk_font_for(text) if _is_cjk(ch) else font


def _runs(text: str, font: str) -> list[tuple[str, str]]:
    """Split text into (font, substring) runs, each drawn with a font that has its glyphs."""
    runs: list[list[str]] = []
    for ch in text:
        run_font = _font_for_char(ch, font, text)
        if runs and runs[-1][0] == run_font:
            runs[-1][1] += ch
        else:
            runs.append([run_font, ch])
    return [(f, t) for f, t in runs]


def _text_width(text: str, font: str, size: float) -> float:
    return sum(pdfmetrics.stringWidth(t, f, size) for f, t in _runs(text, font))


def _draw_text(c: canvas.Canvas, x: float, y: float, text: str, font: str, size: float) -> None:
    for run_font, run_text in _runs(text, font):
        c.setFont(run_font, size)
        c.drawString(x, y, run_text)
        x += pdfmetrics.stringWidth(run_text, run_font, size)
    c.setFont(font, size)


def _truncate_to_width(text: str, font: str, size: float, max_width: float) -> str:
    while len(text) > 1 and _text_width(text, font, size) > max_width:
        text = text[:-1]
    return text


def _wrap(text: str, font: str, size: float, max_width: float) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        if _text_width(candidate, font, size) <= max_width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = word
        while len(line) > 1 and _text_width(line, font, size) > max_width:
            # hard-break over-long words, e.g. CJK text written without spaces
            head = _truncate_to_width(line, font, size, max_width)
            lines.append(head)
            line = line[len(head):]
    if line:
        lines.append(line)
    return lines

def _draw_wrapped_text(c: canvas.Canvas, text: str, x: float, y: float, *, font: str, size: float, leading: float) -> float:
    for line in _wrap(text, font, size, PAGE_W - x - MARGIN):
        _draw_text(c, x, y, line, font, size)
        y -= leading
    return y


def generate_report(
    run_result: dict[str, Any],
    *,
    chart_path: Path | None,
    output_path: Path,
    notes: str | None = None,
) -> Path:
    params = run_result["params"]
    results = run_result["results"]
    limitations = run_result.get("limitations", [])
    rows = analyze.flatten_results(results)
    _embedded_fallbacks[:] = _load_embedded_fallbacks()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=LETTER)
    y = PAGE_H - MARGIN

    # Title
    topics_str = ", ".join(params["topics"])
    langs_str = ", ".join(params["langs"])
    c.setFont(FONT_BOLD, 15)
    c.drawString(MARGIN, y, "Wikipedia Pageview Interest Report")
    y -= 20

    c.setFont(FONT, 9)
    c.setFillGray(0.35)
    recap = f"Topics: {topics_str}  |  Languages: {langs_str}  |  Period: {params['start']} to {params['end']} ({params['granularity']})"
    y = _draw_wrapped_text(c, recap, MARGIN, y, font=FONT, size=9, leading=11)
    c.setFillGray(0)
    y -= 6

    # Chart
    if chart_path is not None and Path(chart_path).exists():
        img = ImageReader(str(chart_path))
        img_w, img_h = img.getSize()
        draw_w = PAGE_W - 2 * MARGIN
        draw_h = draw_w * img_h / img_w
        max_chart_h = 230
        if draw_h > max_chart_h:
            draw_h = max_chart_h
            draw_w = draw_h * img_w / img_h
        c.drawImage(img, MARGIN, y - draw_h, width=draw_w, height=draw_h, preserveAspectRatio=True)
        y -= draw_h + 14

    # Metrics table (measured data)
    c.setFont(FONT_BOLD, 10)
    c.drawString(MARGIN, y, "Measured data")
    y -= 14
    headers = ["Topic", "Lang", "Pts", "Avg views", "Growth %", "Trend", "Assessment"]
    col_x = [MARGIN, MARGIN + 150, MARGIN + 185, MARGIN + 215, MARGIN + 280, MARGIN + 340, MARGIN + 410]
    c.setFont(FONT_BOLD, 8)
    for header, x in zip(headers, col_x):
        c.drawString(x, y, header)
    y -= 11
    c.setFont(FONT, 8)

    truncated = False
    display_rows = rows[:MAX_TABLE_ROWS]
    if len(rows) > MAX_TABLE_ROWS:
        truncated = True

    for row in display_rows:
        growth_str = "n/a" if row["growth_pct"] is None else f"{row['growth_pct']:+.1f}"
        values = [
            _truncate_to_width(row["topic"], FONT, 8, col_x[1] - col_x[0] - 6),
            row["lang"],
            str(row["data_points"]),
            f"{row['average_views']:.0f}",
            growth_str,
            row["trend_strength"],
            row["promising"],
        ]
        for value, x in zip(values, col_x):
            _draw_text(c, x, y, str(value), FONT, 8)
        y -= 11
        if y < 140:  # reserve room for narrative + limitations
            truncated = True
            break

    y -= 8

    # Limitations come BEFORE the interpretation/recommendation, by design:
    # the case requires communicating data limitations before making
    # recommendations, not after -- read these, then read what they mean below.
    c.setFont(FONT_BOLD, 10)
    c.drawString(MARGIN, y, "Assumptions and limitations (read before the interpretation below)")
    y -= 13
    c.setFont(FONT, 8)
    all_limitations = list(limitations)
    all_limitations.append(
        "Pageviews are a proxy for public interest, not evidence of willingness to pay; "
        "'trend strength' is a heuristic fit-quality score (R² + data span), not a statistical significance test."
    )
    for lim in all_limitations:
        y = _draw_wrapped_text(c, f"- {lim}", MARGIN, y, font=FONT, size=8, leading=10)
        if y < 90:
            break

    y -= 6

    # Narrative (interpretation/recommendation, clearly separated from and
    # placed after the measured data and limitations above)
    c.setFont(FONT_BOLD, 10)
    c.drawString(MARGIN, y, "Interpretation")
    y -= 14
    sentences = []
    for row in display_rows:
        phrase = analyze.trend_phrase(row["growth_pct"], row["trend_strength"])
        sentences.append(
            f"- '{row['topic']}' on {row['lang']}.wikipedia {phrase}; classification: {row['promising']}."
        )
    if truncated:
        sentences.append(f"(+{len(rows) - len(display_rows)} more series in the full JSON output, not shown here.)")
    if not sentences:
        sentences = ["No series had usable pageview data for the requested parameters."]

    for sentence in sentences:
        y = _draw_wrapped_text(c, sentence, MARGIN, y, font=FONT, size=9, leading=11)
        if y < 50:
            break

    if notes:
        y -= 8
        c.setFont(FONT_ITALIC, 8)
        y = _draw_wrapped_text(c, f"Additional context from requester: {notes}", MARGIN, y, font=FONT_ITALIC, size=8, leading=10)

    c.setFont(FONT, 7)
    c.setFillGray(0.5)
    c.drawString(MARGIN, 30, f"Source: Wikimedia Pageviews API. Generated {_dt.date.today().isoformat()}.")

    c.showPage()
    c.save()
    return output_path
