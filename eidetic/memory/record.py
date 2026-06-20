"""Record type and serialisation for eidetic memory."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from eidetic.memory.scope import Scope

# Sentinel used when a record has no known creation date.
# An undated record must not be penalised by age-decay; callers that implement
# signal computation treat this value as "decay-neutral" (no age penalty).
DATE_UNKNOWN: str = "date-unknown"


@dataclass
class Record:
    """A single memory record."""

    id: str
    text: str
    type: str
    hash: str
    metadata: dict[str, Any]
    scope: Scope
    score: float | None = None

    # -- t1: temporal + lifecycle fields ------------------------------------

    # ISO-8601 date/datetime string when the record was created, or the
    # DATE_UNKNOWN sentinel when the creation date is unavailable.
    created: str = DATE_UNKNOWN

    # ISO-8601 datetime string of the most recent recall, or None if the
    # record has never been recalled.  Bumped passively on every recall hit
    # (t3 implements the bump).
    last_recall: str | None = None

    # Running count of how many times this record has been recalled.
    recall_count: int = 0

    # IDs of related memory records (corroborating facts, predecessor/
    # successor records, linked claims).  Never use a mutable default arg —
    # use field(default_factory=list).
    links: list[str] = field(default_factory=list)

    # ID of the record this record explicitly supersedes (authoritative
    # conflict declaration).  None means no explicit supersession.
    supersedes: str | None = None

    # Lifecycle status.  One of "active", "shadowed", "archived".
    # "shadowed" and "archived" records are hidden from default recall but
    # still retrievable under explicit flags (--include-shadowed /
    # --include-archived).  Never hard-deleted.
    lifecycle: str = "active"

    # Computed signal strength scalar (float in roughly [0, 1]) or None when
    # not yet computed.  Set at query time by the signal function (t2); stored
    # here so recall output can expose it.  None is the "neutral" / not-yet-
    # computed sentinel — an absent signal must not bias ranking.
    signal: float | None = None

    # Attribution: the agent or caller that ingested this record.  Set by
    # `remember` at ingest time; None for legacy records that pre-date this
    # field.  Later tasks (t2+) stamp and persist this value; this field is
    # the envelope declaration only.
    added_by: str | None = None

    def __post_init__(self) -> None:
        if not self.hash:
            object.__setattr__(self, "hash", _hash_text(self.text))
        # `links` must always be a list: callers (and JSON carrying
        # "links": null or a non-list) can set it to None, which would crash
        # `len(record.links)` in signal scoring.  Normalise at this single
        # construction chokepoint so every code path is safe.
        if not isinstance(self.links, list):
            coerced = list(self.links) if isinstance(self.links, (tuple, set)) else []
            object.__setattr__(self, "links", coerced)

    @staticmethod
    def _hash(text: str) -> str:
        return _hash_text(text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "type": self.type,
            "hash": self.hash,
            "metadata": self.metadata,
            "scope": {
                "name": self.scope.name,
                "visibility": self.scope.visibility,
            },
            "score": self.score,
            # t1 fields
            "created": self.created,
            "last_recall": self.last_recall,
            "recall_count": self.recall_count,
            "links": self.links,
            "supersedes": self.supersedes,
            "lifecycle": self.lifecycle,
            "signal": self.signal,
            "added_by": self.added_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Record:
        scope_data = data["scope"]
        scope = Scope(name=scope_data["name"], visibility=scope_data["visibility"])
        return cls(
            id=data["id"],
            text=data["text"],
            type=data["type"],
            hash=data["hash"],
            metadata=data["metadata"],
            scope=scope,
            score=data.get("score"),
            # t1 fields — use .get() with safe defaults so legacy records load cleanly
            created=data.get("created", DATE_UNKNOWN),
            last_recall=data.get("last_recall"),
            recall_count=data.get("recall_count", 0),
            links=data.get("links", []),
            supersedes=data.get("supersedes"),
            lifecycle=data.get("lifecycle", "active"),
            signal=data.get("signal"),
            added_by=data.get("added_by"),
        )


def _hash_text(text: str) -> str:
    """Deterministic SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
