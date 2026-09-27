"""Shared helpers: Wikimedia project-id normalization, HTTP session, dates, run-record I/O.

This module is the single source of truth for turning a user-given language
code into the three different strings Wikidata / MediaWiki / the pageviews
REST API each expect for "which language edition" (see references/api-notes.md).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
from pathlib import Path
from typing import Any

import requests

USER_AGENT = (
    "wikipedia-pageview-insights/0.1 "
    "(Agent Skill case study; contact: lozunskiyr@gmail.com)"
)

PAGEVIEWS_COVERAGE_START = _dt.date(2015, 7, 1)

_LANG_CODE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]+)?$")


class InvalidLanguageCode(ValueError):
    pass


def normalize_project(lang_code: str) -> dict[str, str]:
    """Derive the three Wikimedia-family identifiers for one language code.

    - sitelink_key: Wikidata sitelinks key, e.g. "plwiki"
    - mediawiki_host: MediaWiki action API host, e.g. "pl.wikipedia.org"
    - pageviews_project: pageviews REST API {project} path segment, e.g. "pl.wikipedia"

    Confirmed live against the Wikimedia pageviews REST API (2026-09-22): both
    "en.wikipedia" and "en.wikipedia.org" return identical 200 results, so the
    shorter documented-canonical form is used and no alternate-form retry is
    needed (see references/api-notes.md).
    """
    code = lang_code.strip().lower()
    if not _LANG_CODE_RE.match(code):
        raise InvalidLanguageCode(f"'{lang_code}' does not look like a language code")
    return {
        "lang_code": code,
        # Wikidata spells hyphenated codes with underscores (confirmed live:
        # zh-yue -> "zh_yuewiki"), while hosts/projects keep the hyphen.
        "sitelink_key": f"{code.replace('-', '_')}wiki",
        "mediawiki_host": f"{code}.wikipedia.org",
        "pageviews_project": f"{code}.wikipedia",
    }


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
    timeout: float = 15.0,
) -> requests.Response:
    """GET with bounded exponential backoff on 429/5xx and network errors.

    Never retries on 404 (that's a real "no data" answer, not a transient
    failure) or other 4xx.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = session.get(url, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            resp = None
        if resp is not None:
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = RuntimeError(f"HTTP {resp.status_code} from {url}")
            else:
                return resp
        if attempt < max_attempts - 1:
            time.sleep(backoff_seconds * (2**attempt))
    assert last_exc is not None
    raise last_exc


def parse_date(value: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"'{value}' is not a valid YYYY-MM-DD date") from exc


def clamp_start_date(start: _dt.date) -> tuple[_dt.date, bool]:
    """Return (possibly-clamped start date, whether clamping happened)."""
    if start < PAGEVIEWS_COVERAGE_START:
        return PAGEVIEWS_COVERAGE_START, True
    return start, False


def validate_date_range(start: _dt.date, end: _dt.date) -> None:
    today = _dt.date.today()
    if start > end:
        raise ValueError(f"start date {start} is after end date {end}")
    if end > today:
        raise ValueError(f"end date {end} is in the future")


def to_api_date(d: _dt.date) -> str:
    return d.strftime("%Y%m%d")


def snap_end_for_granularity(end: _dt.date, granularity: str) -> _dt.date:
    """For monthly granularity, the Wikimedia pageviews API returns a
    truncated (partial-month) figure for the final bucket if `end` is not
    on/after the last day of its month -- confirmed live: requesting
    end=2025-09-01 returned 439 views for September 2025, while
    end=2025-09-30 correctly returned the full month's 10860. Snap `end`
    forward to the last day of its month so monthly requests never silently
    under-report the most recent period. Daily granularity is exact
    regardless of `end` and is returned unchanged.
    """
    if granularity != "monthly":
        return end
    if end.month == 12:
        next_month_first = _dt.date(end.year + 1, 1, 1)
    else:
        next_month_first = _dt.date(end.year, end.month + 1, 1)
    return next_month_first - _dt.timedelta(days=1)


def snap_start_for_granularity(start: _dt.date, granularity: str) -> _dt.date:
    """The same truncation applies to the *first* monthly bucket: confirmed
    live, start=2025-08-15 returned 15651 views for August 2025 while
    start=2025-08-01 returned the full month's 27178 -- both labeled as the
    same "2025-08-01" point. Snap `start` back to the 1st of its month so
    every monthly point is a whole month. Daily is returned unchanged.
    """
    if granularity != "monthly":
        return start
    return start.replace(day=1)


def last_complete_month_end(today: _dt.date | None = None) -> _dt.date:
    """Last day of the most recent calendar month that has fully ended."""
    today = today or _dt.date.today()
    return today.replace(day=1) - _dt.timedelta(days=1)


def choose_granularity(start: _dt.date, end: _dt.date) -> str:
    span_days = (end - start).days
    return "daily" if span_days <= 186 else "monthly"


def count_periods(start: _dt.date, end: _dt.date, granularity: str) -> int:
    """How many daily/monthly points a [start, end] request is expected to
    return -- used to catch a relayed response that is valid JSON but
    incomplete (a small model silently truncating a long series), which a
    per-item check alone cannot detect."""
    if granularity == "monthly":
        return (end.year - start.year) * 12 + (end.month - start.month) + 1
    return (end - start).days + 1


def month_chunks(start: _dt.date, end: _dt.date) -> list[tuple[_dt.date, _dt.date]]:
    """Split [start, end] into calendar-month-aligned pieces, e.g.
    2024-01-15..2024-03-10 -> [(01-15,01-31), (02-01,02-29), (03-01,03-10)].
    Used to keep any one relayed daily-granularity pageviews request small
    enough for a cheap model's web-fetch tool to reproduce verbatim (a full
    day-by-day year is 100+ KB of JSON; one month is a few KB)."""
    chunks: list[tuple[_dt.date, _dt.date]] = []
    cursor = start
    while cursor <= end:
        month_end = snap_end_for_granularity(cursor, "monthly")
        chunk_end = min(month_end, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + _dt.timedelta(days=1)
    return chunks


def write_run_record(outdir: Path, record: dict[str, Any]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "latest.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_latest(outdir: Path) -> dict[str, Any] | None:
    path = outdir / "latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def print_json(obj: dict[str, Any]) -> None:
    """Print JSON to stdout as UTF-8, safe on Windows consoles with non-UTF locales."""
    import sys

    data = json.dumps(obj, indent=2, ensure_ascii=False)
    sys.stdout.buffer.write(data.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
