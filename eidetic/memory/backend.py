"""Backend adapter: eidetic delegates storage to ``data_refinery.store``.

eidetic no longer owns its own storage engine. Instead it adapts
``data_refinery.store`` — an opaque, scope-aware key-value store — and keeps
all memory semantics here: the record schema, the four recall ranking modes,
freshness scoring, and the lifecycle state machine.

The store has no search capability of its own, so ``search`` fetches the
full candidate set for a scope via ``drstore.list(scope)`` and ranks in-process
using :func:`eidetic.memory.scoring.rank`.

Mapping layer:
    - :func:`record_to_envelope` serialises an eidetic :class:`~eidetic.memory.record.Record`
      into a ``data_refinery.store.Envelope`` (score/signal are NOT stored —
      they are query-time artefacts only).
    - :func:`record_from_envelope` deserialises in the reverse direction.

The three old backend modules (``backends/files.py``, ``backends/mongo.py``,
``backends/neo4j.py``) have been deleted; their storage logic now lives inside
data-refinery. See issue #13 for the migration context.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Protocol

import data_refinery.store as drstore
from data_refinery.cli._errors import CliError as _DRCliError
from data_refinery.store import Envelope
from data_refinery.store import Scope as DRScope

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.embed import EmbedClient
from eidetic.memory.record import DATE_UNKNOWN, Record
from eidetic.memory.scope import Scope, can_serve
from eidetic.memory.scoring import rank

DEFAULT_BACKEND = "files"

# The three real stores data-refinery resolves. "graph" is *not* here — it is a
# CLI-facing alias for "neo4j" (see _BACKEND_ALIASES / BACKEND_CHOICES).
_KNOWN_BACKENDS: set[str] = {"files", "mongo", "neo4j"}

# CLI-facing aliases. "graph" is the operator-preferred display label for the
# neo4j store; it resolves to the same backend. Kept so a single backend token
# works on every verb (issue #12) without breaking existing "graph" usage.
_BACKEND_ALIASES: dict[str, str] = {"graph": "neo4j"}

# The accepted `--backend` tokens, exposed as ONE list so every verb's
# `choices=` stays in lockstep — the duplicated, drifting per-verb choice lists
# were the root cause of issue #12. Order is display order (real stores first,
# then the alias).
BACKEND_CHOICES: tuple[str, ...] = ("files", "mongo", "neo4j", "graph")


# ---------------------------------------------------------------------------
# Protocol — preserved exactly for all existing callers
# ---------------------------------------------------------------------------


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

        Unlike :meth:`search`, this performs no scope-visibility filtering — it
        is the maintenance/enumeration path (e.g. the ``sweep`` lifecycle pass)
        that must see every record, public and private alike, to reason about it.
        It never mutates the store.
        """
        ...


# ---------------------------------------------------------------------------
# Environment bridge
# ---------------------------------------------------------------------------


def _bridge_env(name: str, *, data_dir: str | None = None) -> None:
    """Map eidetic's historical env vars onto data-refinery's ``DR_*`` names.

    Called unconditionally before every store operation so that the *current*
    value of eidetic's env vars is always reflected — tests set
    ``os.environ["EIDETIC_DATA_DIR"]`` directly and expect each
    ``get_backend("files")`` call to pick it up without stale leakage.

    - ``files``:  ``DR_DATA_DIR`` is set to *data_dir* when given, otherwise
      ``EIDETIC_DATA_DIR`` when present, otherwise the default
      ``~/.eidetic/memory``. The assignment is always made (unconditional) so a
      stale ``DR_DATA_DIR`` left by a prior test never wins.
    - ``mongo``:  ``DR_MONGO_URI`` is forwarded from ``EIDETIC_MONGO_URI`` when set.
    - ``neo4j``:  ``DR_NEO4J_URI`` is forwarded from ``NEO4J_URI`` when set.
    """
    if name == "files":
        if data_dir is not None:
            os.environ["DR_DATA_DIR"] = data_dir
        else:
            os.environ["DR_DATA_DIR"] = os.environ.get("EIDETIC_DATA_DIR") or _home_store_dir()
    elif name == "mongo":
        eidetic_mongo = os.environ.get("EIDETIC_MONGO_URI")
        if eidetic_mongo:
            os.environ["DR_MONGO_URI"] = eidetic_mongo
    elif name == "neo4j":
        neo4j_uri = os.environ.get("NEO4J_URI")
        if neo4j_uri:
            os.environ["DR_NEO4J_URI"] = neo4j_uri


