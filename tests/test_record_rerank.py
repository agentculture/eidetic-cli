"""Tests for the recall-only ``rerank_score`` field on Record.

The critical invariant: ``rerank_score`` must NEVER be persisted.  The
envelope whitelist in ``backend.record_to_envelope`` omits it by construction,
but that alone makes a naive round-trip test vacuous (it passes even if the
guard is broken).  These tests exercise the guard directly: they construct an
Envelope whose metadata *does* carry ``rerank_score`` (simulating a broken
whitelist) and assert that ``record_from_envelope`` still returns
``rerank_score=None``.

Mutation-verified: if you add ``rerank_score=m.get("rerank_score")`` to
``record_from_envelope`` in backend.py, ``test_rerank_score_never_survives_from_envelope``
FAILS.
"""

from __future__ import annotations

import pytest
from data_refinery.store import Envelope
from data_refinery.store import Scope as DRScope

from eidetic.memory.backend import record_from_envelope, record_to_envelope
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _scope() -> Scope:
    return Scope(name="default", visibility="public")


def _record(**kwargs) -> Record:
    defaults = dict(
        id="rec-1",
        text="hello world",
        type="note",
        hash="",
        metadata={},
        scope=_scope(),
    )
    defaults.update(kwargs)
    return Record(**defaults)


# ---------------------------------------------------------------------------
# Field declaration and to_dict / from_dict threading
# ---------------------------------------------------------------------------


def test_rerank_score_defaults_none() -> None:
    """A fresh Record has rerank_score=None."""
    r = _record()
    assert r.rerank_score is None


def test_rerank_score_in_to_dict() -> None:
    """to_dict() includes the 'rerank_score' key."""
    r = _record(rerank_score=0.87)
    d = r.to_dict()
    assert "rerank_score" in d
    assert d["rerank_score"] == 0.87


def test_rerank_score_round_trips() -> None:
    """from_dict(to_dict(r)) preserves rerank_score."""
    r = _record(rerank_score=0.42)
    restored = Record.from_dict(r.to_dict())
    assert restored.rerank_score == pytest.approx(0.42)


def test_rerank_score_round_trips_none() -> None:
    """from_dict(to_dict(r)) preserves rerank_score=None."""
    r = _record()
    restored = Record.from_dict(r.to_dict())
    assert restored.rerank_score is None


def test_from_dict_legacy_missing_rerank_score_defaults_none() -> None:
    """from_dict on a dict lacking 'rerank_score' yields None (no KeyError)."""
    data = {
        "id": "legacy-1",
        "text": "old fact",
        "type": "note",
        "hash": "deadbeef",
        "metadata": {},
        "scope": {"name": "default", "visibility": "public"},
    }
    r = Record.from_dict(data)
    assert r.rerank_score is None


# ---------------------------------------------------------------------------
# The non-persistence guard: record_from_envelope must NOT read rerank_score
# ---------------------------------------------------------------------------


def test_rerank_score_never_survives_from_envelope() -> None:
    """Even if rerank_score leaks into envelope metadata, record_from_envelope
    must return a Record with rerank_score=None.

    This test BYPASSES the record_to_envelope whitelist: it constructs an
    Envelope directly with 'rerank_score' in its metadata, simulating a broken
    whitelist.  The guard under test is that record_from_envelope does not
    read the key back.

    Mutation-verify: add ``rerank_score=m.get("rerank_score")`` to
    record_from_envelope in backend.py → this test FAILS.
    """
    env = Envelope(
        id="rec-guard",
        content="some text",
        hash="abc123",
        scope=DRScope(name="default", visibility="public"),
        metadata={
            "type": "note",
            "record_metadata": {},
            "created": "2025-01-01",
            "last_recall": None,
            "recall_count": 0,
            "links": [],
            "supersedes": None,
            "lifecycle": "active",
            "added_by": None,
            # The leak: rerank_score is present in the stored metadata.
            "rerank_score": 0.99,
        },
    )
    record = record_from_envelope(env)
    assert record.rerank_score is None, (
        "record_from_envelope must NOT read rerank_score from envelope "
        "metadata — it is a query-time artefact and must never persist."
    )


def test_rerank_score_never_survives_from_envelope_score_and_signal_control() -> None:
    """Control: score and signal are also absent from record_from_envelope.

    This mirrors the rerank_score guard test but for the pre-existing fields,
    proving the pattern is consistent.
    """
    env = Envelope(
        id="rec-control",
        content="some text",
        hash="abc123",
        scope=DRScope(name="default", visibility="public"),
        metadata={
            "type": "note",
            "record_metadata": {},
            "created": "2025-01-01",
            "last_recall": None,
            "recall_count": 0,
            "links": [],
            "supersedes": None,
            "lifecycle": "active",
            "added_by": None,
            "score": 0.5,
            "signal": 0.7,
            "rerank_score": 0.99,
        },
    )
    record = record_from_envelope(env)
    assert record.score is None
    assert record.signal is None
    assert record.rerank_score is None


def test_rerank_score_is_not_written_into_the_envelope() -> None:
    """The WRITE side of the guard: record_to_envelope must not persist rerank_score.

    The sibling test above covers the read path (record_from_envelope ignores a
    leaked key).  This one covers the path that actually writes: today
    record_to_envelope is an explicit field whitelist that omits rerank_score by
    construction, so a future edit ADDING it to that whitelist is the realistic
    regression — and nothing else in the suite would notice.

    Mutation-verify: add ``"rerank_score": record.rerank_score`` to the metadata
    dict in backend.record_to_envelope → this test FAILS.
    """
    record = Record(
        id="rec-write-guard",
        text="some text",
        type="note",
        hash="abc123",
        metadata={},
        scope=Scope(name="default", visibility="public"),
    )
    record.rerank_score = 0.99
    record.score = 0.5
    record.signal = 0.7

    env = record_to_envelope(record)

    assert "rerank_score" not in env.metadata, (
        "record_to_envelope must not persist rerank_score — it is a query-time "
        "artefact, like score and signal."
    )
    # Control: the two pre-existing query-time fields are excluded the same way,
    # so a whitelist that started persisting all three would fail here too.
    assert "score" not in env.metadata
    assert "signal" not in env.metadata
