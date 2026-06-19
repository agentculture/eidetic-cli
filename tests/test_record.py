"""Tests for eidetic.memory.record — hash determinism and dict round-trip."""

from __future__ import annotations

import hashlib

from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _default_scope() -> Scope:
    return Scope(name="default", visibility="public")


def test_hash_determinism() -> None:
    """Identical text yields identical SHA-256 hashes."""
    text = "hello world"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()

    r1 = Record(id="a", text=text, type="note", hash="", metadata={}, scope=_default_scope())
    r2 = Record(id="b", text=text, type="note", hash="", metadata={}, scope=_default_scope())

    assert r1.hash == expected
    assert r2.hash == expected
    assert r1.hash == r2.hash


def test_hash_derived_when_empty() -> None:
    """When hash is not supplied, it is derived from text."""
    text = "derive me"
    r = Record(id="x", text=text, type="note", hash="", metadata={}, scope=_default_scope())
    assert r.hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_hash_preserved_when_supplied() -> None:
    """A non-empty hash is preserved as-is."""
    custom = "deadbeef"
    r = Record(
        id="y", text="ignored", type="note", hash=custom, metadata={}, scope=_default_scope()
    )
    assert r.hash == custom


def test_to_dict_round_trip() -> None:
    """to_dict() / from_dict() round-trips exactly."""
    scope = Scope(name="private-scope", visibility="private")
    original = Record(
        id="rec-1",
        text="some text",
        type="memo",
        hash="",
        metadata={"key": "value"},
        scope=scope,
        score=0.95,
    )
    data = original.to_dict()
    restored = Record.from_dict(data)

    assert restored.id == original.id
    assert restored.text == original.text
    assert restored.type == original.type
    assert restored.hash == original.hash
    assert restored.metadata == original.metadata
    assert restored.scope.name == original.scope.name
    assert restored.scope.visibility == original.scope.visibility
    assert restored.score == original.score


def test_from_dict_score_none() -> None:
    """from_dict handles missing score key (defaults to None)."""
    data = {
        "id": "z",
        "text": "t",
        "type": "t",
        "hash": "h",
        "metadata": {},
        "scope": {"name": "default", "visibility": "public"},
    }
    r = Record.from_dict(data)
    assert r.score is None
