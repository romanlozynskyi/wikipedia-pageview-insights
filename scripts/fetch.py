"""Wikimedia per-article pageviews REST client with an exact-key disk cache.

v1 caching is deliberately simple: a cache hit requires the exact tuple
(pageviews_project, article, granularity, start, end) to match a prior
fetch. A different date range is a full re-fetch, not a computed delta --
see references/api-notes.md for why, and the roadmap for interval-aware
caching as a future improvement.

For monthly granularity, the requested `start`/`end` are snapped to the
first/last day of their months before being sent to the API and used as
the cache key (see util.snap_start_for_granularity / snap_end_for_granularity)
-- confirmed live that Wikimedia returns truncated figures for the first
and final months otherwise.

A series whose effective end is today or later is never written to the
cache: its most recent period is still accumulating views, so a cached
copy would go stale.

Requests go through datasource.WikimediaSource: direct HTTP when Python
can reach Wikimedia, otherwise the agent relay. A relayed body is accepted
only if every item is the requested series (project, article, granularity,
all-access, user agent, inside the requested period) AND the item count
matches what the period requires -- a cheap model's web-fetch tool can
silently truncate a long response while every remaining item still looks
individually valid, which the per-item check alone would miss. Anything
that fails either check is re-requested, never analyzed. Each result
records its `origin` ("direct" or "relay"), which the cache keeps too.

A daily-granularity request that must go through the relay is split into
calendar-month chunks (util.month_chunks) before any request is built: a
full year of daily data is 100+ KB of JSON, well past what a cheap model's
web-fetch tool reliably reproduces verbatim (see references/api-notes.md);
one month is a few KB. Chunking turns one hard copy task into several easy
ones, and every chunk still goes through the same per-item and
completeness checks. Direct HTTP requests are never chunked -- Python
copies bytes exactly, so there is nothing to protect against.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any

import requests

import datasource
import util

PAGEVIEWS_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
ACCESS = "all-access"
AGENT = "user"

# Above this many daily items, a relay-bound request is split into monthly
# chunks (see module docstring). ~31 items is a few KB -- comfortably within
# what a small model's web-fetch tool reproduces verbatim; a full year
# (365+ items, 100+ KB) is not.
_MAX_RELAY_ITEMS = 31


def _cache_key(project: str, article: str, granularity: str, start: str, end: str) -> str:
    raw = "|".join([project, article, granularity, start, end])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _is_relay_bound(source: datasource.WikimediaSource) -> bool:
    """True once this run is known to be relying on the agent relay for
    Wikimedia access -- either forced (`--data-source relay`) or auto-mode
    having already hit a direct-access failure this run."""
    return source.mode == "relay" or (source.mode == "auto" and source.direct_failure is not None)


def _handle_response(
    resp: datasource.Response,
    cache_path: Path,
    *,
    cacheable: bool,
    project: str,
    article: str,
    granularity: str,
    start: _dt.date,
    end: _dt.date,
    source: datasource.WikimediaSource,
    purpose: str,
) -> dict[str, Any]:
    """Turn one API Response into a fetch result, applying the relay
    validation/caching rules shared by the single-request and chunked paths."""
    if resp.status_code == 404:
        result = {"status": "no_data", "origin": resp.origin}
        if cacheable:
            cache_path.write_text(json.dumps({"status": "no_data"}), encoding="utf-8")
        result["from_cache"] = False
        return result

    if resp.status_code != 200:
        return {"status": "error", "detail": f"HTTP {resp.status_code}", "from_cache": False}

    try:
        points = _parse_items(
            resp.body,
            expected=(
                {"project": project, "article": article, "granularity": granularity, "start": start, "end": end}
                if resp.origin == "relay"
                else None
            ),
        )
    except ValueError as exc:
        if resp.origin == "relay":
            source.reject(resp, purpose, f"saved body is not this series' API response: {exc}")
            return {"status": "pending", "from_cache": False}
        return {"status": "error", "detail": f"unexpected API response: {exc}", "from_cache": False}

    result = {"status": "ok", "points": points, "origin": resp.origin}
    if cacheable:
        cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    result["from_cache"] = False
    return result


def _fetch_one_range(
    source: datasource.WikimediaSource,
    cache_dir: Path,
    *,
    project: str,
    article: str,
    granularity: str,
    start: _dt.date,
    end: _dt.date,
) -> dict[str, Any]:
    """Fetch exactly one (project, article, granularity, start, end) as a
    single request -- the original, unchunked path."""
    cacheable = end < _dt.date.today()
    start_s, end_s = util.to_api_date(start), util.to_api_date(end)
    key = _cache_key(project, article, granularity, start_s, end_s)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, key)

    if cacheable and path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        cached.setdefault("origin", "direct")  # written before origins were recorded
        cached["from_cache"] = True
        return cached

    encoded_article = requests.utils.quote(article.replace(" ", "_"), safe="")
    url = f"{PAGEVIEWS_BASE}/{project}/{ACCESS}/{AGENT}/{encoded_article}/{granularity}/{start_s}/{end_s}"
    purpose = f"pageviews of '{article}' on {project}, {granularity}, {start} to {end}"

    try:
        resp = source.get(url, purpose=purpose, expect_key="items")
    except datasource.DataPending:
        return {"status": "pending", "from_cache": False}
    except (requests.RequestException, RuntimeError) as exc:
        return {"status": "error", "detail": str(exc), "from_cache": False}

    return _handle_response(
        resp, path, cacheable=cacheable, project=project, article=article,
        granularity=granularity, start=start, end=end, source=source, purpose=purpose,
    )


def _fetch_chunked_daily(
    source: datasource.WikimediaSource,
    cache_dir: Path,
    *,
    project: str,
    article: str,
    start: _dt.date,
    end: _dt.date,
) -> dict[str, Any]:
    """Fetch a long daily-granularity series as several small monthly
    requests. Every chunk not already cached is requested together via
    source.get_all(), so one relay round can carry every chunk's URL at
    once instead of needing one round per chunk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    chunks = util.month_chunks(start, end)
    encoded_article = requests.utils.quote(article.replace(" ", "_"), safe="")

    resolved: dict[tuple[_dt.date, _dt.date], dict[str, Any]] = {}
    to_fetch: list[tuple[_dt.date, _dt.date]] = []
    paths: dict[tuple[_dt.date, _dt.date], Path] = {}
    cacheable_by_chunk: dict[tuple[_dt.date, _dt.date], bool] = {}

    for c_start, c_end in chunks:
        cacheable = c_end < _dt.date.today()
        cacheable_by_chunk[c_start, c_end] = cacheable
        key = _cache_key(project, article, "daily", util.to_api_date(c_start), util.to_api_date(c_end))
        path = _cache_path(cache_dir, key)
        paths[c_start, c_end] = path
        if cacheable and path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            cached.setdefault("origin", "direct")
            cached["from_cache"] = True
            resolved[c_start, c_end] = cached
        else:
            to_fetch.append((c_start, c_end))

    if to_fetch:
        specs = [
            {
                "url": f"{PAGEVIEWS_BASE}/{project}/{ACCESS}/{AGENT}/{encoded_article}/daily/{util.to_api_date(c_start)}/{util.to_api_date(c_end)}",
                "purpose": f"pageviews of '{article}' on {project}, daily, {c_start} to {c_end}",
                "expect_key": "items",
            }
            for c_start, c_end in to_fetch
        ]
        try:
            responses = source.get_all(specs)
        except (requests.RequestException, RuntimeError) as exc:
            return {"status": "error", "detail": str(exc), "from_cache": False}
        except datasource.DataPending:
            return {"status": "pending", "from_cache": False}
        for (c_start, c_end), resp, spec in zip(to_fetch, responses, specs):
            resolved[c_start, c_end] = _handle_response(
                resp, paths[c_start, c_end], cacheable=cacheable_by_chunk[c_start, c_end],
                project=project, article=article, granularity="daily", start=c_start, end=c_end,
                source=source, purpose=spec["purpose"],
            )

    if any(r["status"] == "pending" for r in resolved.values()):
        return {"status": "pending", "from_cache": False}
    for r in resolved.values():
        if r["status"] == "error":
            return r

    merged: dict[str, int] = {}
    any_relay = False
    any_from_network = False
    for c_start, c_end in chunks:
        r = resolved[c_start, c_end]
        if not r.get("from_cache"):
            any_from_network = True
        if r["status"] == "ok":
            if r.get("origin") == "relay":
                any_relay = True
            for p in r["points"]:
                merged[p["date"]] = p["views"]

    points = [{"date": d, "views": v} for d, v in sorted(merged.items())]
    return {
        "status": "ok",
        "points": points,
        "origin": "relay" if any_relay else "direct",
        "from_cache": not any_from_network,
    }


