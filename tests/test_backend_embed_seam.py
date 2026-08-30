"""Tests for the named EmbedClient seam on StoreBackend (t4).

The recall CLI handler must be able to obtain the backend's embed client
through a public, documented seam — and to inject a fake one — without
monkeypatching the private ``_embed`` attribute.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eidetic.memory.backend import StoreBackend, get_backend
from eidetic.memory.embed import EmbedClient
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


class _FakeEmbed:
    """A stand-in embed client that records which lane it was asked to run."""

    def __init__(self) -> None:
        self.embed_calls: list[list[str]] = []
        self.rerank_calls: list[tuple[str, list[str]]] = []

    def embed_detect(self, texts: list[str]) -> tuple[list[list[float]], bool]:
        self.embed_calls.append(texts)
        # Deterministic unit vectors, reported as "online" so hybrid fuses them.
        return [[1.0, 0.0] for _ in texts], True

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        self.rerank_calls.append((query, docs))
        return [0.5] * len(docs)


def _make_record(rid: str, text: str) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata={},
        scope=Scope(name="default", visibility="public"),
    )


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch) -> str:
    """Isolated store dir; EIDETIC_DATA_DIR is a tmp_path, never a real store."""
    d = str(tmp_path / "memory")
    monkeypatch.setenv("EIDETIC_DATA_DIR", d)
    return d


def test_embed_client_property_returns_injected_fake(data_dir: str) -> None:
    """A fake injected through the constructor seam is what the property returns."""
    fake = _FakeEmbed()
    backend = StoreBackend("files", embed_client=fake)
    assert backend.embed_client is fake


def test_embed_client_property_defaults_to_embed_client(data_dir: str) -> None:
    """Without injection the seam exposes a real EmbedClient (unchanged default)."""
    backend = StoreBackend("files")
    assert isinstance(backend.embed_client, EmbedClient)


def test_get_backend_forwards_embed_client_seam(data_dir: str) -> None:
    """The public factory forwards the seam, so the CLI never touches _embed."""
    fake = _FakeEmbed()
    backend = get_backend("files", embed_client=fake)
    assert backend.embed_client is fake


def test_search_uses_injected_client(data_dir: str) -> None:
    """search() ranks through the injected client — no monkeypatching involved."""
    fake = _FakeEmbed()
    backend = StoreBackend("files", embed_client=fake)
    backend.upsert(_make_record("a1", "alpha record"))
    backend.upsert(_make_record("b1", "beta record"))

    scope = Scope(name="default", visibility="public")
    hits = backend.search("alpha", 5, scope, None, mode="hybrid")

    assert [r.id for r in hits] == ["a1", "b1"]
    # The fake was actually consulted for the query + candidate texts.
    assert fake.embed_calls
    assert "alpha" in fake.embed_calls[0]
    assert "alpha record" in fake.embed_calls[0]


def test_cli_handler_can_reach_reranker_via_seam(data_dir: str) -> None:
    """The coming recall --rerank path: cmd_recall reaches rerank() through the seam."""
    fake = _FakeEmbed()
    backend = StoreBackend("files", embed_client=fake)
    scores = backend.embed_client.rerank("query", ["doc one", "doc two"])
    assert scores == [0.5, 0.5]
    assert fake.rerank_calls == [("query", ["doc one", "doc two"])]
