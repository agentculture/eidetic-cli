"""Tests for added_by round-trip on files and mongo backends (task t4).

Asserts:
1. upsert(record) → reload returns added_by unchanged (both backends).
2. A record/doc persisted WITHOUT added_by loads as added_by is None (legacy compat).

The files backend tests always run.
The live-mongo tests (marked 'live_mongo') skip when MongoDB is unreachable.
The fake-client mongo tests always run (no external service).
"""

from __future__ import annotations

import json

import pytest

from eidetic.memory.backends.files import FilesBackend
from eidetic.memory.backends.mongo import MongoBackend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope
from tests.test_mongo_backend import _FakeClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCOPE = Scope(name="default", visibility="public")


def _make_record(rid: str = "r1", added_by: str | None = None) -> Record:
    return Record(
        id=rid,
        text=f"record text {rid}",
        type="note",
        hash="",
        metadata={},
        scope=_SCOPE,
        added_by=added_by,
    )


# ---------------------------------------------------------------------------
# Live-mongo availability check
# ---------------------------------------------------------------------------


def _mongo_is_reachable() -> bool:
    """Return True only when a live MongoDB answers within 500 ms."""
    try:
        from pymongo import MongoClient

        uri = __import__("os").environ.get("EIDETIC_MONGO_URI", "mongodb://localhost:27018")
        c = MongoClient(uri, serverSelectionTimeoutMS=500)
        c.admin.command("ping")
        c.close()
        return True
    except Exception:
        return False


_MONGO_AVAILABLE = _mongo_is_reachable()
skip_no_mongo = pytest.mark.skipif(
    not _MONGO_AVAILABLE,
    reason="live MongoDB not reachable — set EIDETIC_MONGO_URI or start docker compose",
)


# ===========================================================================
# Files backend — always run
# ===========================================================================


class TestFilesBackendAddedByRoundtrip:
    """FilesBackend: added_by must survive upsert → reload."""

    @pytest.fixture()
    def backend(self, tmp_path: pytest.Path) -> FilesBackend:
        return FilesBackend(base_dir=str(tmp_path / "memory"))

    def test_added_by_set_survives_roundtrip(self, backend: FilesBackend) -> None:
        """upsert a record with added_by='agent-x' → reload returns 'agent-x'."""
        rec = _make_record(rid="f1", added_by="agent-x")
        backend.upsert(rec)
        reloaded = {r.id: r for r in backend.all()}
        assert "f1" in reloaded
        assert reloaded["f1"].added_by == "agent-x"

    def test_added_by_none_survives_roundtrip(self, backend: FilesBackend) -> None:
        """upsert a record with added_by=None → reload returns None."""
        rec = _make_record(rid="f2", added_by=None)
        backend.upsert(rec)
        reloaded = {r.id: r for r in backend.all()}
        assert "f2" in reloaded
        assert reloaded["f2"].added_by is None

    def test_legacy_doc_without_added_by_loads_as_none(self, tmp_path: pytest.Path) -> None:
        """A JSONL line that has no 'added_by' key at all → added_by is None."""
        base = tmp_path / "legacy_mem"
        base.mkdir()
        # Write a minimal record dict that deliberately omits 'added_by'
        legacy_doc = {
            "id": "legacy1",
            "text": "legacy text",
            "type": "note",
            "hash": "h1",
            "metadata": {},
            "scope": {"name": "default", "visibility": "public"},
        }
        jsonl_file = base / "default__public.jsonl"
        jsonl_file.write_text(json.dumps(legacy_doc) + "\n", encoding="utf-8")

        backend = FilesBackend(base_dir=str(base))
        records = {r.id: r for r in backend.all()}
        assert "legacy1" in records
        assert records["legacy1"].added_by is None


# ===========================================================================
# Mongo backend (fake client) — always run
# ===========================================================================


class TestMongoBackendAddedByRoundtripFake:
    """MongoBackend with a fake in-memory client: added_by must survive upsert → reload."""

    @pytest.fixture()
    def backend(self) -> MongoBackend:
        return MongoBackend(client=_FakeClient())

    def test_added_by_set_survives_roundtrip(self, backend: MongoBackend) -> None:
        """upsert a record with added_by='agent-y' → reload returns 'agent-y'."""
        rec = _make_record(rid="m1", added_by="agent-y")
        backend.upsert(rec)
        reloaded = {r.id: r for r in backend.all()}
        assert "m1" in reloaded
        assert reloaded["m1"].added_by == "agent-y"

    def test_added_by_none_survives_roundtrip(self, backend: MongoBackend) -> None:
        """upsert a record with added_by=None → reload returns None."""
        rec = _make_record(rid="m2", added_by=None)
        backend.upsert(rec)
        reloaded = {r.id: r for r in backend.all()}
        assert "m2" in reloaded
        assert reloaded["m2"].added_by is None

    def test_legacy_doc_without_added_by_loads_as_none(self) -> None:
        """A MongoDB doc that has no 'added_by' key at all → added_by is None."""
        fake_client = _FakeClient()
        # Inject a legacy doc directly, bypassing the backend upsert so no
        # 'added_by' key is written to the store.
        fake_client["eidetic_memory"]["records"].replace_one(
            {"_id": "legacy2"},
            {
                "_id": "legacy2",
                "id": "legacy2",
                "text": "legacy mongo text",
                "type": "note",
                "hash": "h2",
                "metadata": {},
                "scope": {"name": "default", "visibility": "public"},
                # 'added_by' deliberately absent
            },
            upsert=True,
        )
        backend = MongoBackend(client=fake_client)
        records = {r.id: r for r in backend.all()}
        assert "legacy2" in records
        assert records["legacy2"].added_by is None


# ===========================================================================
# Mongo backend (live) — skip when unavailable
# ===========================================================================


@skip_no_mongo
class TestMongoBackendAddedByRoundtripLive:
    """MongoBackend against a real MongoDB: added_by round-trip + legacy compat."""

    @pytest.fixture()
    def backend(self) -> MongoBackend:
        import os

        uri = os.environ.get("EIDETIC_MONGO_URI", "mongodb://localhost:27018")
        db_name = "eidetic_test_t4"
        b = MongoBackend(uri=uri, db=db_name)
        # Clean up any leftover data from a previous run
        b._collection.drop()  # type: ignore[attr-defined]
        yield b
        b._collection.drop()  # type: ignore[attr-defined]
        b.close()

    def test_added_by_set_survives_roundtrip_live(self, backend: MongoBackend) -> None:
        rec = _make_record(rid="live1", added_by="live-agent")
        backend.upsert(rec)
        reloaded = {r.id: r for r in backend.all()}
        assert "live1" in reloaded
        assert reloaded["live1"].added_by == "live-agent"

    def test_added_by_none_survives_roundtrip_live(self, backend: MongoBackend) -> None:
        rec = _make_record(rid="live2", added_by=None)
        backend.upsert(rec)
        reloaded = {r.id: r for r in backend.all()}
        assert "live2" in reloaded
        assert reloaded["live2"].added_by is None

    def test_legacy_doc_without_added_by_loads_as_none_live(self, backend: MongoBackend) -> None:
        backend._collection.replace_one(  # type: ignore[attr-defined]
            {"_id": "live-legacy"},
            {
                "_id": "live-legacy",
                "id": "live-legacy",
                "text": "live legacy text",
                "type": "note",
                "hash": "h3",
                "metadata": {},
                "scope": {"name": "default", "visibility": "public"},
            },
            upsert=True,
        )
        records = {r.id: r for r in backend.all()}
        assert "live-legacy" in records
        assert records["live-legacy"].added_by is None