# ---------------------------------------------------------------------------
# Store-path resolver helpers
# ---------------------------------------------------------------------------

# Module-level cache keyed by cwd for _git_toplevel.
_GIT_CACHE: dict[str, str | None] = {}

# The per-base store layout, defined once so the ".eidetic"/"memory" path
# components never drift across the resolver helpers (and _bridge_env).
_STORE_SUBPATH = (".eidetic", "memory")


def _store_dir(base: Path) -> str:
    """Return the eidetic store directory under *base* (``<base>/.eidetic/memory``)."""
    return str(base.joinpath(*_STORE_SUBPATH))


def _home_store_dir() -> str:
    """Return the default home-based store directory path."""
    return _store_dir(Path.home())


def _override_dir() -> str | None:
    """Return the explicit ``EIDETIC_DATA_DIR`` override, or ``None``."""
    return os.environ.get("EIDETIC_DATA_DIR") or None


def _git_toplevel() -> str | None:
    """Return the git repo toplevel for the current working directory.

    Returns ``None`` when outside a repo, git is unavailable, or any error
    occurs. Never raises. Results are cached per-cwd so a batch ingest
    spawns at most one git subprocess, while ``os.chdir`` to a different
    directory gets a fresh result.
    """
    try:
        cwd = os.getcwd()
    except OSError:
        return None
    if cwd in _GIT_CACHE:
        return _GIT_CACHE[cwd]
    try:
        # `git` is intentionally resolved from PATH (a hard-coded absolute path
        # would not be portable across dev/CI/install environments); the argv is
        # a fixed literal with no user input, so there is no injection surface.
        result = subprocess.run(  # nosec B607
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            _GIT_CACHE[cwd] = result.stdout.strip()
            return _GIT_CACHE[cwd]
        _GIT_CACHE[cwd] = None
        return None
    except OSError:
        _GIT_CACHE[cwd] = None
        return None


def _resolve_write_dir(visibility: str) -> str:
    """Resolve the write directory for a record with the given *visibility*.

    Precedence:
    1. ``EIDETIC_DATA_DIR`` override (if set)
    2. Repo ``.eidetic/memory`` for public records inside a git repo
    3. Home ``~/.eidetic/memory`` (private records, or outside a repo)
    """
    override = _override_dir()
    if override:
        return override
    if visibility == "public":
        top = _git_toplevel()
        if top:
            return _store_dir(Path(top))
    return _home_store_dir()


def _candidate_read_dirs() -> list[str]:
    """Return the list of directories to search for multi-store reads.

    When ``EIDETIC_DATA_DIR`` is set, returns a single-element list
    (byte-identical to today's behaviour). Otherwise returns home plus
    the repo store (if inside a git repo), with no duplicates.
    """
    override = _override_dir()
    if override:
        return [override]
    dirs: list[str] = [_home_store_dir()]
    top = _git_toplevel()
    if top:
        repo = _store_dir(Path(top))
        if repo not in dirs:
            dirs.append(repo)
    return dirs


# ---------------------------------------------------------------------------
# CliError translation
# ---------------------------------------------------------------------------


@contextmanager
def _translate_errors() -> Generator[None, None, None]:
    """Convert ``data_refinery`` :class:`CliError` into eidetic's own variant.

    data-refinery raises ``data_refinery.cli._errors.CliError`` which is
    structurally identical to eidetic's own ``CliError`` (same attributes:
    ``code``, ``message``, ``remediation``), but a different class object.
    This context manager re-raises as eidetic's ``CliError`` so callers never
    see a foreign exception type.
    """
    try:
        yield
    except _DRCliError as exc:
        raise CliError(
            code=exc.code,
            message=exc.message,
            remediation=exc.remediation,
        ) from exc


# ---------------------------------------------------------------------------
# Mapping functions (public — imported by migration modules)
# ---------------------------------------------------------------------------


def record_to_envelope(record: Record) -> Envelope:
    """Serialise a :class:`Record` into a data-refinery :class:`Envelope`.

    score and signal are intentionally excluded — they are query-time output
    artefacts and must never be persisted to the store.
    """
    metadata: dict[str, Any] = {
        "type": record.type,
        "record_metadata": record.metadata,
        "created": record.created,
        "last_recall": record.last_recall,
        "recall_count": record.recall_count,
        "links": record.links,
        "supersedes": record.supersedes,
        "lifecycle": record.lifecycle,
        "added_by": record.added_by,
    }
    return Envelope(
        id=record.id,
        content=record.text,
        hash=record.hash,
        scope=DRScope(name=record.scope.name, visibility=record.scope.visibility),
        metadata=metadata,
    )


def record_from_envelope(env: Envelope) -> Record:
    """Deserialise a data-refinery :class:`Envelope` into a :class:`Record`.

    score and signal are left at their defaults (None) — they are never read
    from stored metadata.
    """
    m = env.metadata or {}
    return Record(
        id=env.id,
        text=env.content,
        type=m.get("type", ""),
        hash=env.hash,
        metadata=m.get("record_metadata") or {},
        scope=Scope(name=env.scope.name, visibility=env.scope.visibility),
        created=m.get("created", DATE_UNKNOWN),
        last_recall=m.get("last_recall"),
        recall_count=m.get("recall_count", 0),
        links=m.get("links") or [],
        supersedes=m.get("supersedes"),
        lifecycle=m.get("lifecycle", "active"),
        added_by=m.get("added_by"),
    )


# ---------------------------------------------------------------------------
# StoreBackend — implements the Backend protocol via data_refinery.store
# ---------------------------------------------------------------------------


class StoreBackend:
    """A :class:`Backend` implementation that delegates storage to ``data_refinery.store``.

    All memory semantics (ranking, lifecycle filtering, freshness signal) stay
    in eidetic. The store is used as a pure opaque key-value layer.
    """

    def __init__(self, name: str, **kwargs: object) -> None:
        self._name = name
        self._kwargs = kwargs
        self._embed = EmbedClient()

    def upsert(self, record: Record) -> None:
        """Idempotently upsert *record* into the store (by id; dedup by hash within scope)."""
        if self._name == "files":
            _bridge_env(self._name, data_dir=_resolve_write_dir(record.scope.visibility))
        else:
            _bridge_env(self._name)
        with _translate_errors():
            drstore.put(record_to_envelope(record), backend=self._name, **self._kwargs)

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
    ) -> list[Record]:
        """Return the top-*k* records for *query* under *scope*, ranked by *mode*.

        Candidates are fetched from the store via ``drstore.list(scope)`` (which
        applies scope-visibility rules) then ranked in-process by
        :func:`eidetic.memory.scoring.rank`. Facet *filters* are applied before
        ranking: only records where every ``record.metadata[key] == value`` pass.
        """
        if self._name == "files":
            # Gather candidates from every dir in _candidate_read_dirs(), union by id.
            # Only serveable copies enter the dedup map (first-dir-wins among
            # serveable copies; home before repo). Applying can_serve inside the
            # loop ensures a non-serveable duplicate can never shadow a serveable
            # one.
            seen: dict[str, Record] = {}
            for d in _candidate_read_dirs():
                _bridge_env("files", data_dir=d)
                with _translate_errors():
                    for env in drstore.list(
                        scope=DRScope(name=scope.name, visibility=scope.visibility),
                        backend="files",
                        **self._kwargs,
                    ):
                        r = record_from_envelope(env)
                        if not can_serve(scope, r.scope):
                            continue
                        seen.setdefault(r.id, r)
            candidates = list(seen.values())
        else:
            _bridge_env(self._name)
            with _translate_errors():
                envs = drstore.list(
                    scope=DRScope(name=scope.name, visibility=scope.visibility),
                    backend=self._name,
                    **self._kwargs,
                )
            candidates = [record_from_envelope(e) for e in envs]
        # Defense in depth: data-refinery's list() already enforces scope
        # visibility via its own can_serve, but re-applying eidetic's policy here
        # makes the public/private no-leak invariant hold *in eidetic* regardless
        # of the store's behavior — the invariant is security-critical, so it is
        # enforced on both sides of the boundary.
        candidates = [r for r in candidates if can_serve(scope, r.scope)]
        if filters:
            candidates = [
                r for r in candidates if all(r.metadata.get(k) == v for k, v in filters.items())
            ]
        return rank(
            mode,
            query,
            candidates,
            self._embed,
            top_k,
            alpha=alpha,
            case_sensitive=case_sensitive,
        )

    def all(self) -> list[Record]:
        """Enumerate every stored record across all scopes (no ranking or filtering).

        Uses ``drstore.get_backend(name).all()`` which bypasses scope-visibility
        rules — required for the ``sweep`` lifecycle pass that must see every
        record (public and private) to evaluate transitions.
        """
        if self._name == "files":
            # Enumerate across every dir in _candidate_read_dirs(), union by id.
            seen: dict[str, Record] = {}
            for d in _candidate_read_dirs():
                _bridge_env("files", data_dir=d)
                with _translate_errors():
                    backend = drstore.get_backend("files", **self._kwargs)
                    for env in backend.all():
                        r = record_from_envelope(env)
                        seen.setdefault(r.id, r)
            return list(seen.values())
        else:
            _bridge_env(self._name)
            with _translate_errors():
                backend = drstore.get_backend(self._name, **self._kwargs)
                return [record_from_envelope(e) for e in backend.all()]


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def get_backend(name: str = DEFAULT_BACKEND, **kwargs: object) -> Backend:
    """Resolve a named backend, raising :class:`CliError` on failure.

    Extra ``kwargs`` are forwarded to the underlying data-refinery backend
    factory. The only one in use today is ``timeout_ms`` — the ``overview``
    store-probe passes a short connect timeout so a down mongo/neo4j fails fast
    rather than blocking on the default server-selection timeout. Backends that
    don't accept it (files) ignore it.

    The CLI alias ``graph`` resolves to ``neo4j`` (issue #12) before validation,
    so every verb's ``--backend`` accepts the same token set.
    """
    name = _BACKEND_ALIASES.get(name, name)
    if name not in _KNOWN_BACKENDS:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"unknown memory backend: {name!r}",
            remediation=f"available backends: {', '.join(sorted(_KNOWN_BACKENDS))}",
        )
    return StoreBackend(name, **kwargs)


