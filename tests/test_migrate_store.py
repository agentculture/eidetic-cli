"""Tests for ``eidetic migrate store`` — delegated store migration (Record -> Envelope).

The migration now delegates to data-refinery's store-migration endpoint
(``data_refinery.store.migrate``): eidetic supplies only a record->Envelope
transform and the store root, never a filesystem write path. These tests run
against the real data-refinery files backend on a tmp dir, so they exercise the
full delegation — transform, idempotency, atomic rewrite, and the error contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from data_refinery.store import Envelope

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.backend import (
    _legacy_line_to_envelope,
    migrate_store,
    record_from_envelope,
)
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _record_line(rid: str, text: str) -> str:
    """A legacy Record-format JSONL line (top-level ``text``, no ``content``)."""
    rec = Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata={"author": "ori"},
        scope=Scope("notes", "public"),
        created="2026-06-20",
        added_by="eidetic-cli",
    )
    return json.dumps(rec.to_dict())


def _seed(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    f = d / "notes__public.jsonl"
    f.write_text(
        _record_line("a", "first note") + "\n" + _record_line("b", "second note") + "\n",
        encoding="utf-8",
    )
    return f


def test_migrate_converts_records_to_envelopes(tmp_path: Path) -> None:
    d = tmp_path / "memory"
    f = _seed(d)

    report = migrate_store(str(d))
    assert report["files"] == 1
    assert report["migrated"] == 1
    assert report["skipped"] == 0
    assert report["migrated_files"] == ["notes__public.jsonl"]
    assert report["dry_run"] is False

    lines = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    for obj in lines:
        assert "content" in obj and "text" not in obj  # now Envelope-shaped
        rec = record_from_envelope(Envelope.from_dict(obj))
        assert rec.type == "note"
        assert rec.metadata == {"author": "ori"}
        assert rec.scope.name == "notes" and rec.scope.visibility == "public"
        assert rec.created == "2026-06-20"
        assert rec.added_by == "eidetic-cli"

    texts = {record_from_envelope(Envelope.from_dict(o)).text for o in lines}
    assert texts == {"first note", "second note"}


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    d = tmp_path / "memory"
    _seed(d)
    migrate_store(str(d))

    report2 = migrate_store(str(d))
    assert report2["migrated"] == 0
    assert report2["skipped"] == 1
    assert report2["migrated_files"] == []


def test_migrate_keeps_already_envelope_lines_verbatim(tmp_path: Path) -> None:
    """A store already in Envelope format is a no-op.

    data-refinery detects an already-canonical Envelope line and keeps it
    verbatim — it never re-feeds it through eidetic's transform — so a second
    migration is a byte-for-byte no-op.
    """
    d = tmp_path / "memory"
    d.mkdir(parents=True)
    f = d / "notes__public.jsonl"
    env = _legacy_line_to_envelope(json.loads(_record_line("a", "already here")))
    f.write_text(json.dumps(env.to_dict()) + "\n", encoding="utf-8")
    before = f.read_text(encoding="utf-8")

    report = migrate_store(str(d))
    assert report["migrated"] == 0
    assert report["skipped"] == 1
    assert f.read_text(encoding="utf-8") == before  # byte-identical


def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    d = tmp_path / "memory"
    d.mkdir(parents=True)
    f = d / "notes__public.jsonl"
    original = _record_line("a", "first note") + "\n"
    f.write_text(original, encoding="utf-8")

    report = migrate_store(str(d), dry_run=True)
    assert report["migrated"] == 1  # would rewrite...
    assert report["dry_run"] is True
    assert f.read_text(encoding="utf-8") == original  # ...but disk is unchanged


def test_migrate_missing_dir_is_noop(tmp_path: Path) -> None:
    """A non-existent store dir migrates nothing (no scope files to rewrite)."""
    report = migrate_store(str(tmp_path / "does-not-exist"))
    assert report["files"] == 0
    assert report["migrated"] == 0


def test_migrate_corrupt_record_fields_raises_cli_error(tmp_path: Path) -> None:
    """A valid JSON line missing required Record fields raises CliError(EXIT_ENV_ERROR).

    eidetic's transform (``Record.from_dict``) raises ``KeyError`` on a line
    lacking ``scope``/``text``/etc.; data-refinery maps that to a structured
    "corrupt line" CliError (code 2), and eidetic re-raises it as its own
    CliError — never letting a raw traceback escape to stderr.
    """
    d = tmp_path / "memory"
    d.mkdir(parents=True)
    f = d / "notes__public.jsonl"
    f.write_text(json.dumps({"id": "x"}) + "\n", encoding="utf-8")

    with pytest.raises(CliError) as excinfo:
        migrate_store(str(d))

    assert excinfo.value.code == EXIT_ENV_ERROR


def test_transform_maps_legacy_record_to_envelope() -> None:
    """The consumer transform round-trips a legacy Record line into an Envelope."""
    env = _legacy_line_to_envelope(json.loads(_record_line("a", "hello")))
    assert isinstance(env, Envelope)
    assert env.id == "a"
    assert env.content == "hello"
    rec = record_from_envelope(env)
    assert rec.text == "hello" and rec.type == "note"
