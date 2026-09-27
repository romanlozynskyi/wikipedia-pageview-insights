import json
import sys
from pathlib import Path

import pytest
import responses

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import datasource  # noqa: E402
import resolve  # noqa: E402
import util  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _mock_wikidata_search(rsps: responses.RequestsMock, topic: str, fixture: str):
    rsps.add(
        responses.GET,
        resolve.WIKIDATA_API,
        json=_load(fixture),
        match=[responses.matchers.query_param_matcher({"action": "wbsearchentities", "search": topic, "language": "en", "format": "json", "limit": "10"})],
    )


def _direct():
    return datasource.WikimediaSource(util.new_session(), mode="direct")


def _entities_params(qids: list[str], langs: list[str]) -> dict:
    return {"action": "wbgetentities", "ids": "|".join(qids), "props": "sitelinks|descriptions|labels", "languages": "en", "sitefilter": "|".join(f"{l}wiki" for l in langs), "format": "json"}


def _counts_params(qids: list[str]) -> dict:
    return {"action": "query", "prop": "pageprops", "ppprop": "wb-sitelinks", "titles": "|".join(qids), "format": "json"}


def _counts_body(qids: list[str], fixture: str) -> dict:
    # Same shape as the live pageprops answer (captured 2026-09-25); counts
    # taken from the fixture's own sitelinks.
    entities = _load(fixture)["entities"]
    return {"batchcomplete": "", "query": {"pages": {
        str(i): {"pageid": i, "ns": 0, "title": q, "pageprops": {"wb-sitelinks": str(len(entities[q].get("sitelinks", {})))}}
        for i, q in enumerate(qids, start=1)
    }}}


def _mock_wikidata_entities(rsps: responses.RequestsMock, qids: list[str], fixture: str, langs: list[str]):
    rsps.add(
        responses.GET,
        resolve.WIKIDATA_API,
        json=_load(fixture),
        match=[responses.matchers.query_param_matcher(_entities_params(qids, langs))],
    )


def _mock_wikidata_lookup(rsps: responses.RequestsMock, qids: list[str], fixture: str, langs: list[str]):
    """The two requests the search path makes for its hits: filtered entities + sitelink counts."""
    _mock_wikidata_entities(rsps, qids, fixture, langs)
    rsps.add(
        responses.GET,
        resolve.WIKIDATA_API,
        json=_counts_body(qids, fixture),
        match=[responses.matchers.query_param_matcher(_counts_params(qids))],
    )


@responses.activate
def test_resolve_clean_sitelink_hit():
    _mock_wikidata_search(responses, "Intermittent fasting", "wikidata_search_intermittent_fasting.json")
    _mock_wikidata_lookup(
        responses,
        ["Q1666254", "Q112575736", "Q63574657"],
        "wikidata_entities_intermittent_fasting.json",
        ["en", "cs", "uk"],
    )

    source = _direct()
    result = resolve.resolve_all(source, ["Intermittent fasting"], ["en", "cs", "uk"])

    en = result["Intermittent fasting"]["en"]
    assert en["status"] == "resolved"
    assert en["title"] == "Intermittent fasting"
    assert en["method"] == "wikidata_sitelink"
    assert en["qid"] == "Q1666254"

    cs = result["Intermittent fasting"]["cs"]
    assert cs["status"] == "resolved"
    assert cs["title"] == "Přerušovaný půst"

    uk = result["Intermittent fasting"]["uk"]
    assert uk["status"] == "resolved"
    assert uk["title"] == "Інтервальне голодування"
    # pl (no Wikidata sitelink for this topic, falls back to MediaWiki search) is
    # covered separately in test_resolve_falls_back_to_mediawiki_when_no_sitelink


@responses.activate
def test_resolve_falls_back_to_mediawiki_and_verifies_against_wikidata_qid():
    _mock_wikidata_search(responses, "Intermittent fasting", "wikidata_search_intermittent_fasting.json")
    _mock_wikidata_lookup(
        responses,
        ["Q1666254", "Q112575736", "Q63574657"],
        "wikidata_entities_intermittent_fasting.json",
        ["pl"],
    )
    responses.add(
        responses.GET,
        "https://pl.wikipedia.org/w/api.php",
        json={"query": {"search": [{"title": "Głodówka przerywana", "snippet": "..."}]}},
        match=[responses.matchers.query_param_matcher({"action": "query", "list": "search", "srsearch": "Intermittent fasting", "format": "json", "srlimit": "5"})],
    )
    responses.add(
        responses.GET,
        "https://pl.wikipedia.org/w/api.php",
        json={"query": {"pages": {"123": {"title": "Głodówka przerywana", "pageprops": {"wikibase_item": "Q1666254"}}}}},
        match=[responses.matchers.query_param_matcher({"action": "query", "titles": "Głodówka przerywana", "redirects": "1", "prop": "pageprops", "format": "json"})],
    )

    source = _direct()
    result = resolve.resolve_all(source, ["Intermittent fasting"], ["pl"])

    pl = result["Intermittent fasting"]["pl"]
    assert pl["status"] == "resolved"
    assert pl["title"] == "Głodówka przerywana"
    assert pl["method"] == "mediawiki_search_verified"
    assert pl["qid"] == "Q1666254"


