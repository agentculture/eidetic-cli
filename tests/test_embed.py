"""Tests for eidetic.memory.embed — fully offline, no network required."""

from __future__ import annotations

import math

from eidetic.memory.embed import EmbedClient, cosine


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
