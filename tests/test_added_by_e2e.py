"""End-to-end success-signal tests for the ``added_by`` feature (task t7).

Exercises the full pipeline on the FILES backend:

1. ``remember`` a record WITHOUT ``added_by`` → reload → stamped nick is present
   (``"eidetic-cli"`` in this repo, where culture.yaml declares suffix: eidetic-cli).
2. ``remember`` a record WITH an explicit ``added_by`` → round-trips that exact value.
3. A legacy JSONL record persisted WITHOUT ``added_by`` loads as ``added_by is None``.
4. ``compute_stats`` (the backing function for ``overview --store``) lists the stamped
   nick in the per-scope ``contributors``.

Mongo and Neo4j variants skip cleanly when the service is unavailable.
The files-backend assertions always run.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from eidetic.cli._commands.remember import cmd_remember, register
from eidetic.memory.backend import get_backend
from eidetic.memory.backends.files import FilesBackend
from eidetic.memory.scope import Scope
from eidetic.memory.stats import compute_stats

# ---------------------------------------------------------------------------
# The stamped nick for this repo (culture.yaml suffix: eidetic-cli).
# This is what _resolve_nick() returns when culture.yaml is present.
# ---------------------------------------------------------------------------
_EXPECTED_NICK = "eidetic-cli"
_SCOPE = Scope(name="default", visibility="public")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register(sub)
    return parser


def _run_remember(record_json: str, data_dir: str, **extra_args: object) -> int:
    """Run the ``remember`` command in-process with the files backend."""
    cli_args = [
        "remember",
        record_json,
        "--backend",
        "files",
        "--scope",
        "default",
        "--visibility",
        "public",
    ]
    args = _build_parser().parse_args(cli_args)
    # Inject extra keyword args (e.g. added_by flag) onto the namespace.
    for k, v in extra_args.items():
        setattr(args, k, v)
    return args.func(args)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def data_dir(tmp_path: Path) -> str:
    """Isolated data directory; set EIDETIC_DATA_DIR so get_backend("files") uses it."""
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


# ---------------------------------------------------------------------------
# Service-availability guards for mongo / neo4j
# ---------------------------------------------------------------------------


def _mongo_is_reachable() -> bool:
    try:
        from pymongo import MongoClient

        uri = os.environ.get("EIDETIC_MONGO_URI", "mongodb://localhost:27018")
        c = MongoClient(uri, serverSelectionTimeoutMS=500)
        c.admin.command("ping")
        c.close()
        return True
    except Exception:
        return False


def _neo4j_is_reachable() -> bool:
    try:
        from neo4j import GraphDatabase

        uri = os.environ.get("EIDETIC_NEO4J_URI", "bolt://localhost:7688")
        driver = GraphDatabase.driver(uri, auth=("neo4j", "password"))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:
        return False


_skip_no_mongo = pytest.mark.skipif(
    not _mongo_is_reachable(),
    reason="live MongoDB not reachable — set EIDETIC_MONGO_URI or start docker compose",
)
_skip_no_neo4j = pytest.mark.skipif(
    not _neo4j_is_reachable(),
    reason="live Neo4j not reachable — set EIDETIC_NEO4J_URI or start docker compose",
)


# ===========================================================================
# 1. remember WITHOUT added_by → stamped nick is present
# ===========================================================================


def test_remember_stamps_nick_when_added_by_absent(data_dir: str) -> None:
    """remember a record with no added_by → reload shows added_by == 'eidetic-cli'.

    This asserts the full pipeline: _resolve_nick() reads culture.yaml (present
    in this repo with suffix 'eidetic-cli'), stamps the record, the files backend
    upserts it, and a fresh backend.all() round-trips the value.
    """
    rc = _run_remember(
        json.dumps({"id": "e2e-stamp-1", "text": "stamped nick e2e test", "type": "note"}),
        data_dir,
    )
    assert rc == 0

    backend = get_backend("files")
    records = {r.id: r for r in backend.all()}
    assert "e2e-stamp-1" in records
    rec = records["e2e-stamp-1"]
    # The stamped value must be non-None and equal to the nick from culture.yaml.
    assert rec.added_by is not None, "added_by must be stamped when absent from input"
    assert rec.added_by == _EXPECTED_NICK, f"expected nick '{_EXPECTED_NICK}', got '{rec.added_by}'"


# ===========================================================================
# 2. remember WITH explicit added_by → round-trips exact value
# ===========================================================================


def test_remember_preserves_explicit_added_by(data_dir: str) -> None:
    """A record that already carries added_by is preserved verbatim — not overwritten."""
    rc = _run_remember(
        json.dumps(
            {
                "id": "e2e-explicit-1",
                "text": "explicit added_by e2e test",
                "type": "note",
                "added_by": "external-agent",
            }
        ),
        data_dir,
    )
    assert rc == 0

    backend = get_backend("files")
    records = {r.id: r for r in backend.all()}
    assert "e2e-explicit-1" in records
    assert records["e2e-explicit-1"].added_by == "external-agent"


# ===========================================================================
# 3. Legacy JSONL line WITHOUT added_by key → loads as None
# ===========================================================================


def test_legacy_record_without_added_by_loads_as_none(tmp_path: Path) -> None:
    """A JSONL record that has no 'added_by' key at all loads with added_by=None.

    This exercises FilesBackend's reader directly, bypassing remember, to verify
    backward-compat with pre-feature records already persisted on disk.
    """
    base = tmp_path / "legacy_mem"
    base.mkdir()
    legacy_doc = {
        "id": "e2e-legacy-1",
        "text": "legacy record without added_by",
        "type": "note",
        "hash": "h-legacy",
        "metadata": {},
        "scope": {"name": "default", "visibility": "public"},
        # 'added_by' deliberately absent
    }
    jsonl_file = base / "default__public.jsonl"
    jsonl_file.write_text(json.dumps(legacy_doc) + "\n", encoding="utf-8")

    backend = FilesBackend(base_dir=str(base))
    records = {r.id: r for r in backend.all()}
    assert "e2e-legacy-1" in records
    assert records["e2e-legacy-1"].added_by is None


# ===========================================================================
# 4. compute_stats lists the stamped nick in per-scope contributors
# ===========================================================================


def test_overview_compute_stats_lists_stamped_nick_as_contributor(data_dir: str) -> None:
    """After stamping, compute_stats must include the nick in the scope's contributors.

    This is the 'overview --store' success signal: the per-scope contributors
    union includes the agent that called remember.
    """
    # Ingest a record without added_by — stamping happens inside cmd_remember.
    rc = _run_remember(
        json.dumps({"id": "e2e-stats-1", "text": "stats contributor e2e test", "type": "note"}),
        data_dir,
    )
    assert rc == 0

    backend = get_backend("files")
    all_records = list(backend.all())
    stats = compute_stats(all_records)

    assert stats["total"] >= 1
    # Find the "default/public" scope entry.
    scope_entry = next(
        (s for s in stats["scopes"] if s["name"] == "default" and s["visibility"] == "public"),
        None,
    )
    assert scope_entry is not None, "default/public scope must appear in stats"
    assert (
        _EXPECTED_NICK in scope_entry["contributors"]
    ), f"'{_EXPECTED_NICK}' must appear in contributors; got: {scope_entry['contributors']}"


# ===========================================================================
# 5. Multi-record: mix of stamped + explicit + legacy — contributors union is right
# ===========================================================================


def test_contributors_union_stamped_and_explicit(data_dir: str) -> None:
    """Stamped nick + explicit added_by together appear in the contributors union."""
    # Record stamped by the agent nick.
    _run_remember(
        json.dumps({"id": "e2e-union-1", "text": "union test auto nick", "type": "note"}),
        data_dir,
    )
    # Record with explicit added_by.
    _run_remember(
        json.dumps(
            {
                "id": "e2e-union-2",
                "text": "union test explicit author",
                "type": "note",
                "added_by": "external-contributor",
            }
        ),
        data_dir,
    )

    backend = get_backend("files")
    stats = compute_stats(list(backend.all()))
    scope_entry = next(
        (s for s in stats["scopes"] if s["name"] == "default" and s["visibility"] == "public"),
        None,
    )
    assert scope_entry is not None
    contributors = scope_entry["contributors"]
    assert (
        _EXPECTED_NICK in contributors
    ), f"stamped nick '{_EXPECTED_NICK}' must be in contributors: {contributors}"
    assert (
        "external-contributor" in contributors
    ), f"'external-contributor' must be in contributors: {contributors}"


# ===========================================================================
# 6. Mongo variant — skip when unavailable
# ===========================================================================


@_skip_no_mongo
def test_mongo_remember_stamps_nick(tmp_path: Path) -> None:
    """Mongo backend: same stamping guarantee — skip when MongoDB is down."""
    import os as _os

    from eidetic.memory.backends.mongo import MongoBackend

    uri = _os.environ.get("EIDETIC_MONGO_URI", "mongodb://localhost:27018")
    db_name = "eidetic_test_e2e_t7"
    backend = MongoBackend(uri=uri, db=db_name)
    # Clean slate.
    backend._collection.drop()  # type: ignore[attr-defined]

    try:
        # Build a namespace that mimics what cmd_remember produces.
        ns = argparse.Namespace(
            record=json.dumps({"id": "mongo-e2e-1", "text": "mongo e2e stamp", "type": "note"}),
            backend="mongo",
            scope="default",
            visibility="public",
            added_by=None,
            json=False,
        )
        # Patch get_backend to return our controlled backend.
        import eidetic.cli._commands.remember as rem_mod

        orig = rem_mod.get_backend
        rem_mod.get_backend = lambda name, **kw: backend  # type: ignore[assignment]
        try:
            rc = cmd_remember(ns)
        finally:
            rem_mod.get_backend = orig  # type: ignore[assignment]

        assert rc == 0
        records = {r.id: r for r in backend.all()}
        assert "mongo-e2e-1" in records
        assert records["mongo-e2e-1"].added_by == _EXPECTED_NICK
    finally:
        backend._collection.drop()  # type: ignore[attr-defined]
        backend.close()


# ===========================================================================
# 7. Neo4j variant — skip when unavailable
# ===========================================================================


@_skip_no_neo4j
def test_neo4j_remember_stamps_nick(tmp_path: Path) -> None:
    """Neo4j backend: same stamping guarantee — skip when Neo4j is down."""
    import os as _os

    from eidetic.memory.backends.neo4j import Neo4jBackend

    uri = _os.environ.get("EIDETIC_NEO4J_URI", "bolt://localhost:7688")
    backend = Neo4jBackend(uri=uri, auth=("neo4j", "password"))

    try:
        # Wipe any test data first.
        backend._driver.execute_query(  # type: ignore[attr-defined]
            "MATCH (n:EideticRecord) WHERE n.id STARTS WITH 'neo4j-e2e-' DETACH DELETE n"
        )

        ns = argparse.Namespace(
            record=json.dumps({"id": "neo4j-e2e-1", "text": "neo4j e2e stamp", "type": "note"}),
            backend="neo4j",
            scope="default",
            visibility="public",
            added_by=None,
            json=False,
        )

        import eidetic.cli._commands.remember as rem_mod

        orig = rem_mod.get_backend
        rem_mod.get_backend = lambda name, **kw: backend  # type: ignore[assignment]
        try:
            rc = cmd_remember(ns)
        finally:
            rem_mod.get_backend = orig  # type: ignore[assignment]

        assert rc == 0
        records = {r.id: r for r in backend.all()}
        assert "neo4j-e2e-1" in records
        assert records["neo4j-e2e-1"].added_by == _EXPECTED_NICK
    finally:
        backend._driver.execute_query(  # type: ignore[attr-defined]
            "MATCH (n:EideticRecord) WHERE n.id STARTS WITH 'neo4j-e2e-' DETACH DELETE n"
        )
        backend.close()
