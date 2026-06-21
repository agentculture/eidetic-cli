"""Tests for the one-shot store migration (Record JSONL -> Envelope JSONL)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from data_refinery.store import Envelope

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.backend import record_from_envelope
from eidetic.memory.migrate_store import _ensure_within, migrate_store
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _record_line(rid: str, text: str) -> str:
    """A legacy Record-format JSONL line (top-level ``text``)."""
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

    stats = migrate_store(str(d))
    assert stats.records_converted == 2
    assert stats.files_rewritten == 1
    assert stats.already_envelope == 0

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

    stats2 = migrate_store(str(d))
    assert stats2.records_converted == 0
    assert stats2.files_rewritten == 0
    assert stats2.already_envelope == 2


def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    d = tmp_path / "memory"
    d.mkdir(parents=True)
    f = d / "notes__public.jsonl"
    original = _record_line("a", "first note") + "\n"
    f.write_text(original, encoding="utf-8")

    stats = migrate_store(str(d), dry_run=True)
    assert stats.records_converted == 1
    assert stats.files_rewritten == 1  # would rewrite...
    assert f.read_text(encoding="utf-8") == original  # ...but disk is unchanged


def test_migrate_missing_dir_is_noop(tmp_path: Path) -> None:
    stats = migrate_store(str(tmp_path / "does-not-exist"))
    assert stats.files_scanned == 0
    assert stats.records_converted == 0
    assert stats.files_rewritten == 0


def test_migrate_corrupt_record_fields_raises_cli_error(tmp_path: Path) -> None:
    """A valid JSON line missing required Record fields must raise CliError(EXIT_ENV_ERROR).

    ``Record.from_dict`` indexes ``data["text"]``, ``data["type"]``, ``data["scope"]``,
    etc. directly, so a line like ``{"id": "x"}`` will raise ``KeyError``.  The
    migration must catch that and re-raise as a structured :class:`CliError` — never
    letting the raw ``KeyError`` traceback escape to stderr.
    """
    d = tmp_path / "memory"
    d.mkdir(parents=True)
    f = d / "notes__public.jsonl"
    # Valid JSON but missing all required Record fields ("text", "type", "scope", …).
    f.write_text(json.dumps({"id": "x"}) + "\n", encoding="utf-8")

    with pytest.raises(CliError) as excinfo:
        migrate_store(str(d))

    assert excinfo.value.code == EXIT_ENV_ERROR


def test_ensure_within_accepts_path_inside_base(tmp_path: Path) -> None:
    """A temp path that lives inside the (canonical) store dir is returned as-is."""
    base = os.path.realpath(tmp_path)
    inside = tmp_path / "notes__public.jsonl.tmp"
    assert _ensure_within(base, inside) == Path(os.path.realpath(inside))


def test_ensure_within_rejects_path_outside_base(tmp_path: Path) -> None:
    """A target that canonicalises outside the store dir is refused with CliError.

    Guards the operator-supplied data-dir trust boundary: a write can never be
    redirected outside the resolved store directory.
    """
    base = os.path.realpath(tmp_path / "store")
    (tmp_path / "store").mkdir()
    escape = tmp_path / "store" / ".." / "elsewhere.jsonl.tmp"

    with pytest.raises(CliError) as excinfo:
        _ensure_within(base, escape)

    assert excinfo.value.code == EXIT_ENV_ERROR