def fetch_series(
    source: datasource.WikimediaSource,
    cache_dir: Path,
    *,
    project: str,
    article: str,
    granularity: str,
    start: _dt.date,
    end: _dt.date,
) -> dict[str, Any]:
    """Fetch one article's pageview series, using the exact-key disk cache.

    Returns {status: "ok", points: [{date, views}, ...], origin, from_cache: bool}
    or {status: "no_data"} on a 404 (article/project has no data for this
    range -- the Wikimedia API does not distinguish the two causes, see
    references/api-notes.md) or {status: "error", detail: ...} on a
    non-retryable failure, or {status: "pending"} when the response is
    queued on `source` for the agent relay.
    """
    effective_start = util.snap_start_for_granularity(start, granularity)
    effective_end = util.snap_end_for_granularity(end, granularity)

    if (
        granularity == "daily"
        and _is_relay_bound(source)
        and util.count_periods(effective_start, effective_end, "daily") > _MAX_RELAY_ITEMS
    ):
        return _fetch_chunked_daily(source, cache_dir, project=project, article=article, start=effective_start, end=effective_end)

    return _fetch_one_range(
        source, cache_dir, project=project, article=article, granularity=granularity,
        start=effective_start, end=effective_end,
    )


def _timestamp_to_date(timestamp: str) -> str:
    # Wikimedia timestamps look like "2024010100" (YYYYMMDDHH)
    return f"{timestamp[0:4]}-{timestamp[4:6]}-{timestamp[6:8]}"