@responses.activate
def test_resolve_rejects_mediawiki_hit_that_is_actually_a_different_wikidata_item():
    # Regression test for a real bug found via live testing: full-text search
    # for "Intermittent fasting" on pl.wikipedia confidently top-hits "Stres
    # oksydacyjny" (Oxidative stress, Q898814) -- a different topic entirely.
    # This must be rejected as not_found, not silently accepted as resolved.
    _mock_wikidata_search(responses, "Intermittent fasting", "wikidata_search_intermittent_fasting.json")
    _mock_wikidata_lookup(
        responses,
        ["Q1666254", "Q112575736", "Q63574657"],
        "wikidata_entities_intermittent_fasting.json",
        ["pl"],
    )
    responses.add(
        responses.GET,
        "https://pl.wikipedia.org/w/api.php",
        json={"query": {"search": [{"title": "Stres oksydacyjny", "snippet": "..."}]}},
        match=[responses.matchers.query_param_matcher({"action": "query", "list": "search", "srsearch": "Intermittent fasting", "format": "json", "srlimit": "5"})],
    )
    responses.add(
        responses.GET,
        "https://pl.wikipedia.org/w/api.php",
        json={"query": {"pages": {"1238433": {"title": "Stres oksydacyjny", "pageprops": {"wikibase_item": "Q898814"}}}}},
        match=[responses.matchers.query_param_matcher({"action": "query", "titles": "Stres oksydacyjny", "redirects": "1", "prop": "pageprops", "format": "json"})],
    )

    source = _direct()
    result = resolve.resolve_all(source, ["Intermittent fasting"], ["pl"])

    pl = result["Intermittent fasting"]["pl"]
    assert pl["status"] == "not_found"
    assert "Q898814" in pl["reason"]


@responses.activate
def test_resolve_mediawiki_fallback_without_wikidata_entity_is_unverified():
    responses.add(
        responses.GET,
        resolve.WIKIDATA_API,
        json={"search": []},
        match=[responses.matchers.query_param_matcher({"action": "wbsearchentities", "search": "Obscure local topic", "language": "en", "format": "json", "limit": "10"})],
    )
    responses.add(
        responses.GET,
        "https://en.wikipedia.org/w/api.php",
        json={"query": {"search": [{"title": "Obscure Local Topic", "snippet": "..."}]}},
        match=[responses.matchers.query_param_matcher({"action": "query", "list": "search", "srsearch": "Obscure local topic", "format": "json", "srlimit": "5"})],
    )
    responses.add(
        responses.GET,
        "https://en.wikipedia.org/w/api.php",
        json={"query": {"pages": {"1": {"title": "Obscure Local Topic", "pageprops": {}}}}},
        match=[responses.matchers.query_param_matcher({"action": "query", "titles": "Obscure Local Topic", "redirects": "1", "prop": "pageprops", "format": "json"})],
    )

    source = _direct()
    result = resolve.resolve_all(source, ["Obscure local topic"], ["en"])

    en = result["Obscure local topic"]["en"]
    assert en["status"] == "resolved"
    assert en["method"] == "mediawiki_search_unverified"
    assert en["qid"] is None


def _candidate(qid, label, n_sitelinks, is_disambiguation=False):
    return {
        "qid": qid,
        "label": label,
        "description": "",
        "sitelinks": {f"lang{i}wiki": f"title{i}" for i in range(n_sitelinks)},
        "sitelink_count": n_sitelinks,
        "is_disambiguation": is_disambiguation,
    }


