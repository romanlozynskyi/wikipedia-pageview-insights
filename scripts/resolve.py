"""Topic -> canonical Wikipedia article title per language edition.

Primary path: Wikidata entity search -> sitelinks (cross-language-correct,
the same real-world concept in every language). Fallback per language:
that language's own MediaWiki search, for topics with no clean Wikidata
entity or no sitelink in that language.

Ambiguity (multiple plausible distinct concepts, or a disambiguation page)
is returned as its own status -- never silently resolved by picking the
first hit. Callers (pageviews_cli.py) decide what to do with it.

Every request goes through datasource.WikimediaSource. A pair whose next
request has to come through the agent relay gets status "pending"; the CLI
turns those into a needs_data response rather than guessing.
"""
from __future__ import annotations

import re
from typing import Any

import datasource
import util

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_QID_RE = re.compile(r"^Q\d+$")
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_CANDIDATES = 5


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _entities_request(qids: list[str], sitelink_keys: list[str]) -> dict[str, Any]:
    """wbgetentities limited to English labels/descriptions and the requested
    editions' sitelinks. Unfiltered, every language's labels, descriptions
    and sitelinks come back (measured: 64 KB for just two items) -- too much
    for an agent to relay verbatim; filtered, under 1 KB for the same two
    (see references/api-notes.md)."""
    return {
        "url": WIKIDATA_API,
        "params": {
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "sitelinks|descriptions|labels",
            "languages": "en",
            "sitefilter": "|".join(sitelink_keys),
            "format": "json",
        },
        "purpose": f"Wikidata entities {', '.join(qids)}",
        "expect_key": "entities",
    }


def _sitelink_counts_request(qids: list[str]) -> dict[str, Any]:
    """Each item's total Wikipedia sitelink count (page prop wb-sitelinks) --
    what the viability filter and prominence rule need, now that the entities
    request only carries the requested editions' sitelinks."""
    return {
        "url": WIKIDATA_API,
        "params": {
            "action": "query",
            "prop": "pageprops",
            "ppprop": "wb-sitelinks",
            "titles": "|".join(qids),
            "format": "json",
        },
        "purpose": f"Wikidata sitelink counts {', '.join(qids)}",
        "expect_key": "query",
    }


def _wikidata_entities(source: datasource.WikimediaSource, qids: list[str], sitelink_keys: list[str]) -> dict[str, Any]:
    if not qids:
        return {}
    resp = source.get(**_entities_request(qids, sitelink_keys))
    resp.raise_for_status()
    return resp.body.get("entities", {})


def _search_wikidata(
    source: datasource.WikimediaSource, topic: str, sitelink_keys: list[str]
) -> list[dict[str, Any]]:
    """Return viable Wikidata candidates for a topic: search hits that have at
    least one Wikipedia sitelink (filters out papers/clinical trials *about*
    the topic, which commonly outrank the real concept entity in raw search
    order but have no sitelinks at all -- confirmed empirically for e.g.
    "Intermittent fasting")."""
    resp = source.get(
        WIKIDATA_API,
        params={
            "action": "wbsearchentities",
            "search": topic,
            "language": "en",
            "format": "json",
            "limit": 10,
        },
        purpose=f"Wikidata search for '{topic}'",
        expect_key="search",
    )
    resp.raise_for_status()
    hits = resp.body.get("search", [])
    if not hits:
        return []

    qids = [h["id"] for h in hits]
    entities_resp, counts_resp = source.get_all([_entities_request(qids, sitelink_keys), _sitelink_counts_request(qids)])
    entities_resp.raise_for_status()
    counts_resp.raise_for_status()
    entities = entities_resp.body.get("entities", {})
    counts = {
        page.get("title"): int(page.get("pageprops", {}).get("wb-sitelinks", 0))
        for page in counts_resp.body.get("query", {}).get("pages", {}).values()
    }

    viable: list[dict[str, Any]] = []
    for hit in hits:
        entity = entities.get(hit["id"])
        if not entity:
            continue
        sitelinks = entity.get("sitelinks", {})
        sitelink_count = max(counts.get(hit["id"], 0), len(sitelinks))
        if not sitelink_count:
            continue  # not a real Wikipedia-backed concept (e.g. a journal article)
        description = entity.get("descriptions", {}).get("en", {}).get("value", "")
        label = entity.get("labels", {}).get("en", {}).get("value", hit.get("label", ""))
        viable.append(
            {
                "qid": hit["id"],
                "label": label,
                "description": description,
                "sitelinks": {k: v["title"] for k, v in sitelinks.items()},
                "sitelink_count": sitelink_count,
                "is_disambiguation": "disambiguation" in description.lower(),
            }
        )
    return viable


