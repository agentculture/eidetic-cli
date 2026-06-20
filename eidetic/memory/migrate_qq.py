"""One-shot importer for the legacy "QQ" memory sources (t6).

Reads the three QQ knowledge layers and yields eidetic :class:`Record`s:

* **files** — ``core.md`` / ``notes.md`` markdown (sections under ``##``
  headings). These hold PERSONAL/identity facts.
* **mongo** — the ``claude_notes`` collection (text, importance, access_count,
  last_accessed, embedding).
* **neo4j** — entities/relationships tagged ``knowledge_context="claude"``
  (description, mention_count, last_seen, verified, source_history).

Each source reader is GUARDED: the Mongo/Neo4j connectors wrap import/connect in
try/except and, on any failure (server down, import error, missing files), emit
a WARNING to stderr and SKIP that source — they never abort the whole run.

No-leak invariant: QQ files/core.md hold PERSONAL data, so callers migrate into
a PRIVATE scope by default. The mappers carry the caller-supplied scope verbatim
onto every record; the command defaults that scope to ``qq``/``private``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eidetic.cli._output import emit_diagnostic
from eidetic.memory.backend import Backend
from eidetic.memory.record import DATE_UNKNOWN, Record
from eidetic.memory.scope import Scope

# Provenance source tags (also used as report keys).
SRC_FILES = "qq-files"
SRC_MONGO = "qq-mongo"
SRC_NEO4J = "qq-neo4j"

# Default QQ markdown sources on this machine (personal knowledge).
DEFAULT_FILE_PATHS: tuple[str, ...] = (
    str(Path.home() / ".claude" / "skills" / "memory" / "references" / "core.md"),
    str(Path.home() / ".claude" / "skills" / "memory" / "references" / "notes.md"),
)


def _warn(source: str, reason: str) -> None:
    """Emit a skip warning for *source* to stderr (never raises)."""
    emit_diagnostic(f"warning: skipping source {source}: {reason}")


def _slug(text: str) -> str:
    """Lowercase, dash-joined slug for a stable per-section id fragment."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "section"


# --------------------------------------------------------------------------- #
# Files reader
# --------------------------------------------------------------------------- #


def _iter_sections(markdown: str) -> Iterator[tuple[str, str]]:
    """Yield ``(section_title, body)`` for each ``##`` heading in *markdown*.

    The leading H1/preamble (before the first ``##``) is ignored — those QQ
    files open with a ``# Title`` + an "Last updated" line, not memory content.
    """
    title: str | None = None
    buf: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if title is not None:
                yield title, "\n".join(buf).strip()
            title = line[3:].strip()
            buf = []
        elif title is not None:
            buf.append(line)
    if title is not None:
        yield title, "\n".join(buf).strip()


