"""Tests for eidetic.memory.backends.neo4j — Neo4jBackend (mocked + live)."""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock

import pytest

from eidetic.cli._errors import CliError
from eidetic.memory.backends.neo4j import Neo4jBackend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope

# ---------------------------------------------------------------------------
# Live-neo4j fixture + skip guard
# ---------------------------------------------------------------------------

_NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
_NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
_NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")  # None means no-auth


def _neo4j_reachable() -> bool:
    """Return True iff a live Neo4j instance is reachable."""
    try:
        import neo4j

        opts = {}
        if _NEO4J_PASSWORD:
            drv = neo4j.GraphDatabase.driver(
                _NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASSWORD), connection_timeout=2, **opts
            )
        else:
            drv = neo4j.GraphDatabase.driver(_NEO4J_URI, connection_timeout=2)
        drv.verify_connectivity()
        drv.close()
        return True
    except Exception:
        return False


requires_live_neo4j = pytest.mark.skipif(
    not _neo4j_reachable(),
    reason="live Neo4j not reachable — skipping live round-trip tests",
)


@pytest.fixture()
def live_neo4j_backend():
    """Yield a real Neo4jBackend connected to the live store; clean up after."""
    backend = Neo4jBackend(
        uri=_NEO4J_URI,
        user=_NEO4J_USER,
        password=_NEO4J_PASSWORD,
    )
    yield backend
    backend.close()


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


# -- fake driver helpers -------------------------------------------------


def _fake_node(record: Record) -> dict:
    """Build a dict that looks like a Neo4j node for *record*."""
    return {
        "id": record.id,
        "text": record.text,
        "type": record.type,
        "hash": record.hash,
        "metadata": json.dumps(record.metadata),
        "scope_name": record.scope.name,
        "scope_visibility": record.scope.visibility,
        "embedding": [0.1, 0.2, 0.3],
    }


def _fake_driver(nodes: list[dict]) -> MagicMock:
    """Return a MagicMock driver whose session().run() yields *nodes*."""
    driver = MagicMock()
    session = MagicMock()

    run_mock = MagicMock()

    def _run(query: str, params: dict | None = None) -> list[dict]:
        # Return rows shaped like Neo4j result rows
        return [{"m": n} for n in nodes]

    run_mock.side_effect = _run
    session.run = run_mock
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=None)
    driver.session.return_value = session
    return driver


# -- upsert tests --------------------------------------------------------


def test_upsert_issues_merge() -> None:
    """upsert sends a MERGE query keyed on id."""
    driver = _fake_driver([])
    backend = Neo4jBackend(driver=driver)
    rec = _make_record(rid="u1", text="test upsert")
    backend.upsert(rec)

    session = driver.session.return_value
    session.run.assert_called_once()
    call_args = session.run.call_args
    query = call_args[0][0]
    params = call_args[0][1]

    assert "MERGE" in query
    assert "{id: $id}" in query
    assert params["id"] == "u1"
    assert params["text"] == "test upsert"
    assert params["scope_name"] == "default"
    assert params["scope_visibility"] == "public"


def test_upsert_stores_metadata() -> None:
    """upsert JSON-encodes the metadata dict."""
    driver = _fake_driver([])
    backend = Neo4jBackend(driver=driver)
    rec = _make_record(rid="m1", text="meta test", metadata={"tag": "important"})
    backend.upsert(rec)

    session = driver.session.return_value
    call_args = session.run.call_args
    params = call_args[0][1]
    stored = json.loads(params["metadata"])
    assert stored == {"tag": "important"}


# -- search tests --------------------------------------------------------


def test_search_returns_records_with_score() -> None:
    """search maps fake rows to Records carrying metadata and score."""
    rec = _make_record(rid="s1", text="searchable record", metadata={"env": "test"})
    driver = _fake_driver([_fake_node(rec)])
    backend = Neo4jBackend(driver=driver)

    results = backend.search(
        "searchable",
        top_k=10,
        scope=Scope(name="default", visibility="public"),
        filters=None,
    )

    assert len(results) == 1
    r = results[0]
    assert r.id == "s1"
    assert r.metadata == {"env": "test"}
    assert isinstance(r.score, float)


def test_search_embeds_at_query_time() -> None:
    """approximate search embeds query + candidate text fresh via embed_detect.

    Ranking recomputes embeddings at query time (one batch) rather than reading
    the node's stored embedding, so vector dimensions always match and behaviour
    is identical across backends.
    """
    rec = _make_record(rid="e1", text="embedded record")
    driver = _fake_driver([_fake_node(rec)])
    backend = Neo4jBackend(driver=driver)

    original = backend._embed.embed_detect
    calls: list[list[str]] = []

    def tracked(texts: list[str]):  # type: ignore[no-untyped-def]
        calls.append(texts)
        return original(texts)

    backend._embed.embed_detect = tracked  # type: ignore[method-assign]

    results = backend.search(
        "embedded",
        top_k=10,
        scope=Scope(name="default", visibility="public"),
        filters=None,
        mode="approximate",
    )

    assert len(results) == 1
    # One batch call embedding the query AND the candidate text together.
    assert len(calls) == 1
    assert calls[0] == ["embedded", "embedded record"]


