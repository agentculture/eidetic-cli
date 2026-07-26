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


# ---------------------------------------------------------------------------
# added_by attribution field (t1)
# ---------------------------------------------------------------------------


def test_added_by_in_to_dict() -> None:
    """to_dict() includes the 'added_by' key."""
    r = Record(
        id="ab-1",
        text="some text",
        type="note",
        hash="",
        metadata={},
        scope=_default_scope(),
        added_by="agent-x",
    )
    d = r.to_dict()
    assert "added_by" in d
    assert d["added_by"] == "agent-x"


def test_added_by_round_trip() -> None:
    """from_dict(to_dict(r)) round-trips a record with added_by set."""
    scope = Scope(name="test-scope", visibility="public")
    original = Record(
        id="ab-2",
        text="attribution test",
        type="note",
        hash="",
        metadata={"src": "test"},
        scope=scope,
        added_by="some-agent",
    )
    restored = Record.from_dict(original.to_dict())
    assert restored == original
    assert restored.added_by == "some-agent"


def test_added_by_defaults_none_when_missing() -> None:
    """from_dict on a dict lacking 'added_by' yields record.added_by is None (no KeyError)."""
    data = {
        "id": "ab-3",
        "text": "legacy record",
        "type": "note",
        "hash": "h",
        "metadata": {},
        "scope": {"name": "default", "visibility": "public"},
    }
    r = Record.from_dict(data)
    assert r.added_by is None


# ---------------------------------------------------------------------------
# recall_count float tolerance (t3 — groundwork for graded reinforcement)
# ---------------------------------------------------------------------------


def test_recall_count_float_round_trips() -> None:
    """A fractional recall_count (graded/hop-decayed reinforcement) round-trips exactly."""
    r = Record(
        id="rc-float",
        text="t",
        type="note",
        hash="",
        metadata={},
        scope=_default_scope(),
        recall_count=2.5,
    )
    restored = Record.from_dict(r.to_dict())
    assert restored.recall_count == 2.5
    assert isinstance(restored.recall_count, float)


def test_recall_count_default_stays_int() -> None:
    """The default recall_count (0) stays an int end to end.

    Untouched (never-recalled) records must keep serialising 'recall_count': 0
    (int), not 0.0 (float) -- byte-identical JSON output for legacy records.
    """
    r = Record(id="rc-default", text="t", type="note", hash="", metadata={}, scope=_default_scope())
    assert r.recall_count == 0
    assert isinstance(r.recall_count, int)
    d = r.to_dict()
    assert isinstance(d["recall_count"], int)
    restored = Record.from_dict(d)
    assert isinstance(restored.recall_count, int)
    assert restored.recall_count == 0


def test_recall_count_int_value_not_coerced_to_float() -> None:
    """An explicit integer recall_count is preserved as int, not upcast to float."""
    r = Record(
        id="rc-int-7",
        text="t",
        type="note",
        hash="",
        metadata={},
        scope=_default_scope(),
        recall_count=7,
    )
    d = r.to_dict()
    assert isinstance(d["recall_count"], int)
    restored = Record.from_dict(d)
    assert isinstance(restored.recall_count, int)
    assert restored.recall_count == 7


def test_recall_count_legacy_missing_key_defaults_to_int_zero() -> None:
    """from_dict on a dict lacking 'recall_count' defaults to int 0, not float 0.0."""
    data = {
        "id": "legacy-rc",
        "text": "legacy record",
        "type": "note",
        "hash": "h",
        "metadata": {},
        "scope": {"name": "default", "visibility": "public"},
    }
    r = Record.from_dict(data)
    assert r.recall_count == 0
    assert isinstance(r.recall_count, int)