_PROMINENCE_RATIO = 0.15  # see docstring below


def _choose_candidate(
    viable: list[dict[str, Any]], topic: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    """Return (chosen, None) if unambiguous, or (None, candidates) if ambiguous,
    or (None, None) if there is nothing usable from Wikidata at all.

    Multiple Wikidata entities sharing the exact search label is *not* by
    itself treated as ambiguous: searching "Astronomy" returns the science
    (Q333, 318 sitelinks) alongside an "Astronomy" magazine (Q3232273, 9
    sitelinks) and a fictional Hogwarts class (Q12012641, 2 sitelinks) --
    confirmed live. Flagging that as ambiguous would force an unnecessary
    disambiguation round-trip for an overwhelmingly obvious case. Instead,
    among same-label candidates, only those with a sitelink count at least
    _PROMINENCE_RATIO of the most-linked one are treated as genuine rival
    meanings. A true naming collision like "Mercury" (planet 274, element
    185, Roman god 87 sitelinks) still has multiple candidates clearing
    that bar and is correctly flagged ambiguous; the astronomy magazine and
    Hogwarts class (9 and 2 sitelinks vs. 318) do not.
    """
    if not viable:
        return None, None

    top = viable[0]
    if top["is_disambiguation"]:
        return None, viable[:_MAX_CANDIDATES]

    topic_lower = topic.strip().lower()
    exact_label_matches = [c for c in viable if c["label"].strip().lower() == topic_lower]
    if len(exact_label_matches) > 1:
        max_sitelinks = max(c["sitelink_count"] for c in exact_label_matches)
        prominent = [
            c for c in exact_label_matches if c["sitelink_count"] >= max_sitelinks * _PROMINENCE_RATIO
        ]
        if len(prominent) > 1:
            return None, prominent[:_MAX_CANDIDATES]
        return prominent[0], None

    return top, None


def _mediawiki_fallback(
    source: datasource.WikimediaSource, topic: str, lang_code: str, expected_qid: str | None = None
) -> dict[str, Any]:
    """Search this language's own wiki for the topic. If `expected_qid` is
    given (the topic's Wikidata entity, known not to have a sitelink in this
    language), the top hit is cross-checked against its own
    pageprops.wikibase_item: a mismatch means the search's top hit is almost
    certainly an unrelated article that happened to share keywords -- e.g.
    searching "Intermittent fasting" on pl.wikipedia's full-text search
    confidently top-hits "Stres oksydacyjny" (Oxidative stress, Q898814),
    not the intermittent-fasting concept (Q1666254). That is reported as
    not_found (true: no dedicated article exists), never as a false
    "resolved". Without an expected_qid to check against (topic has no
    Wikidata entity at all), the hit is still returned, but flagged as
    unverified so callers/agents can weight it accordingly.
    """
    host = util.normalize_project(lang_code)["mediawiki_host"]
    api_url = f"https://{host}/w/api.php"

    resp = source.get(
        api_url,
        params={"action": "query", "list": "search", "srsearch": topic, "format": "json", "srlimit": 5},
        purpose=f"{host} search for '{topic}'",
        expect_key="query",
    )
    resp.raise_for_status()
    hits = resp.body.get("query", {}).get("search", [])
    if not hits:
        return {"status": "not_found"}

    top_title = hits[0]["title"]
    info_resp = source.get(
        api_url,
        params={
            "action": "query",
            "titles": top_title,
            "redirects": 1,
            "prop": "pageprops",
            "format": "json",
        },
        purpose=f"{host} page properties of '{top_title}'",
        expect_key="query",
    )
    info_resp.raise_for_status()
    pages = info_resp.body.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    resolved_title = page.get("title", top_title)
    pageprops = page.get("pageprops", {})
    is_disambiguation = "disambiguation" in pageprops

    if is_disambiguation:
        candidates = [
            {"qid": None, "label": h["title"], "description": _strip_tags(h.get("snippet", ""))}
            for h in hits[:_MAX_CANDIDATES]
        ]
        return {"status": "ambiguous", "candidates": candidates}

    if expected_qid is not None:
        page_qid = pageprops.get("wikibase_item")
        if page_qid != expected_qid:
            return {
                "status": "not_found",
                "reason": (
                    f"nearest full-text match '{resolved_title}' is a different Wikidata item "
                    f"({page_qid or 'none'}, expected {expected_qid}) -- no dedicated article for "
                    "this topic in this language"
                ),
            }
        return {"status": "resolved", "title": resolved_title, "qid": expected_qid, "method": "mediawiki_search_verified"}

    return {"status": "resolved", "title": resolved_title, "qid": None, "method": "mediawiki_search_unverified"}


def _resolve_pin(source: datasource.WikimediaSource, pin_value: str, lang_code: str) -> dict[str, Any]:
    if _QID_RE.match(pin_value):
        sitelink_key = util.normalize_project(lang_code)["sitelink_key"]
        entities = _wikidata_entities(source, [pin_value], [sitelink_key])
        entity = entities.get(pin_value)
        if not entity:
            return {"status": "not_found", "reason": f"QID {pin_value} does not exist"}
        sitelinks = entity.get("sitelinks", {})
        if sitelink_key not in sitelinks:
            return {"status": "not_found", "reason": f"{pin_value} has no {lang_code} article"}
        return {
            "status": "resolved",
            "title": sitelinks[sitelink_key]["title"],
            "qid": pin_value,
            "method": "pinned_qid",
        }
    return {"status": "resolved", "title": pin_value, "qid": None, "method": "pinned_title"}


def _resolve_unpinned(
    source: datasource.WikimediaSource,
    topic: str,
    lang_code: str,
    chosen: dict[str, Any] | None,
    ambiguous_candidates: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if ambiguous_candidates is not None:
        return {
            "status": "ambiguous",
            "candidates": [
                {"qid": c.get("qid"), "label": c["label"], "description": c.get("description", "")}
                for c in ambiguous_candidates
            ],
        }

    expected_qid = None
    if chosen is not None:
        sitelink_key = util.normalize_project(lang_code)["sitelink_key"]
        if sitelink_key in chosen["sitelinks"]:
            return {
                "status": "resolved",
                "title": chosen["sitelinks"][sitelink_key],
                "qid": chosen["qid"],
                "method": "wikidata_sitelink",
            }
        expected_qid = chosen["qid"]

    return _mediawiki_fallback(source, topic, lang_code, expected_qid=expected_qid)


def resolve_all(
    source: datasource.WikimediaSource,
    topics: list[str],
    lang_codes: list[str],
    pins: dict[tuple[str, str], str] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Resolve every (topic, lang) pair. Returns
    {topic: {lang_code: {status: resolved|ambiguous|not_found|pending, ...}}}.
    Never raises for a bad topic/lang -- always encodes failure in the result.
    "pending" means the pair's next request is queued on `source` for the
    agent relay; every pair gets as far as it can, so one re-run round
    covers all topics and languages at once.
    """
    pins = pins or {}
    sitelink_keys = [util.normalize_project(lc)["sitelink_key"] for lc in lang_codes]
    results: dict[str, dict[str, dict[str, Any]]] = {}

    for topic in topics:
        results[topic] = {}
        unpinned_langs = [lc for lc in lang_codes if (topic, lc) not in pins]

        chosen: dict[str, Any] | None = None
        ambiguous_candidates: list[dict[str, Any]] | None = None
        search_pending = False
        if unpinned_langs:
            try:
                viable = _search_wikidata(source, topic, sitelink_keys)
                chosen, ambiguous_candidates = _choose_candidate(viable, topic)
            except datasource.DataPending:
                search_pending = True

        for lang_code in lang_codes:
            try:
                if (topic, lang_code) in pins:
                    results[topic][lang_code] = _resolve_pin(source, pins[(topic, lang_code)], lang_code)
                elif search_pending:
                    results[topic][lang_code] = {"status": "pending"}
                else:
                    results[topic][lang_code] = _resolve_unpinned(
                        source, topic, lang_code, chosen, ambiguous_candidates
                    )
            except datasource.DataPending:
                results[topic][lang_code] = {"status": "pending"}

    return results


def has_ambiguity(resolution: dict[str, dict[str, dict[str, Any]]]) -> bool:
    return any(
        entry.get("status") == "ambiguous"
        for lang_map in resolution.values()
        for entry in lang_map.values()
    )