def test_search_respects_top_k() -> None:
    """search returns at most top_k records."""
    recs = [_make_record(rid=f"t{i}", text=f"record {i}") for i in range(5)]
    driver = _fake_driver([_fake_node(r) for r in recs])
    backend = Neo4jBackend(driver=driver)

    results = backend.search(
        "record",
        top_k=2,
        scope=Scope(name="default", visibility="public"),
        filters=None,
    )

    assert len(results) <= 2


def test_search_drops_private_record_for_public_scope() -> None:
    """A private-scope record is dropped when querying from a public scope."""
    private_rec = _make_record(
        rid="priv1",
        text="secret data",
        scope=Scope(name="personal", visibility="private"),
    )
    public_rec = _make_record(
        rid="pub1",
        text="public data",
        scope=Scope(name="default", visibility="public"),
    )
    driver = _fake_driver([_fake_node(private_rec), _fake_node(public_rec)])
    backend = Neo4jBackend(driver=driver)

    results = backend.search(
        "data",
        top_k=10,
        scope=Scope(name="default", visibility="public"),
        filters=None,
    )

    ids = [r.id for r in results]
    assert "priv1" not in ids
    assert "pub1" in ids


def test_search_drops_private_record_for_different_scope() -> None:
    """A private-scope record is dropped for a different private scope."""
    private_rec = _make_record(
        rid="priv1",
        text="secret data",
        scope=Scope(name="personal", visibility="private"),
    )
    driver = _fake_driver([_fake_node(private_rec)])
    backend = Neo4jBackend(driver=driver)

    results = backend.search(
        "data",
        top_k=10,
        scope=Scope(name="other", visibility="private"),
        filters=None,
    )

    ids = [r.id for r in results]
    assert "priv1" not in ids


# -- all() enumeration ---------------------------------------------------


def test_all_enumerates_every_record() -> None:
    """all() maps every node to a Record, ignoring scope visibility."""
    pub = _make_record(rid="a1", text="alpha")
    priv = _make_record(
        rid="p1",
        text="secret",
        scope=Scope(name="personal", visibility="private"),
    )
    driver = _fake_driver([_fake_node(pub), _fake_node(priv)])
    backend = Neo4jBackend(driver=driver)
    all_ids = {r.id for r in backend.all()}
    assert all_ids == {"a1", "p1"}


def test_all_empty_store_returns_empty_list() -> None:
    driver = _fake_driver([])
    backend = Neo4jBackend(driver=driver)
    assert backend.all() == []


# -- driver connection errors ------------------------------------------


def test_build_raises_cli_error_on_missing_driver() -> None:
    """build() raises CliError when neo4j is not importable."""
    import sys

    real_neo4j = sys.modules.get("neo4j")
    try:
        sys.modules["neo4j"] = None
        backend = Neo4jBackend()
        # Force driver creation
        with pytest.raises(CliError) as exc_info:
            backend.upsert(_make_record())
        err = exc_info.value
        assert err.code == 2  # EXIT_ENV_ERROR
    finally:
        if real_neo4j is None:
            sys.modules.pop("neo4j", None)
        else:
            sys.modules["neo4j"] = real_neo4j


def test_driver_connection_error_wrapped() -> None:
    """Connection errors are wrapped in CliError with remediation."""
    driver = MagicMock()
    driver.session.side_effect = ConnectionError("refused")
    backend = Neo4jBackend(driver=driver)

    with pytest.raises(CliError) as exc_info:
        backend.upsert(_make_record())

    err = exc_info.value
    assert err.code == 2
    assert err.remediation


def test_close_with_fake_driver() -> None:
    """A backend built with a fake driver can close() and the driver's close is called."""
    driver = MagicMock()
    backend = Neo4jBackend(driver=driver)
    backend.close()
    driver.close.assert_called_once()


def test_close_never_connected_is_noop() -> None:
    """A never-connected Neo4jBackend().close() is a no-op."""
    backend = Neo4jBackend()
    backend.close()  # should not raise


# -- temporal / lifecycle field round-trip (qodo "Neo4j loses lifecycle state")


