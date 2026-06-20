"""Tests for eidetic.cli._commands.recall — the recall verb."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from eidetic.cli._commands import recall
from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.memory.backend import get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _make_record(
    rid: str = "r1",
    text: str = "hello world",
    scope: Scope | None = None,
    metadata: dict | None = None,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata=metadata or {},
        scope=scope or Scope(name="default", visibility="public"),
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> str:
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


@pytest.fixture
def seeded(data_dir: str) -> None:
    """Store several records across scopes for recall tests."""
    backend = get_backend("files")
    backend.upsert(_make_record("a1", "alpha record", metadata={"tag": "alpha"}))
    backend.upsert(_make_record("b1", "beta record", metadata={"tag": "beta"}))
    backend.upsert(_make_record("c1", "gamma record", metadata={"tag": "gamma"}))
    backend.upsert(_make_record("d1", "delta record", metadata={"tag": "alpha"}))
    # Private record in a secret scope
    backend.upsert(
        _make_record(
            "secret1",
            "secret record",
            scope=Scope(name="secret", visibility="private"),
            metadata={"tag": "secret"},
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    recall.register(sub)
    return parser


def test_recall_json_returns_hits_with_provenance(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--json"])
    rc = args.func(args)
    assert rc == 0
    out = capsys.readouterr().out
    hits = json.loads(out)
    assert isinstance(hits, list)
    assert len(hits) > 0
    for hit in hits:
        assert "text" in hit
        assert "metadata" in hit
        assert "score" in hit
        assert isinstance(hit["score"], (int, float))


def test_recall_top_k_limits_results(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--top-k", "2", "--json"])
    rc = args.func(args)
    assert rc == 0
    out = capsys.readouterr().out
    hits = json.loads(out)
    assert len(hits) <= 2


def test_recall_filter_narrows_results(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--filter", "tag=alpha", "--json"])
    rc = args.func(args)
    assert rc == 0
    out = capsys.readouterr().out
    hits = json.loads(out)
    assert len(hits) > 0
    for hit in hits:
        assert hit["metadata"].get("tag") == "alpha"


def test_recall_public_scope_never_returns_private_record(
    data_dir: str, seeded: None, capsys
) -> None:
    """A recall in the public 'default' scope must NOT return the private 'secret' record."""
    parser = _build_parser()
    args = parser.parse_args(
        [
            "recall",
            "secret",
            "--scope",
            "default",
            "--visibility",
            "public",
            "--json",
        ]
    )
    rc = args.func(args)
    assert rc == 0
    out = capsys.readouterr().out
    hits = json.loads(out)
    for hit in hits:
        assert hit["id"] != "secret1"


def test_recall_text_mode(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record"])
    rc = args.func(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "score:" in out
    assert "text:" in out


def test_recall_malformed_filter_raises_cli_error(data_dir: str, seeded: None) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--filter", "badfilter"])
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR


def test_recall_default_mode_is_hybrid() -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "anything"])
    assert args.mode == "hybrid"
    assert args.alpha == 0.5
    assert args.case_sensitive is False


def test_recall_exact_mode_matches_substring(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "alpha record", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    assert [h["id"] for h in hits] == ["a1"]


def test_recall_keyword_mode_drops_non_matches(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "alpha", "--mode", "keyword", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    # 'alpha' appears in a1's text only ("alpha record"); others have no overlap.
    assert {h["id"] for h in hits} == {"a1"}
    assert all(h["score"] > 0.0 for h in hits)


def test_recall_hybrid_mode_returns_scored_hits(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "hybrid", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    assert len(hits) > 0
    for hit in hits:
        assert hit["score"] is not None


def test_recall_bad_alpha_raises_cli_error(data_dir: str, seeded: None) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "hybrid", "--alpha", "2.0"])
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR
