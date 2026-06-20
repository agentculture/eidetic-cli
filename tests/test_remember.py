"""Tests for eidetic.cli._commands.remember — the remember verb."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

import pytest

from eidetic.cli._commands.remember import register
from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.memory.backend import get_backend
from eidetic.memory.scope import Scope


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> str:
    """Return a temp directory and set EIDETIC_DATA_DIR for the files backend."""
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register(sub)
    return parser


def test_single_record_upsert(tmp_data_dir: str) -> None:
    """A single JSON-object arg upserts one record, retrievable via search."""
    record_json = json.dumps({"id": "r1", "text": "hello world", "type": "note"})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    rc = args.func(args)
    assert rc == 0

    backend = get_backend("files")
    results = backend.search(
        "hello", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    assert any(r.id == "r1" for r in results)


def test_ndjson_stdin_upserts_multiple(tmp_data_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """NDJSON on stdin upserts multiple records."""
    lines = [
        json.dumps({"id": "a1", "text": "first record", "type": "note"}),
        json.dumps({"id": "a2", "text": "second record", "type": "note"}),
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines)))
    args = _build_parser().parse_args(
        [
            "remember",
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    rc = args.func(args)
    assert rc == 0

    backend = get_backend("files")
    results = backend.search(
        "record", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    ids = {r.id for r in results}
    assert "a1" in ids
    assert "a2" in ids


def test_idempotent_upsert(tmp_data_dir: str) -> None:
    """Re-running remember with the same id leaves exactly one record."""
    record_json = json.dumps({"id": "dup1", "text": "original", "type": "note"})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    args.func(args)

    # Upsert again with same id, different text
    record_json2 = json.dumps({"id": "dup1", "text": "updated", "type": "note"})
    args2 = _build_parser().parse_args(
        [
            "remember",
            record_json2,
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    args2.func(args2)

    backend = get_backend("files")
    results = backend.search(
        "updated", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    ids = [r.id for r in results if r.id == "dup1"]
    assert ids.count("dup1") == 1


def test_bad_json_raises_cli_error(tmp_data_dir: str) -> None:
    """A bad-JSON input raises CliError."""
    args = _build_parser().parse_args(
        [
            "remember",
            "not valid json at all",
            "--backend",
            "files",
        ]
    )
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR


def test_json_output(tmp_data_dir: str, capsys: pytest.CaptureFixture[str]) -> None:
    """--json emits structured JSON output."""
    record_json = json.dumps({"id": "j1", "text": "json test", "type": "note"})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
            "--json",
        ]
    )
    rc = args.func(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["upserted"] == 1
    assert payload["ids"] == ["j1"]


def test_missing_id_raises_cli_error(tmp_data_dir: str) -> None:
    """A record missing 'id' raises CliError."""
    record_json = json.dumps({"text": "no id here", "type": "note"})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
        ]
    )
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR


def test_missing_text_raises_cli_error(tmp_data_dir: str) -> None:
    """A record missing 'text' raises CliError."""
    record_json = json.dumps({"id": "x1", "type": "note"})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
        ]
    )
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR


def test_missing_type_raises_cli_error(tmp_data_dir: str) -> None:
    """A record missing 'type' raises CliError (type is required on ingest)."""
    record_json = json.dumps({"id": "x1", "text": "no type here"})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
        ]
    )
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR
    assert "type" in exc_info.value.message


def test_malformed_scope_raises_cli_error(tmp_data_dir: str) -> None:
    """A record with a string 'scope' (not a dict) raises CliError."""
    record_json = json.dumps({"id": "x", "text": "t", "type": "note", "scope": "default"})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
        ]
    )
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR


def test_inline_scope_without_hash_or_metadata_upserts(tmp_data_dir: str) -> None:
    """A record with an inline 'scope' but no 'hash'/'metadata' upserts cleanly.

    Regression: the inline-scope branch routed straight to Record.from_dict, which
    reads 'hash'/'metadata' as required keys, so such a record raised KeyError —
    contradicting the optional-hash/metadata contract and breaking the #3 NDJSON
    ingest path. hash must be derived from text, metadata defaults to {}.
    """
    record_json = json.dumps(
        {
            "id": "is1",
            "text": "inline scope no hash",
            "type": "note",
            "scope": {"name": "qq", "visibility": "private"},
        }
    )
    args = _build_parser().parse_args(["remember", record_json, "--backend", "files"])
    assert args.func(args) == 0

    backend = get_backend("files")
    results = backend.search(
        "inline", top_k=10, scope=Scope(name="qq", visibility="private"), filters=None
    )
    hit = [r for r in results if r.id == "is1"]
    assert len(hit) == 1
    assert hit[0].hash  # derived from text, not blank
    assert hit[0].metadata == {}
    assert hit[0].scope == Scope(name="qq", visibility="private")


def test_ingest_drops_caller_score(tmp_data_dir: str) -> None:
    """A record JSON that includes 'score' results in a stored record with score=None."""
    record_json = json.dumps({"id": "sc1", "text": "scored", "type": "note", "score": 0.9})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    args.func(args)

    backend = get_backend("files")
    results = backend.search(
        "scored", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    hit = [r for r in results if r.id == "sc1"]
    assert len(hit) == 1
    # The stored record's score should be None (not 0.9)
    # Note: search will set score during retrieval, so we check the raw file
    import json as _json
    from pathlib import Path

    data_dir = Path(tmp_data_dir)
    jsonl_files = list(data_dir.glob("*.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
    stored = _json.loads(lines[0])
    assert stored["score"] is None


# --- t4 tests: created-date stamping + supersedes/links carrying ---


def test_ingest_stamps_created_when_not_provided(tmp_data_dir: str) -> None:
    """A record without 'created' gets stamped with the current ISO-8601 date."""
    from datetime import datetime, timezone

    record_json = json.dumps({"id": "cr1", "text": "no created date", "type": "note"})
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    before = datetime.now(timezone.utc).isoformat()
    args.func(args)
    after = datetime.now(timezone.utc).isoformat()

    backend = get_backend("files")
    results = backend.search(
        "created", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    hit = [r for r in results if r.id == "cr1"]
    assert len(hit) == 1
    record = hit[0]
    # created should be a non-sentinel ISO string between before and after
    assert record.created != "date-unknown"
    assert before <= record.created <= after


def test_ingest_preserves_provided_created(tmp_data_dir: str) -> None:
    """A record with 'created' preserves the provided value verbatim."""
    provided_date = "2025-01-15T10:30:00+00:00"
    record_json = json.dumps(
        {
            "id": "cr2",
            "text": "with created date",
            "type": "note",
            "created": provided_date,
        }
    )
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    args.func(args)

    backend = get_backend("files")
    results = backend.search(
        "created", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    hit = [r for r in results if r.id == "cr2"]
    assert len(hit) == 1
    record = hit[0]
    assert record.created == provided_date


def test_ingest_carries_supersedes(tmp_data_dir: str) -> None:
    """A record with 'supersedes' carries it through to storage."""
    record_json = json.dumps(
        {
            "id": "sup1",
            "text": "newer version",
            "type": "note",
            "supersedes": "old_id",
        }
    )
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    args.func(args)

    backend = get_backend("files")
    results = backend.search(
        "newer", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    hit = [r for r in results if r.id == "sup1"]
    assert len(hit) == 1
    record = hit[0]
    assert record.supersedes == "old_id"


def test_ingest_carries_links(tmp_data_dir: str) -> None:
    """A record with 'links' carries them through to storage."""
    links = ["ref1", "ref2", "ref3"]
    record_json = json.dumps(
        {
            "id": "lnk1",
            "text": "linked record",
            "type": "note",
            "links": links,
        }
    )
    args = _build_parser().parse_args(
        [
            "remember",
            record_json,
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    args.func(args)

    backend = get_backend("files")
    results = backend.search(
        "linked", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    hit = [r for r in results if r.id == "lnk1"]
    assert len(hit) == 1
    record = hit[0]
    assert record.links == links


def test_ingest_idempotent_with_created_and_supersedes_links(tmp_data_dir: str) -> None:
    """Re-ingesting the same id stays idempotent even with created/supersedes/links."""
    first_record = {
        "id": "idp1",
        "text": "original",
        "type": "note",
        "created": "2025-01-10T00:00:00+00:00",
        "supersedes": "old1",
        "links": ["ref1"],
    }
    args1 = _build_parser().parse_args(
        [
            "remember",
            json.dumps(first_record),
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    args1.func(args1)

    # Ingest again with same id but different content
    second_record = {
        "id": "idp1",
        "text": "updated",
        "type": "note",
        "created": "2025-01-20T00:00:00+00:00",
        "supersedes": "old2",
        "links": ["ref2", "ref3"],
    }
    args2 = _build_parser().parse_args(
        [
            "remember",
            json.dumps(second_record),
            "--backend",
            "files",
            "--scope",
            "default",
            "--visibility",
            "public",
        ]
    )
    args2.func(args2)

    backend = get_backend("files")
    results = backend.search(
        "updated", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    ids = [r.id for r in results if r.id == "idp1"]
    # Should have exactly one record with id='idp1', not a duplicate
    assert ids.count("idp1") == 1
    hit = [r for r in results if r.id == "idp1"][0]
    # Should have the second record's values
    assert hit.text == "updated"
    assert hit.created == "2025-01-20T00:00:00+00:00"
    assert hit.supersedes == "old2"
    assert hit.links == ["ref2", "ref3"]
