"""Tests for the one-shot QQ memory migration (t6).

Hermetic: NO live Mongo/Neo4j. The Mongo/Neo4j readers are exercised only on
their *skip-on-unavailable* path by patching the import/connect to raise. The
files reader runs against small fixture markdown files written to a tmp dir —
never the real personal ~/.claude/skills/memory files.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from eidetic.cli._commands import migrate
from eidetic.memory import migrate_qq
from eidetic.memory.backend import get_backend
from eidetic.memory.record import DATE_UNKNOWN
from eidetic.memory.scope import Scope

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

_CORE_MD = """\
# Core Memory

## Identity
- Preferred name: Ori
- Location: Israel

## System
- Hardware: NVIDIA DGX Spark
- Memory: 128GB
"""

_NOTES_MD = """\
# QQ Memory Notes

## Ongoing Threads
- A thread about devex
- A thread about ec2-cli
"""


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> str:
    """Point the files backend at a temp dir via EIDETIC_DATA_DIR."""
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


@pytest.fixture
def qq_files(tmp_path: Path) -> list[str]:
    """Write small fixture core.md / notes.md and return their paths."""
    core = tmp_path / "core.md"
    notes = tmp_path / "notes.md"
    core.write_text(_CORE_MD, encoding="utf-8")
    notes.write_text(_NOTES_MD, encoding="utf-8")
    return [str(core), str(notes)]


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    migrate.register(sub)
    return parser.parse_args(argv)


# --------------------------------------------------------------------------- #
# Files reader / mapper
# --------------------------------------------------------------------------- #


def test_read_files_yields_section_records(qq_files: list[str]) -> None:
    """Each markdown ## section becomes one Record with provenance + a stable id."""
    records = list(migrate_qq.read_files(qq_files, scope=Scope("qq", "private")))
    # core.md has Identity + System; notes.md has Ongoing Threads => 3 sections.
    assert len(records) == 3

    by_section = {r.metadata.get("section"): r for r in records}
    assert "Identity" in by_section
    assert "System" in by_section
    assert "Ongoing Threads" in by_section

    ident = by_section["Identity"]
    assert ident.metadata["source"] == "qq-files"
    assert "Preferred name: Ori" in ident.text
    assert ident.id.startswith("qq-file:")
    # files reader gets recall_count 0 and a file-mtime date signature (ISO).
    assert ident.recall_count == 0
    assert ident.created != DATE_UNKNOWN
    assert ident.scope.visibility == "private"


_NOTES_DUP_MD = """\
# QQ Memory Notes

## Ongoing Threads
- The big first thread, the bulk of the file.

## Other Section
- Something in between.

## Ongoing Threads
- A second, smaller thread far below the first.
"""


def test_read_files_disambiguates_duplicate_headings(tmp_path: Path) -> None:
    """Two ## sections that slug identically must NOT collide on one id.

    Regression: duplicate "## Ongoing Threads" headings produced the same
    qq-file id, so the idempotent upsert dropped all but one section's body.
    """
    notes = tmp_path / "notes.md"
    notes.write_text(_NOTES_DUP_MD, encoding="utf-8")

    records = list(migrate_qq.read_files([str(notes)], scope=Scope("qq", "private")))

    # All three sections survive as distinct records...
    assert len(records) == 3
    ids = [r.id for r in records]
    assert len(set(ids)) == 3, "duplicate headings collapsed to one id"

    # ...the first occurrence keeps the bare slug (stable across re-runs),
    # the second gets a deterministic -2 suffix.
    assert ids[0].endswith("#ongoing-threads")
    assert ids[2].endswith("#ongoing-threads-2")

    # Both bodies are preserved — neither overwrote the other.
    bodies = "\n".join(r.text for r in records)
    assert "bulk of the file" in bodies
    assert "smaller thread far below" in bodies