def test_choose_candidate_filters_out_minor_same_label_entities():
    # Regression test for a real finding from live Haiku validation: searching
    # "Astronomy" returns the science (318 sitelinks) alongside an "Astronomy"
    # magazine (9) and a fictional Hogwarts class (2) that share the exact
    # label. These should not force an unnecessary disambiguation round-trip.
    viable = [
        _candidate("Q333", "Astronomy", 318),
        _candidate("Q3232273", "Astronomy", 9),
        _candidate("Q12012641", "astronomy", 2),
    ]
    chosen, ambiguous = resolve._choose_candidate(viable, "Astronomy")
    assert ambiguous is None
    assert chosen["qid"] == "Q333"


def test_choose_candidate_flags_genuine_collision_of_comparably_prominent_entities():
    # "Mercury": planet (274), element (185), Roman god (87) are all
    # well-established topics -- a real naming collision, unlike astronomy above.
    viable = [
        _candidate("Q308", "Mercury", 274),
        _candidate("Q925", "Mercury", 185),
        _candidate("Q1150", "Mercury", 87),
        _candidate("Q1231263", "Mercury", 35),  # a minor commune, below the prominence bar
    ]
    chosen, ambiguous = resolve._choose_candidate(viable, "Mercury")
    assert chosen is None
    assert ambiguous is not None
    ambiguous_qids = {c["qid"] for c in ambiguous}
    assert ambiguous_qids == {"Q308", "Q925", "Q1150"}
    assert "Q1231263" not in ambiguous_qids


def test_choose_candidate_single_viable_entity_is_never_ambiguous():
    viable = [_candidate("Q1666254", "intermittent fasting", 3)]
    chosen, ambiguous = resolve._choose_candidate(viable, "intermittent fasting")
    assert ambiguous is None
    assert chosen["qid"] == "Q1666254"


@responses.activate
def test_resolve_flags_disambiguation_page_as_ambiguous():
    _mock_wikidata_search(responses, "Mercury", "wikidata_search_mercury.json")
    _mock_wikidata_lookup(responses, ["Q19935429", "Q308", "Q925"], "wikidata_entities_mercury.json", ["en"])

    source = _direct()
    result = resolve.resolve_all(source, ["Mercury"], ["en"])

    en = result["Mercury"]["en"]
    assert en["status"] == "ambiguous"
    assert len(en["candidates"]) >= 2
    assert resolve.has_ambiguity(result) is True


@responses.activate
def test_resolve_not_found_when_nothing_matches():
    responses.add(
        responses.GET,
        resolve.WIKIDATA_API,
        json={"search": []},
        match=[responses.matchers.query_param_matcher({"action": "wbsearchentities", "search": "Zzzznonexistenttopic", "language": "en", "format": "json", "limit": "10"})],
    )
    responses.add(
        responses.GET,
        "https://en.wikipedia.org/w/api.php",
        json={"query": {"search": []}},
        match=[responses.matchers.query_param_matcher({"action": "query", "list": "search", "srsearch": "Zzzznonexistenttopic", "format": "json", "srlimit": "5"})],
    )

    source = _direct()
    result = resolve.resolve_all(source, ["Zzzznonexistenttopic"], ["en"])

    assert result["Zzzznonexistenttopic"]["en"]["status"] == "not_found"


@responses.activate
def test_pinned_title_bypasses_resolution_entirely():
    source = _direct()
    result = resolve.resolve_all(
        source,
        ["Mercury"],
        ["en"],
        pins={("Mercury", "en"): "Mercury (planet)"},
    )
    en = result["Mercury"]["en"]
    assert en == {"status": "resolved", "title": "Mercury (planet)", "qid": None, "method": "pinned_title"}
    assert len(responses.calls) == 0  # no network calls at all for a title pin


@responses.activate
def test_pinned_qid_resolves_via_sitelink():
    _mock_wikidata_entities(responses, ["Q308"], "wikidata_entities_mercury.json", ["en"])

    source = _direct()
    result = resolve.resolve_all(source, ["Mercury"], ["en"], pins={("Mercury", "en"): "Q308"})

    en = result["Mercury"]["en"]
    assert en["status"] == "resolved"
    assert en["title"] == "Mercury (planet)"
    assert en["method"] == "pinned_qid"


@responses.activate
def test_pinned_qid_with_no_sitelink_in_language_is_not_found():
    _mock_wikidata_entities(responses, ["Q308"], "wikidata_entities_mercury.json", ["de"])

    source = _direct()
    result = resolve.resolve_all(source, ["Mercury"], ["de"], pins={("Mercury", "de"): "Q308"})

    assert result["Mercury"]["de"]["status"] == "not_found"


