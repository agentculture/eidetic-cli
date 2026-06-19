"""Tests for eidetic.memory.backends.files — FilesBackend."""

from __future__ import annotations

import pytest

from eidetic.memory.backends.files import FilesBackend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


@pytest.fixture
def base_dir(tmp_path: pytest.Path) -> str:
    return str(tmp_path / "memory")


@pytest.fixture
def backend(base_dir: str) -> FilesBackend:
    return FilesBackend(base_dir=base_dir)


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


# -- idempotent upsert ---------------------------------------------------


def test_upsert_same_id_twice_is_one_record(backend: FilesBackend) -> None:
    rec = _make_record(rid="a1", text="first")
    backend.upsert(rec)
    backend.upsert(rec)
    results = backend.search(
        "first", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    ids = [r.id for r in results]
    assert ids.count("a1") == 1


def test_upsert_updates_in_place(backend: FilesBackend) -> None:
    backend.upsert(_make_record(rid="b1", text="old"))
    backend.upsert(_make_record(rid="b1", text="new"))
    results = backend.search(
        "new", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    texts = [r.text for r in results if r.id == "b1"]
    assert texts == ["new"]


def test_upsert_dedup_by_hash(backend: FilesBackend) -> None:
    """Two records with different ids but the same text hash → only one survives."""
    backend.upsert(_make_record(rid="x1", text="same text"))
    backend.upsert(_make_record(rid="x2", text="same text"))
    results = backend.search(
        "same", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    assert len(results) == 1


# -- durability ---------------------------------------------------------


def test_durability_across_instances(base_dir: str) -> None:
    """A fresh FilesBackend over the same dir still finds the record."""
    FilesBackend(base_dir=base_dir).upsert(_make_record(rid="d1", text="persisted"))
    fresh = FilesBackend(base_dir=base_dir)
    results = fresh.search(
        "persisted", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    assert any(r.id == "d1" for r in results)


# -- scope isolation -----------------------------------------------------


def test_scope_isolation_private_not_leaked(backend: FilesBackend) -> None:
    """A private-scope record is NOT returned for a different/public query scope."""
    private_scope = Scope(name="personal", visibility="private")
    backend.upsert(_make_record(rid="p1", text="secret", scope=private_scope))
    public_scope = Scope(name="default", visibility="public")
    results = backend.search("secret", top_k=10, scope=public_scope, filters=None)
    assert not any(r.id == "p1" for r in results)


def test_scope_isolation_private_visible_to_same_scope(backend: FilesBackend) -> None:
    """A private-scope record IS returned when querying from the same scope."""
    private_scope = Scope(name="personal", visibility="private")
    backend.upsert(_make_record(rid="p2", text="secret", scope=private_scope))
    results = backend.search("secret", top_k=10, scope=private_scope, filters=None)
    assert any(r.id == "p2" for r in results)


# -- score and metadata ------------------------------------------------


def test_search_results_carry_score_and_metadata(backend: FilesBackend) -> None:
    rec = _make_record(rid="s1", text="scored record", metadata={"tag": "test"})
    backend.upsert(rec)
    results = backend.search(
        "scored", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    hit = [r for r in results if r.id == "s1"]
    assert len(hit) == 1
    r = hit[0]
    assert isinstance(r.score, float)
    assert r.metadata == {"tag": "test"}


# -- scope file collision ------------------------------------------------


def test_scope_file_no_collision_same_name_different_visibility(
    backend: FilesBackend,
) -> None:
    """Records with the same id in public and private scopes of the same name
    persist independently (no overwrite), and public search never returns private."""
    pub_scope = Scope(name="x", visibility="public")
    priv_scope = Scope(name="x", visibility="private")

    backend.upsert(_make_record(rid="r1", text="public text", scope=pub_scope))
    backend.upsert(_make_record(rid="r1", text="private text", scope=priv_scope))

    # Public search should only return the public record
    pub_results = backend.search("text", top_k=10, scope=pub_scope, filters=None)
    pub_hits = [r for r in pub_results if r.id == "r1"]
    assert len(pub_hits) == 1
    assert pub_hits[0].text == "public text"

    # Private search should only return the private record
    priv_results = backend.search("text", top_k=10, scope=priv_scope, filters=None)
    priv_hits = [r for r in priv_results if r.id == "r1"]
    assert len(priv_hits) == 1
    assert priv_hits[0].text == "private text"


# -- corrupt JSONL guard -------------------------------------------------


def test_corrupt_jsonl_raises_cli_error(backend: FilesBackend, tmp_path: pytest.Path) -> None:
    """A JSONL file with a corrupt line raises CliError, not a bare exception."""
    from eidetic.cli._errors import CliError

    # Write a file with a corrupt line
    corrupt_file = tmp_path / "corrupt.jsonl"
    corrupt_file.write_text(
        '{"id": "ok", "text": "fine", "type": "note", "hash": "h", "metadata": {}, "scope": {"name": "d", "visibility": "public"}}\n'
        "this is not json\n",
        encoding="utf-8",
    )

    # Force the backend to read from this path
    backend._base = tmp_path
    with pytest.raises(CliError) as exc_info:
        backend.search("fine", top_k=10, scope=Scope(name="d", visibility="public"), filters=None)
    assert exc_info.value.code == 2  # EXIT_ENV_ERROR