def read_files(
    paths: Iterable[str] | None = None,
    *,
    scope: Scope,
    status: dict[str, bool] | None = None,
) -> Iterator[Record]:
    """Yield one :class:`Record` per ``##`` section of each QQ markdown file.

    Guarded per-file: a missing/unreadable file is warned-about and skipped, so
    one absent source never aborts the whole files pass. When *status* is given,
    its ``"available"`` key is set True as soon as at least one file is readable.
    """
    if status is not None:
        status["available"] = False
    file_paths = list(paths) if paths is not None else list(DEFAULT_FILE_PATHS)
    for raw_path in file_paths:
        path = Path(raw_path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _warn(SRC_FILES, f"cannot read {raw_path}: {exc}")
            continue
        if status is not None:
            status["available"] = True
        # File mtime is the date signature for everything in this file.
        try:
            mtime = path.stat().st_mtime
            created = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except OSError:
            created = DATE_UNKNOWN
        for title, body in _iter_sections(text):
            if not body:
                continue
            yield _map_file_section(raw_path, title, body, created, scope)


def _map_file_section(
    path: str,
    section: str,
    body: str,
    created: str,
    scope: Scope,
) -> Record:
    rid = f"qq-file:{path}#{_slug(section)}"
    metadata: dict[str, Any] = {
        "source": SRC_FILES,
        "path": path,
        "section": section,
    }
    return Record(
        id=rid,
        text=f"{section}\n{body}",
        type="qq_note",
        hash="",
        metadata=metadata,
        scope=scope,
        created=created,
        recall_count=0,
    )


# --------------------------------------------------------------------------- #
# Mongo reader (guarded)
# --------------------------------------------------------------------------- #


def _mongo_collection() -> Any:
    """Connect to the QQ Mongo ``claude_notes`` collection.

    Isolated so tests can patch it to raise (simulating a down/absent server)
    without ever importing pymongo or contacting a real Mongo.
    """
    from pymongo import MongoClient  # local import: only when actually migrating

    uri = os.environ.get("EIDETIC_MONGO_URI") or "mongodb://localhost:27017"
    db = os.environ.get("QQ_MONGO_DB", "qq_memory")
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    # Force a round-trip so a down server fails HERE (and is skipped), not later.
    client.admin.command("ping")
    return client[db]["claude_notes"]


def map_mongo_doc(doc: dict[str, Any], *, scope: Scope) -> Record:
    """Map a ``claude_notes`` document to a :class:`Record`."""
    note_id = str(doc.get("_id"))
    rid = f"qq-mongo:{note_id}"
    created = doc.get("last_accessed") or DATE_UNKNOWN
    recall_count = int(doc.get("access_count") or 0)
    metadata: dict[str, Any] = {"source": SRC_MONGO}
    if "importance" in doc:
        metadata["importance"] = doc["importance"]
    return Record(
        id=rid,
        text=str(doc.get("text", "")),
        type="qq_note",
        hash="",
        metadata=metadata,
        scope=scope,
        created=created if isinstance(created, str) else str(created),
        recall_count=recall_count,
    )


def read_mongo(*, scope: Scope, status: dict[str, bool] | None = None) -> Iterator[Record]:
    """Yield a :class:`Record` per QQ Mongo note; skip (warn) when unavailable.

    When *status* is given, its ``"available"`` key records whether the source
    was reachable (False => skipped). Lets the orchestrator distinguish a down
    source from a reachable-but-empty one.
    """
    if status is not None:
        status["available"] = False
    try:
        collection = _mongo_collection()
        docs = list(collection.find({}))
    except Exception as exc:  # noqa: BLE001 - any failure => skip this source
        _warn(SRC_MONGO, str(exc))
        return
    if status is not None:
        status["available"] = True
    for doc in docs:
        yield map_mongo_doc(doc, scope=scope)


# --------------------------------------------------------------------------- #
# Neo4j reader (guarded)
# --------------------------------------------------------------------------- #


def _neo4j_session() -> Any:
    """Open a Neo4j session for QQ ``knowledge_context="claude"`` entities.

    Isolated so tests can patch it to raise (down server / import error) without
    importing the neo4j driver or contacting a real database.
    """
    import neo4j  # local import: only when actually migrating

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    auth = (user, password) if password else None
    driver = neo4j.GraphDatabase.driver(uri, auth=auth)
    driver.verify_connectivity()
    return driver.session()


def map_neo4j_entity(
    entity: dict[str, Any],
    *,
    related_ids: list[str] | None,
    scope: Scope,
) -> Record:
    """Map a Neo4j entity (+ its related-entity ids) to a :class:`Record`."""
    ent_id = str(entity.get("id"))
    rid = f"qq-neo4j:{ent_id}"
    created = entity.get("last_seen") or DATE_UNKNOWN
    recall_count = int(entity.get("mention_count") or 0)
    metadata: dict[str, Any] = {"source": SRC_NEO4J}
    if "verified" in entity:
        metadata["verified"] = entity["verified"]
    if "source_history" in entity:
        metadata["source_history"] = entity["source_history"]
    links = [f"qq-neo4j:{rid_}" for rid_ in (related_ids or [])]
    return Record(
        id=rid,
        text=str(entity.get("description", "")),
        type="qq_entity",
        hash="",
        metadata=metadata,
        scope=scope,
        created=created if isinstance(created, str) else str(created),
        recall_count=recall_count,
        links=links,
    )


_NEO4J_QUERY = (
    "MATCH (e {knowledge_context: 'claude'}) "
    "OPTIONAL MATCH (e)-[]-(o {knowledge_context: 'claude'}) "
    "RETURN e AS entity, collect(DISTINCT o.id) AS related"
)


def read_neo4j(*, scope: Scope, status: dict[str, bool] | None = None) -> Iterator[Record]:
    """Yield a :class:`Record` per QQ Neo4j entity; skip (warn) when unavailable.

    When *status* is given, its ``"available"`` key records whether the source
    was reachable (False => skipped).
    """
    if status is not None:
        status["available"] = False
    try:
        session = _neo4j_session()
        try:
            rows = list(session.run(_NEO4J_QUERY))
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    except Exception as exc:  # noqa: BLE001 - any failure => skip this source
        _warn(SRC_NEO4J, str(exc))
        return
    if status is not None:
        status["available"] = True
    for row in rows:
        entity = dict(row["entity"])
        related = [r for r in (row["related"] or []) if r is not None]
        yield map_neo4j_entity(entity, related_ids=related, scope=scope)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


def migrate_all(
    *,
    backend: Backend,
    file_paths: Iterable[str] | None = None,
    scope: Scope,
    include_files: bool = True,
    include_mongo: bool = True,
    include_neo4j: bool = True,
) -> dict[str, Any]:
    """Run every available source and upsert mapped records idempotently.

    Returns a report ``{counts: {source: n}, skipped: [source, ...], total: n,
    scope: {...}}``. A source that yields nothing because it was unavailable
    appears in ``skipped`` (the reader warns to stderr on its own).
    """
    readers: list[tuple[str, Iterator[Record], dict[str, bool]]] = []
    if include_files:
        st: dict[str, bool] = {}
        readers.append((SRC_FILES, read_files(file_paths, scope=scope, status=st), st))
    if include_mongo:
        st = {}
        readers.append((SRC_MONGO, read_mongo(scope=scope, status=st), st))
    if include_neo4j:
        st = {}
        readers.append((SRC_NEO4J, read_neo4j(scope=scope, status=st), st))

    counts: dict[str, int] = {}
    skipped: list[str] = []
    total = 0
    for source, reader, st in readers:
        n = 0
        for record in reader:
            backend.upsert(record)
            n += 1
        counts[source] = n
        total += n
        # A source is "skipped" only when it was unavailable, not merely empty.
        if not st.get("available", False):
            skipped.append(source)

    return {
        "counts": counts,
        "skipped": skipped,
        "total": total,
        "scope": {"name": scope.name, "visibility": scope.visibility},
    }
