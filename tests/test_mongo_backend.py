"""Tests for eidetic.memory.backends.mongo — MongoBackend (no live Mongo required)."""

from __future__ import annotations

import pytest

from eidetic.memory.backends.mongo import MongoBackend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope

# -----------------------------------------------------------------------
# In-memory collection stub
# -----------------------------------------------------------------------


class _FakeCollection:
    """Minimal in-memory stub supporting replace_one(upsert) and find()."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    def replace_one(self, filter_: dict, doc: dict, upsert: bool = False) -> None:
        _id = filter_["_id"]
        if _id in self._store:
            self._store[_id] = doc
        elif upsert:
            self._store[_id] = doc

    def find(self, query: dict | None = None) -> list[dict]:
        # Simple filter: only match docs where all query keys exist and equal
        results = list(self._store.values())
        if not query:
            return results
        filtered: list[dict] = []
        for doc in results:
            match = True
            for key, value in query.items():
                # Support dotted keys like "metadata.tag"
                parts = key.split(".")
                current = doc
                for part in parts:
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        match = False
                        break
                if not match or current != value:
                    match = False
            if match:
                filtered.append(doc)
        return filtered


class _FakeDatabase:
    """Minimal in-memory database stub."""

    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]


class _FakeClient:
    """Minimal in-memory client stub."""

    def __init__(self, *args, **kwargs) -> None:
        self._databases: dict[str, _FakeDatabase] = {}

    def __getitem__(self, name: str) -> _FakeDatabase:
        if name not in self._databases:
            self._databases[name] = _FakeDatabase()
        return self._databases[name]

    def close(self) -> None:
        pass


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def backend() -> MongoBackend:
    """Return a MongoBackend wired to an in-memory fake client."""
    fake_client = _FakeClient()
    return MongoBackend(client=fake_client)


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


# -----------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------


def test_upsert_same_id_twice_is_one_record(backend: MongoBackend) -> None:
    """Upsert is idempotent: same id twice → one doc."""
    rec = _make_record(rid="a1", text="first")
    backend.upsert(rec)
    backend.upsert(rec)
    results = backend.search(
        "first",
        top_k=10,
        scope=Scope(name="default", visibility="public"),
        filters=None,
    )
    ids = [r.id for r in results]
    assert ids.count("a1") == 1


def test_upsert_updates_in_place(backend: MongoBackend) -> None:
    """Upsert with the same id replaces the previous doc."""
    backend.upsert(_make_record(rid="b1", text="old"))
    backend.upsert(_make_record(rid="b1", text="new"))
    results = backend.search(
        "new",
        top_k=10,
        scope=Scope(name="default", visibility="public"),
        filters=None,
    )
    texts = [r.text for r in results if r.id == "b1"]
    assert texts == ["new"]


def test_search_returns_records_with_score(backend: MongoBackend) -> None:
    """Search maps stored docs to Records with a numeric score."""
    rec = _make_record(rid="s1", text="scored record", metadata={"tag": "test"})
    backend.upsert(rec)
    results = backend.search(
        "scored",
        top_k=10,
        scope=Scope(name="default", visibility="public"),
        filters=None,
    )
    hit = [r for r in results if r.id == "s1"]
    assert len(hit) == 1
    r = hit[0]
    assert isinstance(r.score, float)
    assert r.metadata == {"tag": "test"}


def test_search_private_scope_isolation(backend: MongoBackend) -> None:
    """A private-scope record is dropped for a different/public query scope."""
    private_scope = Scope(name="personal", visibility="private")
    backend.upsert(_make_record(rid="p1", text="secret", scope=private_scope))
    public_scope = Scope(name="default", visibility="public")
    results = backend.search(
        "secret",
        top_k=10,
        scope=public_scope,
        filters=None,
    )
    assert not any(r.id == "p1" for r in results)


def test_search_private_visible_to_same_scope(backend: MongoBackend) -> None:
    """A private-scope record IS returned when querying from the same scope."""
    private_scope = Scope(name="personal", visibility="private")
    backend.upsert(_make_record(rid="p2", text="secret", scope=private_scope))
    results = backend.search(
        "secret",
        top_k=10,
        scope=private_scope,
        filters=None,
    )
    assert any(r.id == "p2" for r in results)


def test_search_respects_top_k(backend: MongoBackend) -> None:
    """Search returns at most top_k results."""
    for i in range(5):
        backend.upsert(_make_record(rid=f"t{i}", text=f"record {i}"))
    results = backend.search(
        "record",
        top_k=2,
        scope=Scope(name="default", visibility="public"),
        filters=None,
    )
    assert len(results) <= 2


def test_search_metadata_filters(backend: MongoBackend) -> None:
    """Search applies metadata filters via the find query."""
    backend.upsert(_make_record(rid="m1", text="alpha", metadata={"tag": "important"}))
    backend.upsert(_make_record(rid="m2", text="beta", metadata={"tag": "trivial"}))
    results = backend.search(
        "alpha",
        top_k=10,
        scope=Scope(name="default", visibility="public"),
        filters={"tag": "important"},
    )
    ids = [r.id for r in results]
    assert "m1" in ids
    assert "m2" not in ids


def test_all_enumerates_every_record(backend: MongoBackend) -> None:
    """all() returns every stored doc as a Record, ignoring scope visibility."""
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


def test_all_empty_store_returns_empty_list(backend: MongoBackend) -> None:
    assert backend.all() == []


def test_build_returns_mongo_backend() -> None:
    """Module-level build() returns a MongoBackend instance."""
    from eidetic.memory.backends import mongo

    instance = mongo.build()
    assert isinstance(instance, MongoBackend)


def test_search_includes_doc_without_stored_embedding() -> None:
    """A doc with no stored 'embedding' is still searchable.

    Ranking recomputes embeddings (or scores lexically) at query time, so docs
    written before embedding support are no longer silently dropped from search.
    """
    client = _FakeClient()
    client["eidetic_memory"]["records"].replace_one(
        {"_id": "noemb"},
        {
            "_id": "noemb",
            "id": "noemb",
            "text": "no embedding",
            "type": "note",
            "hash": "h",
            "metadata": {},
            "scope": {"name": "default", "visibility": "public"},
        },
        upsert=True,
    )
    backend = MongoBackend(client=client)
    results = backend.search(
        "no embedding",
        top_k=10,
        scope=Scope(name="default", visibility="public"),
        filters=None,
        mode="keyword",
    )
    assert [r.id for r in results] == ["noemb"]
    assert results[0].score is not None


def test_close_with_fake_client() -> None:
    """A backend built with a fake client can close() without error."""
    fake_client = _FakeClient()
    backend = MongoBackend(client=fake_client)
    backend.close()  # should not raise


def test_close_never_connected_is_noop() -> None:
    """A never-connected MongoBackend().close() is a no-op."""
    backend = MongoBackend()
    backend.close()  # should not raise