def test_upsert_persists_temporal_lifecycle_fields() -> None:
    """upsert writes created/last_recall/recall_count/links/supersedes/lifecycle
    into the Cypher params, and never persists query-time score/signal."""
    driver = _fake_driver([])
    backend = Neo4jBackend(driver=driver)
    rec = Record(
        id="lc1",
        text="lifecycle record",
        type="note",
        hash="",
        metadata={},
        scope=Scope(name="default", visibility="public"),
        created="2026-01-01T00:00:00+00:00",
        last_recall="2026-02-01T00:00:00+00:00",
        recall_count=3,
        links=["a", "b"],
        supersedes="old1",
        lifecycle="shadowed",
    )
    backend.upsert(rec)

    call_args = driver.session.return_value.run.call_args
    query, params = call_args[0][0], call_args[0][1]
    assert params["created"] == "2026-01-01T00:00:00+00:00"
    assert params["last_recall"] == "2026-02-01T00:00:00+00:00"
    assert params["recall_count"] == 3
    assert params["links"] == ["a", "b"]
    assert params["supersedes"] == "old1"
    assert params["lifecycle"] == "shadowed"
    # query-time fields are recall-output-only — never persisted
    assert "score" not in params
    assert "signal" not in params
    assert "m.lifecycle = $lifecycle" in query


def test_node_to_record_round_trips_lifecycle_fields() -> None:
    """A node carrying the lifecycle properties maps back onto the Record."""
    node = {
        "id": "lc2",
        "text": "round trip",
        "type": "note",
        "hash": "h",
        "metadata": "{}",
        "scope_name": "default",
        "scope_visibility": "public",
        "embedding": [0.1, 0.2, 0.3],
        "created": "2026-03-01T00:00:00+00:00",
        "last_recall": "2026-03-05T00:00:00+00:00",
        "recall_count": 7,
        "links": ["x", "y"],
        "supersedes": "p",
        "lifecycle": "archived",
    }
    driver = _fake_driver([node])
    backend = Neo4jBackend(driver=driver)
    rec = backend.all()[0]
    assert rec.created == "2026-03-01T00:00:00+00:00"
    assert rec.last_recall == "2026-03-05T00:00:00+00:00"
    assert rec.recall_count == 7
    assert rec.links == ["x", "y"]
    assert rec.supersedes == "p"
    assert rec.lifecycle == "archived"


def test_node_to_record_legacy_node_uses_defaults() -> None:
    """A legacy node (written before lifecycle fields existed) loads with the
    same safe defaults as Record.from_dict()."""
    legacy = {
        "id": "old",
        "text": "legacy",
        "type": "note",
        "hash": "h",
        "metadata": "{}",
        "scope_name": "default",
        "scope_visibility": "public",
        "embedding": [0.1],
    }
    driver = _fake_driver([legacy])
    backend = Neo4jBackend(driver=driver)
    rec = backend.all()[0]
    assert rec.recall_count == 0
    assert rec.lifecycle == "active"
    assert rec.links == []
    assert rec.last_recall is None
    assert rec.supersedes is None


# -- live round-trip: added_by (t3) ----------------------------------------


@requires_live_neo4j
def test_live_upsert_roundtrips_added_by(live_neo4j_backend: Neo4jBackend) -> None:
    """upsert() followed by reload returns added_by unchanged (live neo4j)."""
    rid = f"test-added-by-{uuid.uuid4().hex}"
    rec = Record(
        id=rid,
        text="round-trip added_by",
        type="note",
        hash="",
        metadata={},
        scope=Scope(name="test", visibility="public"),
        added_by="agent:test-runner",
    )
    live_neo4j_backend.upsert(rec)

    all_records = live_neo4j_backend.all()
    match = next((r for r in all_records if r.id == rid), None)
    assert match is not None, f"record {rid!r} not found after upsert"
    assert match.added_by == "agent:test-runner"

    # Cleanup: delete the test node
    live_neo4j_backend._run("MATCH (m:Memory {id: $id}) DELETE m", {"id": rid})


@requires_live_neo4j
def test_live_node_without_added_by_loads_as_none(live_neo4j_backend: Neo4jBackend) -> None:
    """A node written WITHOUT added_by loads as added_by is None (legacy compat)."""
    rid = f"test-no-added-by-{uuid.uuid4().hex}"
    # Write a node directly via Cypher WITHOUT the added_by property to
    # simulate a legacy node that predates this field.
    live_neo4j_backend._run(
        "CREATE (m:Memory {id: $id, text: $text, type: $type, hash: $hash, "
        "metadata: $metadata, scope_name: $scope_name, "
        "scope_visibility: $scope_visibility, "
        "recall_count: 0, lifecycle: 'active', links: []})",
        {
            "id": rid,
            "text": "legacy node without added_by",
            "type": "note",
            "hash": "",
            "metadata": "{}",
            "scope_name": "test",
            "scope_visibility": "public",
        },
    )

    all_records = live_neo4j_backend.all()
    match = next((r for r in all_records if r.id == rid), None)
    assert match is not None, f"legacy node {rid!r} not found"
    assert match.added_by is None

    # Cleanup
    live_neo4j_backend._run("MATCH (m:Memory {id: $id}) DELETE m", {"id": rid})
