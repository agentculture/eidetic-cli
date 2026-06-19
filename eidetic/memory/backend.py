"""Backend protocol and registry for eidetic memory."""

from __future__ import annotations

from typing import Protocol

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


class Backend(Protocol):
    """Minimal interface for a memory storage backend."""

    def upsert(self, record: Record) -> None: ...

    def search(
        self,
        query: str,
        top_k: int,
        scope: Scope,
        filters: dict | None,
    ) -> list[Record]: ...


# -----------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------

_backends: dict[str, type[Backend]] = {}


def _register(name: str, cls: type[Backend]) -> None:
    _backends[name] = cls


def get_backend(name: str = "files") -> Backend:
    """Resolve a backend by name, raising :class:`CliError` on failure."""
    cls = _backends.get(name)
    if cls is None:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"unknown memory backend: {name!r}",
            remediation="available backends: files",
        )
    return cls()


# -----------------------------------------------------------------------
# In-memory placeholder for 'files' (real implementation is a later task)
# -----------------------------------------------------------------------


class _InMemoryBackend:
    """Minimal in-memory placeholder for the 'files' backend."""

    def __init__(self) -> None:
        self._store: list[Record] = []

    def upsert(self, record: Record) -> None:
        self._store.append(record)

    def search(
        self,
        query: str,
        top_k: int,
        scope: Scope,
        filters: dict | None,
    ) -> list[Record]:
        from eidetic.memory.scope import can_serve

        results = [r for r in self._store if can_serve(scope, r.scope)]
        return results[:top_k]


_register("files", _InMemoryBackend)
