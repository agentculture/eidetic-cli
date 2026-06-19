"""Scope isolation for eidetic memory records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Visibility = Literal["public", "private"]


@dataclass(frozen=True)
class Scope:
    """Named scope with a visibility policy."""

    name: str
    visibility: Visibility


DEFAULT: Scope = Scope(name="default", visibility="public")


def can_serve(query_scope: Scope, record_scope: Scope) -> bool:
    """Return True when *record_scope* may satisfy a query from *query_scope*.

    Public records are visible to any scope. A private record is served only to
    a query in the *same* scope (matching name AND visibility); it never leaks
    to a public scope or to any other scope.
    """
    if record_scope.visibility == "private":
        return query_scope == record_scope
    return True
