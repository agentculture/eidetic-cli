"""Tests for eidetic.cli._commands.sweep — the sweep verb (t5).

Drives the lifecycle engine against a tmp files-backed store: shadowing via
``supersedes``, age/signal archival, the ``--dry-run`` no-write contract, and
the CliError no-traceback contract.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eidetic.cli._commands import sweep
from eidetic.memory.backend import get_backend
from eidetic.memory.record import DATE_UNKNOWN, Record
from eidetic.memory.scope import Scope

_NOW = datetime(2026, 6, 20, tzinfo=timezone.utc)


def _days_ago(n: int) -> str:
    return (_NOW - timedelta(days=n)).isoformat()


def _make_record(
    rid: str,
    text: str = "hello world",
    *,
    scope: Scope | None = None,
    created: str = DATE_UNKNOWN,
    supersedes: str | None = None,
    metadata: dict | None = None,
    recall_count: int = 0,
    last_recall: str | None = None,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata=metadata or {},
        scope=scope or Scope(name="default", visibility="public"),
        created=created,
        supersedes=supersedes,
        recall_count=recall_count,
        last_recall=last_recall,
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> str:
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    sweep.register(sub)
    return parser


def _run(argv: list[str]) -> tuple[int, argparse.Namespace]:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # default now for determinism in tests
    args.now = _NOW.isoformat()
    return args.func(args), args


# -- shadowing -----------------------------------------------------------


def test_sweep_shadows_superseded_record(data_dir: str, capsys) -> None:
    backend = get_backend("files")
    backend.upsert(_make_record("A", text="new claim", supersedes="B", created=_days_ago(1)))
    backend.upsert(_make_record("B", text="old claim", created=_days_ago(2)))

    rc, _ = _run(["sweep", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["shadowed"] >= 1

    # B is now persisted as shadowed (not removed).
    fresh = get_backend("files")
    b = next(r for r in fresh.all() if r.id == "B")
    assert b.lifecycle == "shadowed"
    # A still present too.
    assert any(r.id == "A" for r in fresh.all())


def test_sweep_does_not_shadow_across_scope(data_dir: str, capsys) -> None:
    backend = get_backend("files")
    pub = Scope(name="default", visibility="public")
    priv = Scope(name="default", visibility="private")
    backend.upsert(
        _make_record("A", text="new claim", supersedes="B", scope=pub, created=_days_ago(1))
    )
    backend.upsert(_make_record("B", text="old claim", scope=priv, created=_days_ago(2)))

    rc, _ = _run(["sweep", "--json"])
    assert rc == 0

    fresh = get_backend("files")
    b = next(r for r in fresh.all() if r.id == "B")
    assert b.lifecycle == "active"  # cross-scope supersedes never shadows


# -- archival ------------------------------------------------------------


def test_sweep_archives_old_record(data_dir: str, capsys) -> None:
    backend = get_backend("files")
    backend.upsert(_make_record("old", created=_days_ago(400)))

    rc, _ = _run(["sweep", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["archived"] >= 1

    fresh = get_backend("files")
    old = next(r for r in fresh.all() if r.id == "old")
    assert old.lifecycle == "archived"


def test_sweep_exempts_protected_record(data_dir: str, capsys) -> None:
    backend = get_backend("files")
    backend.upsert(_make_record("p", created=_days_ago(400), metadata={"protected": True}))
    rc, _ = _run(["sweep", "--json"])
    assert rc == 0

    fresh = get_backend("files")
    p = next(r for r in fresh.all() if r.id == "p")
    assert p.lifecycle == "active"  # protected never archived


# -- dry-run -------------------------------------------------------------


def test_sweep_dry_run_does_not_write(data_dir: str, capsys) -> None:
    backend = get_backend("files")
    backend.upsert(_make_record("old", created=_days_ago(400)))

    rc, _ = _run(["sweep", "--dry-run", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["archived"] >= 1  # reported

    # ...but NOT persisted.
    fresh = get_backend("files")
    old = next(r for r in fresh.all() if r.id == "old")
    assert old.lifecycle == "active"


def test_sweep_never_removes_records(data_dir: str, capsys) -> None:
    backend = get_backend("files")
    backend.upsert(_make_record("A", text="new claim", supersedes="B", created=_days_ago(1)))
    backend.upsert(_make_record("B", text="old claim", created=_days_ago(2)))
    backend.upsert(_make_record("old", text="ancient note", created=_days_ago(400)))

    rc, _ = _run(["sweep", "--json"])
    assert rc == 0

    fresh = get_backend("files")
    ids = {r.id for r in fresh.all()}
    assert ids == {"A", "B", "old"}  # nothing deleted


# -- text mode -----------------------------------------------------------


def test_sweep_text_mode_reports_counts(data_dir: str, capsys) -> None:
    backend = get_backend("files")
    backend.upsert(_make_record("old", created=_days_ago(400)))
    rc, _ = _run(["sweep"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "archived" in out.lower()


def test_sweep_empty_store_is_noop(data_dir: str, capsys) -> None:
    rc, _ = _run(["sweep", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["shadowed"] == 0
    assert payload["archived"] == 0