# ---------------------------------------------------------------------------
# Store migration — delegated to data-refinery (eidetic constructs no paths)
# ---------------------------------------------------------------------------


def _legacy_line_to_envelope(obj: dict[str, Any]) -> Envelope:
    """Transform one decoded *legacy* Record-JSONL line into an :class:`Envelope`.

    Handed to ``data_refinery.store.migrate`` as the consumer transform.
    data-refinery only routes *legacy* lines here — an already-canonical Envelope
    line is detected (``Envelope.from_dict(obj).to_dict() == obj``) and kept
    verbatim, never re-fed through the transform — so this need not be idempotent
    and need not recognise the Envelope shape itself. A legacy line carries a
    top-level ``text`` (not ``content``), so it never round-trips as an Envelope
    and always reaches this transform exactly once.

    A malformed legacy line makes ``Record.from_dict`` raise ``KeyError`` (missing
    a required field). data-refinery's migrate loop wraps that into a structured
    "corrupt line" :class:`CliError` (exit 2), which :func:`migrate_store`'s
    ``_translate_errors`` re-raises as eidetic's own — so no raw traceback ever
    escapes. (Covered by ``test_migrate_corrupt_record_fields_raises_cli_error``.)
    """
    return record_to_envelope(Record.from_dict(obj))


def migrate_store(data_dir: str | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """Upgrade an on-disk *files* store from legacy Record-JSONL to Envelope-JSONL.

    eidetic constructs **no filesystem write path**: it delegates the rewrite to
    ``data_refinery.store.migrate``, supplying only the record->Envelope
    transform and the store root it already owns (*data_dir*). data-refinery —
    which owns the store layout — resolves paths, validates the whole store, and
    rewrites **atomically per file**. This is what removed the Sonar S2083
    write-path sink from eidetic (issue #8); the operation is **idempotent** (a
    re-run rewrites nothing) and abort-safe, both guaranteed by data-refinery.

    *data_dir* defaults (via :func:`_bridge_env`) to ``EIDETIC_DATA_DIR`` else
    ``~/.eidetic/memory`` — the same store ``remember``/``recall`` use. Returns
    data-refinery's summary dict ``{backend, files, migrated, migrated_files,
    skipped, dry_run}``. data-refinery's :class:`CliError` is re-raised as
    eidetic's own variant so callers never see a foreign exception.
    """
    _bridge_env("files")
    with _translate_errors():
        return drstore.migrate(
            transform=_legacy_line_to_envelope,
            backend="files",
            base_dir=data_dir,
            dry_run=dry_run,
        )
