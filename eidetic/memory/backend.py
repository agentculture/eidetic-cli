"""Backend protocol and registry for eidetic memory."""

from __future__ import annotations

import importlib
from typing import Protocol

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope

DEFAULT_BACKEND = "files"

_KNOWN_BACKENDS: set[str] = {"files", "neo4j", "mongo"}


class Backend(Protocol):
    """Minimal interface for a memory storage backend."""

    def upsert(self, record: Record) -> None: ...

    def search(
        self,
        query: str,
        top_k: int,
        scope: Scope,
        filters: dict | None,
        mode: str = "hybrid",
        *,
        alpha: float = 0.5,
        case_sensitive: bool = False,
    ) -> list[Record]: ...

    def all(self) -> list[Record]:
        """Enumerate every stored record across all scopes (no ranking/filtering).

        Unlike :meth:`search`, this performs no scope-visibility filtering — it is
        the maintenance/enumeration path (e.g. the ``sweep`` lifecycle pass) that
        must see every record, public and private alike, to reason about it. It
        never mutates the store.
        """
        ...


def get_backend(name: str = DEFAULT_BACKEND, **kwargs: object) -> Backend:
    """Resolve a backend by name, raising :class:`CliError` on failure.

    Extra ``kwargs`` are forwarded to the backend's ``build()`` factory. The only
    one in use today is ``timeout_ms`` — the ``overview`` store-probe passes a
    short connect timeout so a down mongo/neo4j fails fast instead of blocking on
    the default 5s server-selection timeout. Backends that don't take it (files)
    ignore it.
    """
    if name not in _KNOWN_BACKENDS:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"unknown memory backend: {name!r}",
            remediation=f"available backends: {', '.join(sorted(_KNOWN_BACKENDS))}",
        )
    try:
        module = importlib.import_module(f"eidetic.memory.backends.{name}")
    except ImportError as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"failed to load backend {name!r}: {exc}",
            remediation=f"install the optional driver for the {name!r} backend",
        ) from exc
    return module.build(**kwargs)  # type: ignore[no-any-return]