@pytest.fixture
def no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force Mongo + Neo4j unavailable so file-only tests stay hermetic."""

    def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(migrate_qq, "_mongo_collection", _boom)
    monkeypatch.setattr(migrate_qq, "_neo4j_session", _boom)


def test_files_records_land_in_private_scope_by_default(
    tmp_data_dir: str, qq_files: list[str], no_db: None
) -> None:
    """Default migration writes into a private (non-public) scope — no leak."""
    args = _parse(["migrate", "qq", "--files"] + [a for f in qq_files for a in ("--file", f)])
    rc = args.func(args)
    assert rc == 0

    backend = get_backend("files")
    # A public-scope query must NOT see the migrated personal records.
    public_hits = backend.search(
        "Ori", top_k=50, scope=Scope("default", "public"), filters=None, mode="exact"
    )
    assert public_hits == []

    # The same query inside the private qq scope DOES see them.
    private_hits = backend.search(
        "Ori", top_k=50, scope=Scope("qq", "private"), filters=None, mode="exact"
    )
    assert any("Ori" in h.text for h in private_hits)


def test_migration_is_idempotent(tmp_data_dir: str, qq_files: list[str], no_db: None) -> None:
    """Re-running the migration updates in place and never duplicates by id."""
    file_args = [a for f in qq_files for a in ("--file", f)]
    args1 = _parse(["migrate", "qq", "--files"] + file_args)
    args1.func(args1)
    # Count records on disk directly (ranking drops zero-score hits, so a
    # search-based count would be unreliable for an exhaustive total).
    count1 = _count_private_qq_records(tmp_data_dir)
    ids1 = _private_qq_ids(tmp_data_dir)

    args2 = _parse(["migrate", "qq", "--files"] + file_args)
    args2.func(args2)
    count2 = _count_private_qq_records(tmp_data_dir)
    ids2 = _private_qq_ids(tmp_data_dir)

    assert count1 == count2 == 3
    # Re-run updates in place: identical id set, no duplicates.
    assert ids1 == ids2
    assert len(ids2) == count2


def _count_private_qq_records(data_dir: str) -> int:
    path = Path(data_dir) / "qq__private.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _private_qq_ids(data_dir: str) -> set[str]:
    path = Path(data_dir) / "qq__private.jsonl"
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            ids.add(json.loads(line)["id"])
    return ids


# --------------------------------------------------------------------------- #
# Mongo / Neo4j skip-on-unavailable
# --------------------------------------------------------------------------- #


def test_read_mongo_skips_when_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """When the Mongo connector raises, the reader warns and yields nothing."""

    def _boom(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(migrate_qq, "_mongo_collection", _boom)
    records = list(migrate_qq.read_mongo(scope=Scope("qq", "private")))
    assert records == []
    err = capsys.readouterr().err
    assert "warning" in err.lower()
    assert "qq-mongo" in err


def test_read_neo4j_skips_when_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """When the Neo4j connector raises, the reader warns and yields nothing."""

    def _boom(*_a, **_k):
        raise RuntimeError("driver import failed")

    monkeypatch.setattr(migrate_qq, "_neo4j_session", _boom)
    records = list(migrate_qq.read_neo4j(scope=Scope("qq", "private")))
    assert records == []
    err = capsys.readouterr().err
    assert "warning" in err.lower()
    assert "qq-neo4j" in err


def test_mappers_carry_provenance_and_date_signature() -> None:
    """A mongo/neo4j doc maps to a Record with provenance + a date signature."""
    mongo_doc = {
        "_id": "note42",
        "text": "a remembered note",
        "importance": 0.9,
        "access_count": 7,
        "last_accessed": "2026-01-02T03:04:05",
    }
    rec = migrate_qq.map_mongo_doc(mongo_doc, scope=Scope("qq", "private"))
    assert rec.id == "qq-mongo:note42"
    assert rec.metadata["source"] == "qq-mongo"
    assert rec.metadata["importance"] == 0.9
    assert rec.recall_count == 7
    assert rec.created == "2026-01-02T03:04:05"
    assert rec.scope.visibility == "private"

    neo_entity = {
        "id": "ent7",
        "description": "an entity",
        "mention_count": 4,
        "last_seen": "2026-02-03T00:00:00",
        "verified": True,
        "source_history": ["a", "b"],
    }
    rel = migrate_qq.map_neo4j_entity(
        neo_entity, related_ids=["ent8", "ent9"], scope=Scope("qq", "private")
    )
    assert rel.id == "qq-neo4j:ent7"
    assert rel.metadata["source"] == "qq-neo4j"
    assert rel.metadata["verified"] is True
    assert rel.metadata["source_history"] == ["a", "b"]
    assert rel.recall_count == 4
    assert rel.created == "2026-02-03T00:00:00"
    assert rel.links == ["qq-neo4j:ent8", "qq-neo4j:ent9"]


def test_map_mongo_doc_falls_back_to_date_unknown() -> None:
    """A doc with no date signature is decay-neutral (DATE_UNKNOWN)."""
    rec = migrate_qq.map_mongo_doc({"_id": "n", "text": "x"}, scope=Scope("qq", "private"))
    assert rec.created == DATE_UNKNOWN
    assert rec.recall_count == 0


# --------------------------------------------------------------------------- #
# Orchestrator / command
# --------------------------------------------------------------------------- #


def test_migrate_all_completes_with_mongo_neo4j_down(
    tmp_data_dir: str,
    qq_files: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run COMPLETES (files migrate) even when Mongo + Neo4j are down."""

    def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(migrate_qq, "_mongo_collection", _boom)
    monkeypatch.setattr(migrate_qq, "_neo4j_session", _boom)

    report = migrate_qq.migrate_all(
        backend=get_backend("files"),
        file_paths=qq_files,
        scope=Scope("qq", "private"),
    )
    assert report["counts"]["qq-files"] == 3
    assert "qq-mongo" in report["skipped"]
    assert "qq-neo4j" in report["skipped"]
    assert report["total"] == 3


