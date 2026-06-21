"""Tests for added_by round-trip through the eidetic files adapter (task t4).

Asserts:
1. upsert(record) -> reload returns added_by unchanged (files adapter).
2. A record persisted WITHOUT added_by loads as added_by is None (legacy compat).

The mongo round-trip is now data-refinery's concern; eidetic exercises the
files-adapter envelope mapping here.

The files backend tests always run.
"""

from __future__ import annotations

import json
import os

import pytest

from eidetic.memory.backend import Backend, get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope

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


# ===========================================================================
# Files adapter — always run
# ===========================================================================


class TestFilesBackendAddedByRoundtrip:
    """Files adapter: added_by must survive upsert → reload via envelope mapping."""

    @pytest.fixture()
    def backend(self, tmp_path: pytest.Path) -> Backend:
        os.environ["EIDETIC_DATA_DIR"] = str(tmp_path / "memory")
        return get_backend("files")

    def test_added_by_set_survives_roundtrip(self, backend: Backend) -> None:
        """upsert a record with added_by='agent-x' → reload returns 'agent-x'."""
        rec = _make_record(rid="f1", added_by="agent-x")
        backend.upsert(rec)
        reloaded = {r.id: r for r in backend.all()}
        assert "f1" in reloaded
        assert reloaded["f1"].added_by == "agent-x"

    def test_added_by_none_survives_roundtrip(self, backend: Backend) -> None:
        """upsert a record with added_by=None → reload returns None."""
        rec = _make_record(rid="f2", added_by=None)
        backend.upsert(rec)
        reloaded = {r.id: r for r in backend.all()}
        assert "f2" in reloaded
        assert reloaded["f2"].added_by is None

    def test_legacy_doc_without_added_by_loads_as_none(self, tmp_path: pytest.Path) -> None:
        """A JSONL envelope that has no 'added_by' in metadata → added_by is None.

        Writes an envelope in data_refinery format (content/scope/metadata) that
        omits 'added_by' from the metadata dict, then verifies that
        record_from_envelope returns added_by=None for backward compat.
        """
        base = tmp_path / "legacy_mem"
        base.mkdir()
        os.environ["EIDETIC_DATA_DIR"] = str(base)
        # Build an envelope dict in the data_refinery JSONL format.
        # 'added_by' is deliberately absent from metadata.
        envelope_doc = {
            "id": "legacy1",
            "hash": "h1",
            "content": "legacy text",
            "scope": {"name": "default", "visibility": "public"},
            "metadata": {
                "type": "note",
                "record_metadata": {},
                # 'added_by' deliberately absent
            },
        }
        jsonl_file = base / "default__public.jsonl"
        jsonl_file.write_text(json.dumps(envelope_doc) + "\n", encoding="utf-8")

        backend = get_backend("files")
        records = {r.id: r for r in backend.all()}
        assert "legacy1" in records
        assert records["legacy1"].added_by is None
