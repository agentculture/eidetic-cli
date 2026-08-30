"""Tests for the rerank lane in eidetic.memory.embed (t2 of #39).

The two rerank lanes return numbers on the SAME 0..1 scale with completely
different meaning (remote: 0.968 on-topic vs 3.2e-05 distractor; lexical:
0.034 vs 0.016). A caller must be able to tell which lane produced its scores
WITHOUT inspecting the values — exactly the problem ``embed_detect`` already
solved with its ``(vectors, online)`` return.

Also: a server response that omits an index is a server error, not a
zero-relevance document, so it must raise rather than default to 0.0.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

import eidetic.memory.embed as embed_mod
from eidetic.memory.embed import EmbedClient


class _FakeResponse:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _capture(monkeypatch: pytest.MonkeyPatch, body: dict) -> list:
    """Patch urlopen to record outgoing requests and answer with *body*."""
    seen: list = []

    def fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        seen.append(req)
        return _FakeResponse(body)

    monkeypatch.setattr(embed_mod.urllib.request, "urlopen", fake_urlopen)
    return seen


# -- lane self-reporting ----------------------------------------------------


def test_rerank_detect_reports_lexical_lane_offline() -> None:
    """A dead endpoint yields the lexical lane, flagged online=False."""
    client = EmbedClient(base_url="http://127.0.0.1:1/v1")
    scores, online = client.rerank_detect("hello", ["hello world", "goodbye"])
    assert online is False
    assert len(scores) == 2


def test_rerank_detect_reports_remote_lane_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete server response yields the remote lane, flagged online=True."""
    body = {
        "results": [
            {"index": 0, "relevance_score": 0.968},
            {"index": 1, "relevance_score": 3.2e-05},
        ]
    }
    seen = _capture(monkeypatch, body)
    scores, online = EmbedClient(base_url="http://gw/v1").rerank_detect("q", ["doc a", "doc b"])
    assert online is True
    assert scores == [0.968, 3.2e-05]
    assert len(seen) == 1


def test_rerank_detect_distinguishes_lanes_without_inspecting_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lane flag alone separates the two lanes, even on identical scores.

    Both lanes are forced to return the SAME numbers; only ``online`` differs.
    """
    # 0.0 is exactly what the lexical lane also produces for these inputs
    # (no token overlap), so the two lanes return identical score lists.
    body = {
        "results": [
            {"index": 0, "relevance_score": 0.0},
            {"index": 1, "relevance_score": 0.0},
        ]
    }

    def fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        # Only the remote client is answered; the dead-port client must fail.
        if req.full_url.startswith("http://gw/"):
            return _FakeResponse(body)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(embed_mod.urllib.request, "urlopen", fake_urlopen)
    remote_scores, remote_online = EmbedClient(base_url="http://gw/v1").rerank_detect(
        "q", ["a", "b"]
    )
    lexical_scores, lexical_online = EmbedClient(base_url="http://127.0.0.1:1/v1").rerank_detect(
        "q", ["a", "b"]
    )
    assert remote_scores == lexical_scores  # identical values on purpose
    assert remote_online is True
    assert lexical_online is False


def test_rerank_still_returns_scores_only() -> None:
    """The existing rerank() signature keeps working for current callers."""
    client = EmbedClient(base_url="http://127.0.0.1:1/v1")
    scores = client.rerank("hello", ["hello world", "goodbye"])
    assert isinstance(scores, list)
    assert len(scores) == 2


def test_rerank_detect_falls_back_when_remote_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 from the gateway degrades to the lexical lane, not an exception."""

    def raise_401(req, timeout=None):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(embed_mod.urllib.request, "urlopen", raise_401)
    scores, online = EmbedClient(base_url="http://gw/v1").rerank_detect("q", ["a", "b"])
    assert online is False
    assert len(scores) == 2


# -- short responses --------------------------------------------------------


def test_short_response_missing_index_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response without a score for every document is a server error.

    The old ``score_map.get(i, 0.0)`` silently turned the missing index into a
    zero-relevance document; it must raise instead.
    """
    body = {"results": [{"index": 0, "relevance_score": 0.9}]}  # index 1 omitted
    _capture(monkeypatch, body)
    client = EmbedClient(base_url="http://gw/v1")
    with pytest.raises(ValueError, match="missing score for index 1"):
        client._remote_rerank("q", ["doc a", "doc b"])


def test_short_response_degrades_to_lexical_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through rerank_detect a short response is a server error -> lexical lane.

    The raise is caught by the detect wrapper (as any remote failure is) and
    reported as online=False, never as a fabricated 0.0 score.
    """
    body = {"results": [{"index": 0, "relevance_score": 0.9}]}
    _capture(monkeypatch, body)
    scores, online = EmbedClient(base_url="http://gw/v1").rerank_detect("q", ["doc a", "doc b"])
    assert online is False
    assert scores == EmbedClient(base_url="http://127.0.0.1:1/v1")._local_rerank(
        "q", ["doc a", "doc b"]
    )


# -- malformed scores (PR #42 review) ---------------------------------------


@pytest.mark.parametrize(
    ("label", "bad_score"),
    [("string", "high"), ("null", None), ("nan", float("nan")), ("inf", float("inf"))],
)
def test_unusable_score_raises_at_the_boundary(
    monkeypatch: pytest.MonkeyPatch, label: str, bad_score: object
) -> None:
    """A present-but-unusable score is a server error, like a missing one.

    A string or None crashes the caller's sort with a bare TypeError; a NaN
    sorts and thresholds nonsensically (every ``score > cutoff`` is False)
    while still being reported as the remote lane — routing a malformed
    response straight past the fail-closed guard. All are rejected here, at
    the boundary, so they degrade exactly as a dead endpoint does.
    """
    body = {"results": [{"index": 0, "relevance_score": bad_score}, {"index": 1, "score": 0.9}]}
    _capture(monkeypatch, body)
    client = EmbedClient(base_url="http://gw/v1")
    with pytest.raises(ValueError, match="index 0"):
        client._remote_rerank("q", ["doc a", "doc b"])


@pytest.mark.parametrize("bad_score", ["high", None, float("nan")])
def test_unusable_score_degrades_to_the_lexical_lane(
    monkeypatch: pytest.MonkeyPatch, bad_score: object
) -> None:
    """Through rerank_detect a malformed score reports online=False.

    That is what makes --rerank fail closed on it rather than serving the
    numbers as if the cross-encoder had produced them.
    """
    body = {"results": [{"index": 0, "relevance_score": bad_score}, {"index": 1, "score": 0.9}]}
    _capture(monkeypatch, body)
    scores, online = EmbedClient(base_url="http://gw/v1").rerank_detect("q", ["doc a", "doc b"])
    assert online is False
    assert all(isinstance(value, float) for value in scores)


def test_a_well_formed_response_is_still_the_remote_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: the validation must not reject legitimate responses.

    Without this, the two tests above would also pass if validation rejected
    everything and the remote lane were simply dead.
    """
    body = {"results": [{"index": 0, "relevance_score": 0.97}, {"index": 1, "score": 0.02}]}
    _capture(monkeypatch, body)
    scores, online = EmbedClient(base_url="http://gw/v1").rerank_detect("q", ["doc a", "doc b"])
    assert online is True
    assert scores == [0.97, 0.02]
