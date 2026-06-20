"""TDD tests for t1 — temporal + lifecycle fields on Record.

Acceptance criteria:
1. Record.to_dict() / Record.from_dict() round-trip ALL new fields losslessly.
2. A legacy record dict lacking the new keys loads via from_dict with documented
   defaults: date-unknown sentinel, neutral (None) signal, empty links,
   lifecycle="active", recall_count=0, supersedes=None, last_recall=None.
"""

from __future__ import annotations

import pytest

from eidetic.memory.record import DATE_UNKNOWN, Record
from eidetic.memory.scope import Scope


def _scope() -> Scope:
    return Scope(name="default", visibility="public")


def _minimal_record(**kwargs) -> Record:
    """Return a Record with the minimum required positional fields."""
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
# Acceptance criterion 1: full round-trip for all new fields
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """All new fields survive to_dict → from_dict unchanged."""

    def test_created_roundtrips(self) -> None:
        r = _minimal_record(created="2025-01-15")
        assert Record.from_dict(r.to_dict()).created == "2025-01-15"

    def test_last_recall_roundtrips_none(self) -> None:
        r = _minimal_record(last_recall=None)
        assert Record.from_dict(r.to_dict()).last_recall is None

    def test_last_recall_roundtrips_value(self) -> None:
        r = _minimal_record(last_recall="2026-06-20T12:00:00Z")
        assert Record.from_dict(r.to_dict()).last_recall == "2026-06-20T12:00:00Z"

    def test_recall_count_roundtrips_zero(self) -> None:
        r = _minimal_record(recall_count=0)
        assert Record.from_dict(r.to_dict()).recall_count == 0

    def test_recall_count_roundtrips_nonzero(self) -> None:
        r = _minimal_record(recall_count=7)
        assert Record.from_dict(r.to_dict()).recall_count == 7

    def test_links_roundtrips_empty(self) -> None:
        r = _minimal_record(links=[])
        assert Record.from_dict(r.to_dict()).links == []

    def test_links_roundtrips_nonempty(self) -> None:
        r = _minimal_record(links=["rec-2", "rec-3"])
        assert Record.from_dict(r.to_dict()).links == ["rec-2", "rec-3"]

    def test_supersedes_roundtrips_none(self) -> None:
        r = _minimal_record(supersedes=None)
        assert Record.from_dict(r.to_dict()).supersedes is None

    def test_supersedes_roundtrips_value(self) -> None:
        r = _minimal_record(supersedes="rec-old")
        assert Record.from_dict(r.to_dict()).supersedes == "rec-old"

    def test_lifecycle_roundtrips_active(self) -> None:
        r = _minimal_record(lifecycle="active")
        assert Record.from_dict(r.to_dict()).lifecycle == "active"

    def test_lifecycle_roundtrips_shadowed(self) -> None:
        r = _minimal_record(lifecycle="shadowed")
        assert Record.from_dict(r.to_dict()).lifecycle == "shadowed"

    def test_lifecycle_roundtrips_archived(self) -> None:
        r = _minimal_record(lifecycle="archived")
        assert Record.from_dict(r.to_dict()).lifecycle == "archived"

    def test_signal_roundtrips_none(self) -> None:
        r = _minimal_record(signal=None)
        assert Record.from_dict(r.to_dict()).signal is None

    def test_signal_roundtrips_value(self) -> None:
        r = _minimal_record(signal=0.73)
        assert Record.from_dict(r.to_dict()).signal == pytest.approx(0.73)

    def test_full_record_roundtrip(self) -> None:
        """All fields together survive a single round-trip."""
        r = _minimal_record(
            created="2025-03-01",
            last_recall="2026-06-15T09:00:00Z",
            recall_count=3,
            links=["rec-a", "rec-b"],
            supersedes="rec-old",
            lifecycle="active",
            signal=0.55,
            score=0.9,
        )
        d = r.to_dict()
        restored = Record.from_dict(d)
        assert restored.created == "2025-03-01"
        assert restored.last_recall == "2026-06-15T09:00:00Z"
        assert restored.recall_count == 3
        assert restored.links == ["rec-a", "rec-b"]
        assert restored.supersedes == "rec-old"
        assert restored.lifecycle == "active"
        assert restored.signal == pytest.approx(0.55)
        assert restored.score == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Acceptance criterion 2: legacy record loads with safe defaults
# ---------------------------------------------------------------------------


class TestLegacyBackCompat:
    """from_dict on a legacy dict (missing new keys) yields safe defaults."""

    def _legacy_dict(self) -> dict:
        """Minimal dict as a pre-t1 backend would have stored."""
        return {
            "id": "legacy-1",
            "text": "old fact",
            "type": "note",
            "hash": "deadbeef",
            "metadata": {"source": "old"},
            "scope": {"name": "default", "visibility": "public"},
        }

    def test_created_defaults_to_date_unknown_sentinel(self) -> None:
        r = Record.from_dict(self._legacy_dict())
        assert r.created == DATE_UNKNOWN

    def test_last_recall_defaults_to_none(self) -> None:
        r = Record.from_dict(self._legacy_dict())
        assert r.last_recall is None

    def test_recall_count_defaults_to_zero(self) -> None:
        r = Record.from_dict(self._legacy_dict())
        assert r.recall_count == 0

    def test_links_defaults_to_empty_list(self) -> None:
        r = Record.from_dict(self._legacy_dict())
        assert r.links == []

    def test_supersedes_defaults_to_none(self) -> None:
        r = Record.from_dict(self._legacy_dict())
        assert r.supersedes is None

    def test_lifecycle_defaults_to_active(self) -> None:
        r = Record.from_dict(self._legacy_dict())
        assert r.lifecycle == "active"

    def test_signal_defaults_to_none(self) -> None:
        r = Record.from_dict(self._legacy_dict())
        assert r.signal is None

    def test_legacy_score_field_absent_defaults_to_none(self) -> None:
        r = Record.from_dict(self._legacy_dict())
        assert r.score is None

    def test_legacy_record_is_fully_usable(self) -> None:
        """A legacy record round-trips after load (can be stored again cleanly)."""
        r = Record.from_dict(self._legacy_dict())
        d = r.to_dict()
        # All new keys must be present in the re-serialised dict
        assert "created" in d
        assert "last_recall" in d
        assert "recall_count" in d
        assert "links" in d
        assert "supersedes" in d
        assert "lifecycle" in d
        assert "signal" in d


# ---------------------------------------------------------------------------
# DATE_UNKNOWN sentinel is importable and is a string
# ---------------------------------------------------------------------------


def test_date_unknown_sentinel_is_string() -> None:
    assert isinstance(DATE_UNKNOWN, str)
    assert DATE_UNKNOWN  # not empty


def test_links_default_not_shared_mutable() -> None:
    """Two Records created with default links must NOT share the same list."""
    r1 = _minimal_record()
    r2 = _minimal_record()
    r1.links.append("x")
    assert "x" not in r2.links
