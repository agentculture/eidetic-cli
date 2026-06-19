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
    record_json = json.dumps({"id": "r1", "text": "hello world"})
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
        json.dumps({"id": "a1", "text": "first record"}),
        json.dumps({"id": "a2", "text": "second record"}),
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
    record_json = json.dumps({"id": "dup1", "text": "original"})
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
    record_json2 = json.dumps({"id": "dup1", "text": "updated"})
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
    record_json = json.dumps({"id": "j1", "text": "json test"})
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
    record_json = json.dumps({"text": "no id here"})
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
    record_json = json.dumps({"id": "x1"})
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


def test_malformed_scope_raises_cli_error(tmp_data_dir: str) -> None:
    """A record with a string 'scope' (not a dict) raises CliError."""
    record_json = json.dumps({"id": "x", "text": "t", "scope": "default"})
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


def test_ingest_drops_caller_score(tmp_data_dir: str) -> None:
    """A record JSON that includes 'score' results in a stored record with score=None."""
    record_json = json.dumps({"id": "sc1", "text": "scored", "score": 0.9})
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