def _parse_items(body: Any, *, expected: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Turn a pageviews API body into [{date, views}, ...] in date order.

    With `expected` (a relayed body), every item must also carry exactly the
    requested project/article/granularity/access/agent and a timestamp inside
    the requested period -- the check that what was relayed is the official
    response for this series and nothing else -- and the number of items must
    match the period's length exactly, so a silently truncated copy (every
    remaining item individually valid, but fewer of them than the period
    requires) is rejected rather than analyzed as if it were complete.
    """
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ValueError("no 'items' list")

    points: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("an item is not a JSON object")
        timestamp, views = item.get("timestamp"), item.get("views")
        if not (isinstance(timestamp, str) and len(timestamp) == 10 and timestamp.isdigit()):
            raise ValueError(f"malformed timestamp {timestamp!r}")
        if not isinstance(views, int) or isinstance(views, bool) or views < 0:
            raise ValueError(f"malformed views value {views!r} at {timestamp}")
        date = _timestamp_to_date(timestamp)

        if expected is not None:
            actual = {
                "project": item.get("project"),
                "article": str(item.get("article", "")).replace(" ", "_"),
                "granularity": item.get("granularity"),
                "access": item.get("access"),
                "agent": item.get("agent"),
            }
            wanted = {
                "project": expected["project"],
                "article": expected["article"].replace(" ", "_"),
                "granularity": expected["granularity"],
                "access": ACCESS,
                "agent": AGENT,
            }
            for field, value in wanted.items():
                if actual[field] != value:
                    raise ValueError(f"item {field} is {actual[field]!r}, expected {value!r}")
            try:
                day = _dt.date.fromisoformat(date)
            except ValueError:
                raise ValueError(f"malformed timestamp {timestamp!r}") from None
            if not expected["start"] <= day <= expected["end"]:
                raise ValueError(f"item dated {date} is outside {expected['start']}..{expected['end']}")
            if expected["granularity"] == "monthly" and day.day != 1:
                raise ValueError(f"monthly item dated {date} is not the 1st of a month")
            if date in points:
                raise ValueError(f"duplicate item for {date}")

        points[date] = views

    if expected is not None:
        wanted_count = util.count_periods(expected["start"], expected["end"], expected["granularity"])
        if len(points) != wanted_count:
            raise ValueError(
                f"expected {wanted_count} items for {expected['start']}..{expected['end']} "
                f"({expected['granularity']}), got {len(points)} -- looks truncated or incomplete"
            )

    return [{"date": d, "views": v} for d, v in sorted(points.items())]


def fetch_all(
    source: datasource.WikimediaSource,
    cache_dir: Path,
    resolution: dict[str, dict[str, dict[str, Any]]],
    *,
    granularity: str,
    start: _dt.date,
    end: _dt.date,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Fetch series for every resolved (topic, lang) pair. Skips pairs whose
    resolution status is not "resolved" (already reported as a limitation
    upstream). A pair still waiting on the relay comes back as "pending"."""
    results: dict[str, dict[str, dict[str, Any]]] = {}
    for topic, lang_map in resolution.items():
        results[topic] = {}
        for lang_code, entry in lang_map.items():
            if entry.get("status") != "resolved":
                continue
            project = util.normalize_project(lang_code)["pageviews_project"]
            results[topic][lang_code] = fetch_series(
                source,
                cache_dir,
                project=project,
                article=entry["title"],
                granularity=granularity,
                start=start,
                end=end,
            )
    return results