def test_command_json_reports_counts_and_skips(
    tmp_data_dir: str,
    qq_files: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """`migrate qq --json` emits per-source counts and skipped sources."""

    def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(migrate_qq, "_mongo_collection", _boom)
    monkeypatch.setattr(migrate_qq, "_neo4j_session", _boom)

    file_args = [a for f in qq_files for a in ("--file", f)]
    args = _parse(["migrate", "qq", "--json"] + file_args)
    rc = args.func(args)
    assert rc == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["total"] == 3
    assert payload["counts"]["qq-files"] == 3
    assert "qq-mongo" in payload["skipped"]
    assert "qq-neo4j" in payload["skipped"]
    assert payload["scope"] == {"name": "qq", "visibility": "private"}


def test_command_default_scope_is_private(
    tmp_data_dir: str,
    qq_files: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No --scope/--visibility flags => private qq scope (no-leak default)."""

    def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(migrate_qq, "_mongo_collection", _boom)
    monkeypatch.setattr(migrate_qq, "_neo4j_session", _boom)

    file_args = [a for f in qq_files for a in ("--file", f)]
    args = _parse(["migrate", "qq"] + file_args)
    assert args.visibility == "private"
    assert args.scope == "qq"
    rc = args.func(args)
    assert rc == 0

    backend = get_backend("files")
    public_hits = backend.search(
        "Ori", top_k=50, scope=Scope("default", "public"), filters=None, mode="exact"
    )
    assert public_hits == []


def test_command_text_mode_reports_per_source(
    tmp_data_dir: str,
    qq_files: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Text mode prints a per-source summary including skipped sources."""

    def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(migrate_qq, "_mongo_collection", _boom)
    monkeypatch.setattr(migrate_qq, "_neo4j_session", _boom)

    file_args = [a for f in qq_files for a in ("--file", f)]
    args = _parse(["migrate", "qq"] + file_args)
    args.func(args)
    out = capsys.readouterr().out
    assert "qq-files" in out
    assert "3" in out
