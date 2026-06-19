"""Dedicated scope-isolation tests for eidetic memory.

Proves the load-bearing privacy invariant: a record in a PRIVATE scope never
appears in a recall scoped to a different or public scope.
"""

from __future__ import annotations

import argparse
import json
import os

import pytest

from eidetic.cli._commands import recall
from eidetic.memory.backend import get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope, can_serve


@pytest.fixture
def data_dir(tmp_path) -> str:
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    recall.register(sub)
    return parser


# -- (a) can_serve policy checks ----------------------------------------


def test_can_serve_private_not_served_to_different_scope() -> None:
    """A private record is NOT served to a different scope."""
    assert can_serve(Scope("default", "public"), Scope("secret", "private")) is False


def test_can_serve_private_served_to_same_scope() -> None:
    """A private record IS served when the query scope matches exactly."""
    assert can_serve(Scope("secret", "private"), Scope("secret", "private")) is True


def test_can_serve_public_served_to_any_scope() -> None:
    """A public record is served to any query scope."""
    assert can_serve(Scope("secret", "private"), Scope("default", "public")) is True
    assert can_serve(Scope("other", "public"), Scope("default", "public")) is True


# -- (b) search isolation: public scope must NOT return private records --


def test_search_public_scope_excludes_private_record(data_dir: str) -> None:
    """A search in the public 'default' scope never returns a private 'secret' record."""
    backend = get_backend("files")
    backend.upsert(_make_record("pub1", "shared keyword", scope=Scope("default", "public")))
    backend.upsert(_make_record("priv1", "shared keyword", scope=Scope("secret", "private")))

    results = backend.search(
        "shared keyword",
        top_k=10,
        scope=Scope("default", "public"),
        filters=None,
    )

    ids = [r.id for r in results]
    assert "pub1" in ids
    assert "priv1" not in ids


# -- (c) recall VERB isolation: private scope returns its own record ----


def test_recall_public_scope_via_verb_excludes_private(data_dir: str, capsys) -> None:
    """A recall in the public 'default' scope never returns the private 'secret' record."""
    backend = get_backend("files")
    backend.upsert(_make_record("pub1", "shared keyword", scope=Scope("default", "public")))
    backend.upsert(_make_record("priv1", "shared keyword", scope=Scope("secret", "private")))

    parser = _build_parser()
    args = parser.parse_args(["recall", "shared keyword", "--scope", "default", "--json"])
    args.func(args)
    out = capsys.readouterr().out
    hits = json.loads(out)

    for hit in hits:
        assert hit["scope"]["name"] != "secret"


def test_recall_private_scope_via_verb_returns_private(data_dir: str, capsys) -> None:
    """A recall in the private 'secret' scope DOES return the private record."""
    backend = get_backend("files")
    backend.upsert(_make_record("pub1", "shared keyword", scope=Scope("default", "public")))
    backend.upsert(_make_record("priv1", "shared keyword", scope=Scope("secret", "private")))

    parser = _build_parser()
    args = parser.parse_args(
        ["recall", "shared keyword", "--scope", "secret", "--visibility", "private", "--json"]
    )
    args.func(args)
    out = capsys.readouterr().out
    hits = json.loads(out)

    ids = [h["id"] for h in hits]
    assert "priv1" in ids
    assert "pub1" in ids  # public records are visible to private scopes too
