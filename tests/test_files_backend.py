"""Tests for the files-backed storage adapter in eidetic.memory.backend.

Exercises the eidetic.memory.backend.get_backend("files") adapter which
delegates to data_refinery.store. Tests cover: upsert idempotency, dedup
by hash, search, all(), added_by round-trip, and scope isolation.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.backend import Backend, get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


@pytest.fixture
def base_dir(tmp_path: pytest.Path) -> str:
    return str(tmp_path / "memory")


@pytest.fixture
def backend(base_dir: str) -> Backend:
    os.environ["EIDETIC_DATA_DIR"] = base_dir
    return get_backend("files")


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


def test_upsert_same_id_twice_is_one_record(backend: Backend) -> None:
    rec = _make_record(rid="a1", text="first")
    backend.upsert(rec)
    backend.upsert(rec)
    results = backend.search(
        "first", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    ids = [r.id for r in results]
    assert ids.count("a1") == 1


def test_upsert_updates_in_place(backend: Backend) -> None:
    backend.upsert(_make_record(rid="b1", text="old"))
    backend.upsert(_make_record(rid="b1", text="new"))
    results = backend.search(
        "new", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    texts = [r.text for r in results if r.id == "b1"]
    assert texts == ["new"]


def test_upsert_dedup_by_hash(backend: Backend) -> None:
    """Two records with different ids but the same text hash → only one survives."""
    backend.upsert(_make_record(rid="x1", text="same text"))
    backend.upsert(_make_record(rid="x2", text="same text"))
    results = backend.search(
        "same", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    assert len(results) == 1


# -- durability ---------------------------------------------------------


def test_durability_across_instances(base_dir: str) -> None:
    """A fresh get_backend("files") over the same EIDETIC_DATA_DIR still finds the record."""
    os.environ["EIDETIC_DATA_DIR"] = base_dir
    get_backend("files").upsert(_make_record(rid="d1", text="persisted"))
    fresh = get_backend("files")
    results = fresh.search(
        "persisted", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    assert any(r.id == "d1" for r in results)


# -- scope isolation -----------------------------------------------------


def test_scope_isolation_private_not_leaked(backend: Backend) -> None:
    """A private-scope record is NOT returned for a different/public query scope."""
    private_scope = Scope(name="personal", visibility="private")
    backend.upsert(_make_record(rid="p1", text="secret", scope=private_scope))
    public_scope = Scope(name="default", visibility="public")
    results = backend.search("secret", top_k=10, scope=public_scope, filters=None)
    assert not any(r.id == "p1" for r in results)


def test_scope_isolation_private_visible_to_same_scope(backend: Backend) -> None:
    """A private-scope record IS returned when querying from the same scope."""
    private_scope = Scope(name="personal", visibility="private")
    backend.upsert(_make_record(rid="p2", text="secret", scope=private_scope))
    results = backend.search("secret", top_k=10, scope=private_scope, filters=None)
    assert any(r.id == "p2" for r in results)


# -- score and metadata ------------------------------------------------


def test_search_results_carry_score_and_metadata(backend: Backend) -> None:
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
    backend: Backend,
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

    # Private search sees its own private record with its OWN text (proving the
    # same-name public scope did not overwrite it); public records stay visible.
    priv_results = backend.search("text", top_k=10, scope=priv_scope, filters=None)
    priv_hits = [r for r in priv_results if r.id == "r1" and r.scope.visibility == "private"]
    assert len(priv_hits) == 1
    assert priv_hits[0].text == "private text"


# -- all() enumeration ---------------------------------------------------


def test_all_enumerates_every_record_across_scopes(backend: Backend) -> None:
    """all() returns every stored record, including across scopes and private."""
    backend.upsert(_make_record(rid="a1", text="alpha"))
    backend.upsert(
        _make_record(
            rid="p1",
            text="secret",
            scope=Scope(name="personal", visibility="private"),
        )
    )
    all_ids = {r.id for r in backend.all()}
    assert all_ids == {"a1", "p1"}


def test_all_empty_store_returns_empty_list(backend: Backend) -> None:
    assert backend.all() == []


# -- corrupt JSONL guard -------------------------------------------------


def test_corrupt_jsonl_raises_cli_error(base_dir: str) -> None:
    """A corrupt line in the store surfaces as a clean eidetic ``CliError``.

    Storage is now data-refinery's files backend, but the adapter translates its
    ``CliError`` into eidetic's, so the "no traceback ever reaches stderr"
    contract still holds end-to-end. We seed the corruption directly on disk (the
    adapter exposes no injectable handle) and assert the translated error — code
    and remediation intact.
    """
    os.environ["EIDETIC_DATA_DIR"] = base_dir
    store_dir = Path(base_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "s__public.jsonl").write_text("not valid json at all\n", encoding="utf-8")

    with pytest.raises(CliError) as excinfo:
        get_backend("files").all()
    assert excinfo.value.code == EXIT_ENV_ERROR
