"""Tests for eidetic.memory.embed — fully offline, no network required."""

from __future__ import annotations

import json
import urllib.error

import pytest

import eidetic.memory.embed as embed_mod
from eidetic.memory.embed import (
    _DEFAULT_BASE_URL,
    _DEFAULT_MODEL,
    _DEFAULT_RERANK_MODEL,
    EmbedClient,
    cosine,
)

_KEY_VARS = ("EIDETIC_EMBED_API_KEY", "COLLEAGUE_API_KEY", "CULTURE_VLLM_API_KEY")


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every embed-related env var so defaults are what's under test.

    Without this the developer's own shell (which exports COLLEAGUE_API_KEY on a
    configured box) would leak into the assertions.
    """
    for var in (
        *_KEY_VARS,
        "EIDETIC_EMBED_URL",
        "EIDETIC_EMBED_MODEL",
        "EIDETIC_RERANK_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_embed_deterministic_same_input() -> None:
    """embed() returns equal-length vectors for the same input across calls."""
    client = EmbedClient(base_url="http://localhost:0/nope")
    text = "hello world"
    v1 = client.embed([text])[0]
    v2 = client.embed([text])[0]
    assert len(v1) == len(v2)
    assert v1 == v2


def test_embed_different_text_differs() -> None:
    """embed() produces different vectors for different texts."""
    client = EmbedClient(base_url="http://localhost:0/nope")
    v1 = client.embed(["hello"])[0]
    v2 = client.embed(["goodbye"])[0]
    assert v1 != v2


def test_embed_multiple_texts() -> None:
    """embed() returns one vector per input text."""
    client = EmbedClient(base_url="http://localhost:0/nope")
    texts = ["one", "two", "three"]
    vecs = client.embed(texts)
    assert len(vecs) == 3
    assert all(len(v) == len(vecs[0]) for v in vecs)


def test_rerank_deterministic() -> None:
    """rerank() returns deterministic scores for the same inputs."""
    client = EmbedClient(base_url="http://localhost:0/nope")
    query = "hello"
    docs = ["hello world", "goodbye world", "hello there"]
    s1 = client.rerank(query, docs)
    s2 = client.rerank(query, docs)
    assert s1 == s2
    assert len(s1) == len(docs)


def test_cosine_identical() -> None:
    """cosine of identical vectors approximates 1.0."""
    v = [1.0, 2.0, 3.0]
    assert abs(cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal() -> None:
    """cosine of orthogonal vectors is 0.0."""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(cosine(a, b)) < 1e-9


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


def test_default_endpoint_is_the_lobes_gateway(no_env: None) -> None:
    """Defaults target the fleet gateway, not a per-gear container port.

    Regression: the per-role vLLM containers are not published to the host, so
    any per-gear port (the old 8101/8002) 401s or refuses and silently degrades
    recall to the lexical fallback.
    """
    client = EmbedClient()
    assert _DEFAULT_BASE_URL == "http://localhost:8001/v1"
    assert client._base_url == "http://localhost:8001/v1"
    assert client._model == _DEFAULT_MODEL == "Qwen/Qwen3-Embedding-0.6B"


def test_rerank_model_is_distinct_from_embed_model(no_env: None) -> None:
    """The gateway routes on `model`, so rerank must not reuse the embed model."""
    client = EmbedClient()
    assert client._rerank_model == _DEFAULT_RERANK_MODEL
    assert client._rerank_model != client._model


def test_remote_rerank_sends_the_rerank_model(
    monkeypatch: pytest.MonkeyPatch, no_env: None
) -> None:
    """A live rerank call names the reranker gear in its payload."""
    seen = _capture(monkeypatch, {"results": [{"index": 0, "relevance_score": 0.9}]})
    EmbedClient(base_url="http://gw/v1").rerank("q", ["doc"])
    payload = json.loads(seen[0].data)
    assert payload["model"] == _DEFAULT_RERANK_MODEL
    assert seen[0].full_url == "http://gw/v1/rerank"


@pytest.mark.parametrize("var", _KEY_VARS)
def test_api_key_resolved_from_each_env_var(
    monkeypatch: pytest.MonkeyPatch, no_env: None, var: str
) -> None:
    """Any of the three key variables authenticates the request."""
    monkeypatch.setenv(var, "sekrit")
    seen = _capture(monkeypatch, {"data": [{"index": 0, "embedding": [0.1]}]})
    EmbedClient(base_url="http://gw/v1").embed(["hi"])
    assert seen[0].get_header("Authorization") == "Bearer sekrit"


def test_api_key_precedence(monkeypatch: pytest.MonkeyPatch, no_env: None) -> None:
    """EIDETIC_EMBED_API_KEY wins over the shared deployment variables."""
    monkeypatch.setenv("EIDETIC_EMBED_API_KEY", "mine")
    monkeypatch.setenv("COLLEAGUE_API_KEY", "theirs")
    assert EmbedClient()._api_key == "mine"


def test_no_auth_header_when_no_key(monkeypatch: pytest.MonkeyPatch, no_env: None) -> None:
    """With no key configured we send no Authorization header at all."""
    seen = _capture(monkeypatch, {"data": [{"index": 0, "embedding": [0.1]}]})
    EmbedClient(base_url="http://gw/v1").embed(["hi"])
    assert seen[0].get_header("Authorization") is None


def test_auth_failure_degrades_to_local_fallback(
    monkeypatch: pytest.MonkeyPatch, no_env: None
) -> None:
    """A 401 from the gateway falls back to lexical rather than raising."""

    def raise_401(req, timeout=None):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(embed_mod.urllib.request, "urlopen", raise_401)
    vecs, online = EmbedClient(base_url="http://gw/v1").embed_detect(["hi"])
    assert online is False
    assert len(vecs) == 1 and len(vecs[0]) > 0


def test_embed_offline_fallback_deterministic() -> None:
    """embed() returns the deterministic local fallback when offline (no network)."""
    client = EmbedClient(base_url="http://localhost:0/nope")
    text = "offline test"
    v1 = client.embed([text])[0]
    v2 = client.embed([text])[0]
    # Both calls must return the same deterministic vector
    assert v1 == v2
    # The vector must be non-empty and L2-normalised
    assert len(v1) > 0
    import math

    norm = math.sqrt(sum(x * x for x in v1))
    assert abs(norm - 1.0) < 1e-6
