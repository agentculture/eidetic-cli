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

    A private scope's records never satisfy a query scoped elsewhere, and
    never a public scope.
    """
    if record_scope.visibility == "private" and record_scope.name != query_scope.name:
        return False
    return True
