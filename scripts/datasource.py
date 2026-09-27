"""The one door every Wikimedia request goes through: direct HTTP first, agent relay as fallback.

Preferred path: Python fetches the official Wikidata / MediaWiki / pageviews
API URL itself (util.get_with_retry), exactly as before.

Fallback (relay): sandboxed agents often cannot open outbound connections
from Python, yet can still read a URL through their own web-fetch or browser
tool (or ask the user to open it). When direct access is unavailable, a
request is neither dropped nor substituted: its exact official URL is queued
as a data request `{url, save_as, purpose}`. The agent fetches that URL,
writes the raw response body verbatim to `save_as`, and re-runs the same
command. On the re-run the saved body stands in for the HTTP response and
goes through the same parsing, validation, caching and analysis as a direct
one. Only bodies saved under the file name derived from the requested URL
are ever read, so data from anywhere else has no way in.

Modes (`--data-source`):
- auto (default): direct first. If direct access fails before any direct
  request has succeeded in this run, the network is treated as blocked for
  the rest of the run and every request goes to the relay. A failure after
  a success is host- or server-specific and raises as before.
- direct: direct only; failures raise as before, nothing is relayed.
- relay: no network at all; saved bodies only (used on relay re-runs so a
  blocked network is not probed again).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

import util

MODES = ("auto", "direct", "relay")


class DataPending(Exception):
    """The response for at least one request must be supplied via the relay first."""


@dataclass
class Response:
    status_code: int
    body: Any
    origin: str  # "direct" | "relay"
    url: str

    def raise_for_status(self) -> None:
        if self.status_code != 200:
            raise requests.HTTPError(f"HTTP {self.status_code} from {self.url}")


def _blocked_reason(resp: requests.Response, expect_key: str | None) -> str | None:
    """A direct response that is not an answer from Wikimedia: an egress proxy
    refusal (403/407), an HTML block page served in place of the JSON API, or
    -- confirmed live, a real Cowork failure this closes -- a proxy that
    returns its OWN 200-status, valid-JSON "policy denial" page in place of
    the API response. That last case is invisible to a bare status-code or
    JSON-parse check: the response is technically well-formed, just not from
    Wikimedia. `expect_key` (the field only the real endpoint's response
    carries, e.g. "search"/"entities"/"items") is the only way to tell the
    difference, mirroring the check `_parse_saved_body` already applies to
    relayed bodies -- a direct response deserves no less scrutiny than a
    relayed one.

    The real action APIs' own HTTP-200 error shape is specifically
    `{"error": {"code": ..., "info": ...}}` (confirmed live, e.g.
    wbgetentities for a nonexistent QID) -- only a body matching that exact
    shape is accepted as a genuine (if unhelpful) answer. A bare `"error"`
    key with a string or other value is not this API's convention, so it
    does not get a free pass: a proxy denial page is at least as likely to
    use a plain string "error" field as Wikidata's own nested-object one,
    and accepting any truthy "error" key would let such a page slip
    through this check undetected."""
    if resp.status_code in (403, 407):
        return f"HTTP {resp.status_code} from {resp.url}"
    try:
        body = resp.json()
    except ValueError:
        return f"non-JSON HTTP {resp.status_code} response from {resp.url} (likely a proxy or firewall page)"
    if (
        expect_key
        and resp.status_code == 200  # a real 404 (a different status) never reaches this branch
        and isinstance(body, dict)
        and expect_key not in body
        and not isinstance(body.get("error"), dict)
    ):
        return (
            f"HTTP 200 response from {resp.url} has no '{expect_key}' field, so it is not the real API response "
            "(likely a proxy page disguised as valid JSON)"
        )
    return None


def _parse_saved_body(path: Path, expect_key: str | None) -> tuple[int, dict[str, Any]]:
    """Read a relayed response body. Raises ValueError with an agent-facing
    explanation when the file is not a usable verbatim API response."""
    text = path.read_text(encoding="utf-8-sig").strip()
    try:
        body = json.loads(text)
    except ValueError:
        # Tolerate text a tool wrapped around the JSON (e.g. a page-text
        # dump); the JSON itself must still be complete and verbatim.
        first, last = text.find("{"), text.rfind("}")
        try:
            body = json.loads(text[first : last + 1]) if 0 <= first < last else None
        except ValueError:
            body = None
        if body is None:
            raise ValueError(
                "saved file is not valid JSON -- save the complete raw response body exactly as the URL returned "
                "it, not a summary or excerpt"
            ) from None
    if not isinstance(body, dict):
        raise ValueError("saved file is not a JSON object -- save the raw response body of the URL verbatim")

    # Wikimedia REST error body, e.g. the pageviews API's 404:
    # {"status": 404, "title": "Not Found", "type": "about:blank", "detail": ...}
    if isinstance(body.get("status"), int) and isinstance(body.get("title"), str) and "items" not in body:
        return body["status"], body

    # The action APIs answer some requests with HTTP 200 and an "error" object
    # (e.g. wbgetentities for a nonexistent QID). That is a genuine answer and
    # is passed on exactly as a direct response would be.
    if expect_key and expect_key not in body and "error" not in body:
        raise ValueError(
            f"saved body has no '{expect_key}' field, so it is not the response of this URL -- fetch exactly "
            "this URL and save its raw body"
        )
    return 200, body


class WikimediaSource:
    def __init__(self, session: requests.Session, *, mode: str = "auto", relay_dir: Path | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"--data-source must be one of {', '.join(MODES)}")
        if mode != "direct" and relay_dir is None:
            raise ValueError("relay_dir is required unless mode is 'direct'")
        self.session = session
        self.mode = mode
        self.relay_dir = relay_dir
        self.pending: dict[str, dict[str, Any]] = {}
        self.direct_failure: str | None = None
        self.relayed_count = 0
        self._direct_ok = False

    @staticmethod
    def full_url(url: str, params: dict[str, Any] | None = None) -> str:
        """The exact URL requests would send -- the relay's lookup key."""
        return requests.Request("GET", url, params=params).prepare().url

    def relay_path(self, full_url: str) -> Path:
        assert self.relay_dir is not None
        key = hashlib.sha256(full_url.encode("utf-8")).hexdigest()[:24]
        return self.relay_dir / f"{key}.json"

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        purpose: str,
        expect_key: str | None = None,
    ) -> Response:
        """GET one official Wikimedia URL. Raises DataPending (after queuing
        the request) when its body has to come through the relay."""
        full = self.full_url(url, params)

        if self.mode != "relay" and self.direct_failure is None:
            try:
                resp = util.get_with_retry(self.session, full)
            except (requests.RequestException, RuntimeError) as exc:
                if self.mode == "direct" or self._direct_ok:
                    raise
                self.direct_failure = f"{type(exc).__name__}: {exc}"
            else:
                blocked = _blocked_reason(resp, expect_key)
                if blocked is None:
                    self._direct_ok = True
                    return Response(resp.status_code, resp.json(), "direct", full)
                if self.mode == "direct" or self._direct_ok:
                    raise requests.RequestException(blocked)
                self.direct_failure = blocked

        path = self.relay_path(full)
        if not path.exists():
            self._queue(full, purpose, problem=None)
            raise DataPending(full)
        try:
            status, body = _parse_saved_body(path, expect_key)
        except ValueError as exc:
            self._queue(full, purpose, problem=str(exc))
            raise DataPending(full) from None
        self.relayed_count += 1
        return Response(status, body, "relay", full)

    def get_all(self, specs: list[dict[str, Any]]) -> list[Response]:
        """Several independent GETs (each spec is get()'s keyword arguments):
        every one is attempted before DataPending is raised, so all of them
        land in the same relay round instead of one per re-run."""
        responses: list[Response] = []
        pending = False
        for spec in specs:
            try:
                responses.append(self.get(**spec))
            except DataPending:
                pending = True
        if pending:
            raise DataPending()
        return responses

    def reject(self, response: Response, purpose: str, problem: str) -> None:
        """Re-queue a relayed body that parsed but failed a caller's content
        check, so the agent is asked to fetch that URL again."""
        self._queue(response.url, purpose, problem=problem)

    def _queue(self, full_url: str, purpose: str, *, problem: str | None) -> None:
        self.pending[full_url] = {
            "url": full_url,
            "save_as": str(self.relay_path(full_url).resolve()),
            "purpose": purpose,
            "problem": problem,
        }
