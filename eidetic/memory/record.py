"""Record type and serialisation for eidetic memory."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from eidetic.memory.scope import Scope


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

    def __post_init__(self) -> None:
        if not self.hash:
            object.__setattr__(self, "hash", _hash_text(self.text))

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
        )


def _hash_text(text: str) -> str:
    """Deterministic SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
