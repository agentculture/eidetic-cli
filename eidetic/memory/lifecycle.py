"""Pure lifecycle engine for eidetic memory (t5).

Given a flat list of records (already gathered from a store) plus a ``now``,
compute which records should transition to a non-active ``lifecycle`` state.
This module performs **no I/O**: it never reads the clock, never touches a
backend, never deletes anything. It only inspects records and returns a plan.
The companion ``sweep`` command (:mod:`eidetic.cli._commands.sweep`) is the one
place that loads records, calls this engine, and persists the result.

Rules (see CLAUDE.md / t5 spec):

1. **Within-scope hybrid-conflict shadowing.** The explicit ``supersedes`` link
   is AUTHORITATIVE and the ONLY thing that auto-shadows. If record ``A`` has
   ``A.supersedes == B.id`` and ``A`` and ``B`` share the SAME scope (same name
   AND visibility), ``B`` is marked ``shadowed``. A ``supersedes`` link that
   crosses scopes never shadows — this preserves the public/private no-leak
   invariant. eidetic may additionally SUGGEST likely conflicts (high text
   overlap between two same-scope records), but suggestions are only returned for
   human confirmation, never auto-applied.

2. **Archival.** A record is marked ``archived`` when it is older than
   :data:`ARCHIVE_AGE_DAYS` against its ``created`` date (``DATE_UNKNOWN`` is
   age-neutral → never archived by age) OR its
   :func:`~eidetic.memory.scoring.signal_strength` falls below
   :data:`ARCHIVE_SIGNAL_THRESHOLD`.

3. **Never hard-delete.** The engine only ever proposes ``lifecycle`` changes; a
   shadowed/archived record is preserved and still persisted by the caller.

PROTECTED records — those whose ``metadata`` carries a truthy ``"protected"``
key (see :func:`is_protected`) — are EXEMPT: they are never shadowed and never
archived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from eidetic.memory.record import DATE_UNKNOWN, Record
from eidetic.memory.scope import Scope
from eidetic.memory.scoring import signal_strength

# -- tunable module constants -------------------------------------------
#
# Single source of truth so archival policy can be tuned without touching the
# engine body or any call site.

# A record older than this many days (against ``created``) is archived by age.
# DATE_UNKNOWN records have no age and are never archived by this rule.
ARCHIVE_AGE_DAYS: int = 365

# A record whose signal_strength falls below this floor is archived even if it
# is younger than ARCHIVE_AGE_DAYS. The signal model lives in scoring.py; this
# threshold sits well below its neutral midpoint (0.5) so an undated/neutral
# record is NOT archived by signal — only genuinely stale/decayed records are.
ARCHIVE_SIGNAL_THRESHOLD: float = 0.25

# Metadata key that marks a record as core/protected (exempt from all
# transitions). Defined here so the contract is in exactly one place.
PROTECTED_KEY: str = "protected"


@dataclass
class LifecycleResult:
    """Outcome of :func:`compute_transitions`.

    ``changed`` is the subset of input records whose ``lifecycle`` was flipped to
    a non-active state (``shadowed`` or ``archived``). Each entry is the SAME
    record object as the input, mutated in place — the caller persists it.

    ``suggestions`` are advisory conflict hints for human confirmation; they are
    NEVER auto-applied. Each is a small dict: ``{"reason", "ids", "scope"}``.
    """

    changed: list[Record] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)


def is_protected(record: Record) -> bool:
    """True when *record* is core/protected and exempt from all transitions.

    A record is protected when its ``metadata`` carries a truthy value under
    :data:`PROTECTED_KEY` (e.g. ``{"protected": True}``). Falsy values
    (``False``, ``0``, ``""``) and an absent key mean "not protected".
    """
    return bool(record.metadata.get(PROTECTED_KEY))


def _same_scope(a: Scope, b: Scope) -> bool:
    """True when two scopes match on BOTH name and visibility."""
    return a.name == b.name and a.visibility == b.visibility


def _archive_reason(record: Record, now: str | datetime) -> str | None:
    """Return why *record* should archive (``"age"``/``"signal"``) or ``None``.

    Age is evaluated only for dated records (DATE_UNKNOWN is age-neutral). Signal
    is always evaluated; a record below the threshold archives regardless of age.
    """
    created = signal_age_days(record, now)
    if created is not None and created > ARCHIVE_AGE_DAYS:
        return "age"
    if signal_strength(record, now) < ARCHIVE_SIGNAL_THRESHOLD:
        return "signal"
    return None


def signal_age_days(record: Record, now: str | datetime) -> float | None:
    """Age of *record* in days against ``now``, or ``None`` when undated.

    Reuses scoring.py's tolerant ISO parser so DATE_UNKNOWN / unparseable dates
    return ``None`` (age-neutral) rather than raising.
    """
    # Imported lazily-by-reference from scoring to keep one parser.
    from eidetic.memory.scoring import _days_between, _parse_dt

    if record.created == DATE_UNKNOWN:
        return None
    created_dt = _parse_dt(record.created)
    now_dt = _parse_dt(now) if isinstance(now, str) else now
    if created_dt is None or now_dt is None:
        return None
    return _days_between(now_dt, created_dt)


def _conflict_suggestions(records: list[Record]) -> list[dict]:
    """Advisory same-scope text-overlap conflicts — returned, never applied.

    Two active, non-protected records in the SAME scope with identical
    normalised text are flagged as a possible conflict for a human to confirm
    (e.g. one may supersede the other). Cross-scope pairs are never compared, so
    no suggestion can couple a public id to a private one (no-leak invariant).
    Conservative on purpose: exact normalised-text equality, not fuzzy overlap,
    so sweep never spams the operator with weak guesses.
    """
    suggestions: list[dict] = []
    # Group by (scope, normalised text); any group with >1 distinct id collides.
    buckets: dict[tuple[str, str, str], list[Record]] = {}
    for rec in records:
        if rec.lifecycle != "active" or is_protected(rec):
            continue
        key = (rec.scope.name, rec.scope.visibility, rec.text.strip().casefold())
        buckets.setdefault(key, []).append(rec)
    for (name, visibility, _norm), group in buckets.items():
        ids = sorted({r.id for r in group})
        if len(ids) > 1:
            suggestions.append(
                {
                    "reason": "same-scope text overlap (possible conflict)",
                    "ids": ids,
                    "scope": {"name": name, "visibility": visibility},
                }
            )
    return suggestions


def _apply_supersedes_shadowing(
    records: list[Record],
    by_id: dict[str, Record],
    mark: Callable[[Record, str], None],
) -> None:
    """Rule 1: authoritative within-scope ``supersedes`` shadowing.

    Marks each superseded same-scope, non-protected target ``shadowed``. A
    dangling or cross-scope link is a no-op (the latter preserves the no-leak
    invariant).
    """
    for rec in records:
        if not rec.supersedes:
            continue
        target = by_id.get(rec.supersedes)
        if target is None:
            continue  # dangling link → no-op
        if not _same_scope(rec.scope, target.scope):
            continue  # cross-scope supersedes never shadows (no-leak)
        if is_protected(target):
            continue  # protected predecessors are never shadowed
        mark(target, "shadowed")


def _apply_archival(
    records: list[Record],
    now: str | datetime,
    already_changed: set[str],
    mark: Callable[[Record, str], None],
) -> None:
    """Rule 2: age/signal archival. Protected and already-shadowed records are
    skipped; everything else past the age/signal threshold is marked
    ``archived``."""
    for rec in records:
        if is_protected(rec):
            continue
        if rec.id in already_changed:
            continue  # already shadowed this pass; don't also archive
        if _archive_reason(rec, now) is not None:
            mark(rec, "archived")


def compute_transitions(records: list[Record], now: str | datetime) -> LifecycleResult:
    """Compute lifecycle transitions for *records* against *now* (PURE).

    Applies rule 1 (within-scope ``supersedes`` shadowing) then rule 2 (age/
    signal archival), skipping protected records entirely. Mutates the
    ``lifecycle`` field of each transitioned record in place and collects it in
    :class:`LifecycleResult.changed` (only when the value actually changes — a
    record already in its target state is not re-reported). Never removes a
    record. Conflict suggestions (advisory) are gathered separately.

    ``now`` is supplied by the caller (ISO string or datetime); this function
    never reads the clock, so the result is deterministic and testable.
    """
    by_id: dict[str, Record] = {r.id: r for r in records}
    changed: list[Record] = []
    seen_changed: set[str] = set()

    def _mark(record: Record, status: str) -> None:
        if record.lifecycle == status or record.id in seen_changed:
            return
        record.lifecycle = status
        seen_changed.add(record.id)
        changed.append(record)

    _apply_supersedes_shadowing(records, by_id, _mark)
    _apply_archival(records, now, seen_changed, _mark)

    suggestions = _conflict_suggestions(records)
    return LifecycleResult(changed=changed, suggestions=suggestions)
