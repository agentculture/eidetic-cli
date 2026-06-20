"""Tests for eidetic.memory.lifecycle — the PURE lifecycle engine.

These cover the t5 lifecycle rules with no I/O:

1. within-scope hybrid-conflict shadowing via the authoritative ``supersedes``
   link (cross-scope supersedes never shadows — the no-leak invariant);
2. age/signal archival with a protected-record exemption;
3. never-hard-delete (the engine only ever proposes ``lifecycle`` changes).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from eidetic.memory import lifecycle
from eidetic.memory.record import DATE_UNKNOWN, Record
from eidetic.memory.scope import Scope

_NOW = datetime(2026, 6, 20, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()

_PUBLIC = Scope(name="default", visibility="public")
_PRIVATE = Scope(name="default", visibility="private")
_OTHER = Scope(name="other", visibility="public")


def _rec(
    rid: str,
    text: str = "some text",
    *,
    scope: Scope | None = None,
    created: str = DATE_UNKNOWN,
    supersedes: str | None = None,
    lifecycle_status: str = "active",
    metadata: dict | None = None,
    recall_count: int = 0,
    last_recall: str | None = None,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata=metadata or {},
        scope=scope or _PUBLIC,
        created=created,
        supersedes=supersedes,
        lifecycle=lifecycle_status,
        recall_count=recall_count,
        last_recall=last_recall,
    )


def _days_ago(n: int) -> str:
    return (_NOW - timedelta(days=n)).isoformat()


# -- module constants exist and are tunable -----------------------------


def test_tunable_constants_present() -> None:
    assert lifecycle.ARCHIVE_AGE_DAYS == 365
    assert isinstance(lifecycle.ARCHIVE_SIGNAL_THRESHOLD, float)


# -- protected helper ----------------------------------------------------


def test_is_protected_truthy_metadata() -> None:
    assert lifecycle.is_protected(_rec("a", metadata={"protected": True}))
    assert lifecycle.is_protected(_rec("b", metadata={"protected": "yes"}))


def test_is_protected_falsy_or_absent() -> None:
    assert not lifecycle.is_protected(_rec("a", metadata={}))
    assert not lifecycle.is_protected(_rec("b", metadata={"protected": False}))
    assert not lifecycle.is_protected(_rec("c", metadata={"protected": 0}))


# -- rule 1: within-scope supersedes shadowing ---------------------------


def test_supersedes_shadows_same_scope_predecessor() -> None:
    newer = _rec("A", supersedes="B", created=_days_ago(1))
    older = _rec("B", created=_days_ago(2))
    result = lifecycle.compute_transitions([newer, older], _NOW_ISO)
    changed = {r.id: r.lifecycle for r in result.changed}
    assert changed.get("B") == "shadowed"
    # The superseding record itself is not shadowed.
    assert "A" not in changed or changed["A"] != "shadowed"


def test_supersedes_across_scopes_does_not_shadow() -> None:
    newer = _rec("A", supersedes="B", scope=_PUBLIC, created=_days_ago(1))
    older = _rec("B", scope=_PRIVATE, created=_days_ago(2))
    result = lifecycle.compute_transitions([newer, older], _NOW_ISO)
    changed = {r.id: r.lifecycle for r in result.changed}
    assert "B" not in changed  # cross-scope supersedes never shadows


def test_supersedes_across_scope_names_does_not_shadow() -> None:
    """Same visibility but different scope NAME must not shadow either."""
    newer = _rec("A", supersedes="B", scope=_PUBLIC, created=_days_ago(1))
    older = _rec("B", scope=_OTHER, created=_days_ago(2))
    result = lifecycle.compute_transitions([newer, older], _NOW_ISO)
    assert "B" not in {r.id for r in result.changed}


def test_supersedes_dangling_link_is_noop() -> None:
    """A supersedes pointing at a missing id changes nothing."""
    newer = _rec("A", supersedes="ghost", created=_days_ago(1))
    result = lifecycle.compute_transitions([newer], _NOW_ISO)
    assert result.changed == []


def test_protected_predecessor_is_not_shadowed() -> None:
    newer = _rec("A", supersedes="B", created=_days_ago(1))
    older = _rec("B", created=_days_ago(2), metadata={"protected": True})
    result = lifecycle.compute_transitions([newer, older], _NOW_ISO)
    assert "B" not in {r.id for r in result.changed}


# -- rule 2: archival by age OR signal -----------------------------------


def test_archives_record_older_than_age_threshold() -> None:
    old = _rec("old", created=_days_ago(lifecycle.ARCHIVE_AGE_DAYS + 5))
    result = lifecycle.compute_transitions([old], _NOW_ISO)
    changed = {r.id: r.lifecycle for r in result.changed}
    assert changed.get("old") == "archived"


def test_does_not_archive_recent_strong_record() -> None:
    fresh = _rec("fresh", created=_days_ago(10), recall_count=5)
    result = lifecycle.compute_transitions([fresh], _NOW_ISO)
    assert "fresh" not in {r.id for r in result.changed}


def test_archives_record_below_signal_threshold() -> None:
    """A record whose signal_strength is below threshold archives even if young."""
    # Force a low signal via a very stale last_recall against a young created date.
    weak = _rec(
        "weak",
        created=_days_ago(5),
        last_recall=_days_ago(5000),
    )
    result = lifecycle.compute_transitions([weak], _NOW_ISO)
    changed = {r.id: r.lifecycle for r in result.changed}
    assert changed.get("weak") == "archived"


def test_date_unknown_is_age_neutral_not_archived_by_age() -> None:
    """DATE_UNKNOWN must not archive by age (only by signal, if low)."""
    undated = _rec("u", created=DATE_UNKNOWN)
    result = lifecycle.compute_transitions([undated], _NOW_ISO)
    # A neutral undated record has a mid signal and no age -> stays active.
    assert "u" not in {r.id for r in result.changed}


def test_protected_record_never_archived_by_age() -> None:
    old = _rec(
        "p",
        created=_days_ago(lifecycle.ARCHIVE_AGE_DAYS + 100),
        metadata={"protected": True},
    )
    result = lifecycle.compute_transitions([old], _NOW_ISO)
    assert "p" not in {r.id for r in result.changed}


def test_protected_record_never_archived_by_signal() -> None:
    weak = _rec(
        "p",
        created=_days_ago(5),
        last_recall=_days_ago(5000),
        metadata={"protected": True},
    )
    result = lifecycle.compute_transitions([weak], _NOW_ISO)
    assert "p" not in {r.id for r in result.changed}


# -- rule 3: never hard-delete -------------------------------------------


def test_engine_only_changes_lifecycle_never_removes() -> None:
    """Every input id is preserved; the engine only flips lifecycle values."""
    recs = [
        _rec("A", supersedes="B", created=_days_ago(1)),
        _rec("B", created=_days_ago(2)),
        _rec("old", created=_days_ago(lifecycle.ARCHIVE_AGE_DAYS + 1)),
        _rec("fresh", created=_days_ago(1), recall_count=3),
    ]
    result = lifecycle.compute_transitions(recs, _NOW_ISO)
    # changed records are a subset of inputs (no fabricated ids).
    input_ids = {r.id for r in recs}
    assert {r.id for r in result.changed}.issubset(input_ids)
    # changed only ever carries non-active lifecycle states.
    assert all(r.lifecycle in ("shadowed", "archived") for r in result.changed)


def test_already_shadowed_record_not_reported_again() -> None:
    """A record already in its target state is not re-reported as a change."""
    newer = _rec("A", supersedes="B", created=_days_ago(1))
    older = _rec("B", created=_days_ago(2), lifecycle_status="shadowed")
    result = lifecycle.compute_transitions([newer, older], _NOW_ISO)
    assert "B" not in {r.id for r in result.changed}


# -- suggestions (returned, never auto-applied) --------------------------


def test_conflict_suggestions_are_returned_not_applied() -> None:
    """High-overlap same-scope records may be SUGGESTED, never auto-shadowed."""
    a = _rec("a", text="The sky is blue today", created=_days_ago(1))
    b = _rec("b", text="The sky is blue today", created=_days_ago(1))
    result = lifecycle.compute_transitions([a, b], _NOW_ISO)
    # Neither is auto-shadowed (no supersedes link between them).
    assert "a" not in {r.id for r in result.changed}
    assert "b" not in {r.id for r in result.changed}
    # But a suggestion may be emitted for human confirmation.
    assert isinstance(result.suggestions, list)


def test_no_cross_scope_conflict_suggestions() -> None:
    """Suggestions never span scopes (preserves no-leak)."""
    a = _rec("a", text="identical claim", scope=_PUBLIC, created=_days_ago(1))
    b = _rec("b", text="identical claim", scope=_PRIVATE, created=_days_ago(1))
    result = lifecycle.compute_transitions([a, b], _NOW_ISO)
    for s in result.suggestions:
        # No suggestion pairs a public id with a private id.
        assert not ({"a", "b"} <= set(s.get("ids", [])))