def test_sitelink_count_not_filtered_sitelinks_drives_viability_and_prominence():
    # With sitefilter, an item can come back with no sitelinks in the requested
    # editions yet be a major concept elsewhere; its total count must decide.
    viable = [_candidate("Q308", "Mercury", 0), _candidate("Q925", "Mercury", 0)]
    viable[0]["sitelink_count"], viable[1]["sitelink_count"] = 274, 185
    chosen, ambiguous = resolve._choose_candidate(viable, "Mercury")
    assert chosen is None and {c["qid"] for c in ambiguous} == {"Q308", "Q925"}


def _relay_source(tmp_path):
    return datasource.WikimediaSource(util.new_session(), mode="relay", relay_dir=tmp_path / "relay")


def _save(source, url, params, body):
    path = source.relay_path(source.full_url(url, params))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def test_relay_resolution_takes_one_round_per_dependent_step(tmp_path):
    topic, langs = "Intermittent fasting", ["en", "cs", "uk"]
    qids = ["Q1666254", "Q112575736", "Q63574657"]
    fixture = "wikidata_entities_intermittent_fasting.json"
    search_params = {"action": "wbsearchentities", "search": topic, "language": "en", "format": "json", "limit": 10}

    # Round 1: only the search is known; every language waits on it.
    source = _relay_source(tmp_path)
    result = resolve.resolve_all(source, [topic], langs)
    assert {e["status"] for e in result[topic].values()} == {"pending"}
    assert list(source.pending) == [source.full_url(resolve.WIKIDATA_API, search_params)]

    # Round 2: entities and sitelink counts are asked for together.
    _save(source, resolve.WIKIDATA_API, search_params, _load("wikidata_search_intermittent_fasting.json"))
    source = _relay_source(tmp_path)
    resolve.resolve_all(source, [topic], langs)
    assert set(source.pending) == {
        source.full_url(resolve.WIKIDATA_API, _entities_params(qids, langs)),
        source.full_url(resolve.WIKIDATA_API, _counts_params(qids)),
    }

    # Round 3: resolved exactly as in direct mode.
    _save(source, resolve.WIKIDATA_API, _entities_params(qids, langs), _load(fixture))
    _save(source, resolve.WIKIDATA_API, _counts_params(qids), _counts_body(qids, fixture))
    source = _relay_source(tmp_path)
    result = resolve.resolve_all(source, [topic], langs)
    assert not source.pending
    assert result[topic]["uk"] == {"status": "resolved", "title": "Інтервальне голодування", "qid": "Q1666254", "method": "wikidata_sitelink"}


def test_relayed_wikidata_error_for_unknown_qid_pin_is_not_found(tmp_path):
    # Live answer for a nonexistent QID: HTTP 200 with an "error" object (captured 2026-09-25).
    source = _relay_source(tmp_path)
    params = {"action": "wbgetentities", "ids": "Q999999999999", "props": "sitelinks|descriptions|labels", "languages": "en", "sitefilter": "enwiki", "format": "json"}
    _save(source, resolve.WIKIDATA_API, params, {"error": {"code": "no-such-entity", "info": "Could not find an entity with the ID \"Q999999999999\"."}})

    result = resolve.resolve_all(source, ["X"], ["en"], pins={("X", "en"): "Q999999999999"})

    assert result["X"]["en"]["status"] == "not_found"
    assert not source.pending


@responses.activate
def test_proxy_200_json_denial_page_on_wikidata_search_is_blocked_not_not_found(tmp_path):
    # Regression: this is the very first network call of almost every run.
    # Before the expect_key check applied to direct responses too, a proxy
    # returning its own HTTP 200 + valid-JSON "policy denial" page here was
    # indistinguishable from Wikidata genuinely answering "zero search
    # hits" (`resp.body.get("search", [])` defaults to `[]` either way) --
    # silently producing a wrong `not_found` for a topic that may well
    # exist, instead of correctly falling back to the relay.
    responses.add(
        responses.GET,
        resolve.WIKIDATA_API,
        json={"error": "policy denial", "code": "EGRESS_BLOCKED"},
        status=200,
        match=[responses.matchers.query_param_matcher({"action": "wbsearchentities", "search": "Astronomy", "language": "en", "format": "json", "limit": "10"})],
    )
    source = datasource.WikimediaSource(util.new_session(), mode="auto", relay_dir=tmp_path / "relay")

    result = resolve.resolve_all(source, ["Astronomy"], ["en"])

    assert result["Astronomy"]["en"]["status"] == "pending"  # NOT "not_found"
    assert source.direct_failure is not None
    assert source.pending  # queued for the relay instead of silently giving up
