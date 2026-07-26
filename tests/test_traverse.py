"""Tests for eidetic.memory.traverse — the PURE bounded-BFS traversal engine.

These cover the t1 rules with no I/O:

1. BFS walks ``links`` + ``supersedes`` from seeds, bounded by ``max_depth``
   and ``max_nodes``; hitting either bound sets ``truncated`` — never a silent
   cut, and a bound that is reached without cutting anything real leaves
   ``truncated`` false.
2. ``can_serve`` runs on EVERY hop, not just at the seed level: a rejected
   record never enters the result at any depth, and a rejected record is not
   expanded further (a dead end, exactly like a dangling ``fetch``).
3. Purity: no I/O, no clock, no store import; deterministic BFS-level
   discovery order; cycles terminate; dangling links are skipped without
   error; seeds are never re-emitted as discoveries; ``max_nodes`` counts
   discovered nodes only, never seeds.
"""

from __future__ import annotations

import inspect
from dataclasses import fields
from typing import Callable

from eidetic.memory import scope as scope_mod
from eidetic.memory import traverse
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope
from eidetic.memory.traverse import TraversalNode, TraversalResult, discover

_PUBLIC = Scope(name="default", visibility="public")
_PRIVATE = Scope(name="default", visibility="private")


def _rec(
    rid: str,
    *,
    text: str = "text",
    scope: Scope | None = None,
    links: list[str] | None = None,
    supersedes: str | None = None,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata={},
        scope=scope or _PUBLIC,
        links=links or [],
        supersedes=supersedes,
    )


def _fetcher(*records: Record) -> Callable[[str], Record | None]:
    by_id = {r.id: r for r in records}
    return by_id.get


def _accept_all(_record: Record) -> bool:
    return True


def _serve_from(query_scope: Scope) -> Callable[[Record], bool]:
    def _predicate(record: Record) -> bool:
        return scope_mod.can_serve(query_scope, record.scope)

    return _predicate


def _ids(result: TraversalResult) -> list[str]:
    return [node.record.id for node in result.nodes]


# -- shape / dataclasses --------------------------------------------------


def test_traversal_node_shape() -> None:
    names = {f.name for f in fields(TraversalNode)}
    assert names == {"record", "depth", "via"}


def test_traversal_result_shape_and_defaults() -> None:
    names = {f.name for f in fields(TraversalResult)}
    assert names == {"nodes", "truncated"}
    empty = TraversalResult()
    assert empty.nodes == []
    assert empty.truncated is False


# -- basic BFS: links + supersedes, depth numbering ------------------------


def test_direct_neighbor_via_links_is_depth_one() -> None:
    a = _rec("A", links=["B"])
    b = _rec("B")
    result = discover([a], _fetcher(b), _accept_all, max_depth=5, max_nodes=20)
    assert _ids(result) == ["B"]
    assert result.nodes[0].depth == 1
    assert result.nodes[0].via == "links"
    assert result.truncated is False


def test_direct_neighbor_via_supersedes_is_depth_one() -> None:
    a = _rec("A", supersedes="B")
    b = _rec("B")
    result = discover([a], _fetcher(b), _accept_all, max_depth=5, max_nodes=20)
    assert _ids(result) == ["B"]
    assert result.nodes[0].via == "supersedes"


def test_edges_are_walked_links_before_supersedes() -> None:
    a = _rec("A", links=["X", "Y"], supersedes="Z")
    x, y, z = _rec("X"), _rec("Y"), _rec("Z")
    result = discover([a], _fetcher(x, y, z), _accept_all, max_depth=5, max_nodes=20)
    assert _ids(result) == ["X", "Y", "Z"]
    kinds = [node.via for node in result.nodes]
    assert kinds == ["links", "links", "supersedes"]


def test_bfs_level_order_across_multiple_seeds_with_dedup() -> None:
    s1 = _rec("S1", links=["A", "B"])
    s2 = _rec("S2", links=["C"])
    a = _rec("A", links=["D"])
    b = _rec("B")
    c = _rec("C", links=["D"])
    d = _rec("D")
    result = discover(
        [s1, s2],
        _fetcher(a, b, c, d),
        _accept_all,
        max_depth=5,
        max_nodes=20,
    )
    assert _ids(result) == ["A", "B", "C", "D"]
    depths = {node.record.id: node.depth for node in result.nodes}
    assert depths == {"A": 1, "B": 1, "C": 1, "D": 2}
    d_node = next(n for n in result.nodes if n.record.id == "D")
    assert d_node.via == "links"
    assert result.truncated is False


# -- cycles, dangling links, seed re-emission ------------------------------


def test_cycle_terminates_without_error_or_false_truncation() -> None:
    a = _rec("A", links=["B"])
    b = _rec("B", links=["A"])
    result = discover([a], _fetcher(b), _accept_all, max_depth=5, max_nodes=20)
    assert _ids(result) == ["B"]
    assert result.truncated is False


def test_dangling_link_is_skipped_without_error() -> None:
    a = _rec("A", links=["ghost"])
    result = discover([a], _fetcher(), _accept_all, max_depth=5, max_nodes=20)
    assert result.nodes == []
    assert result.truncated is False


def test_seed_reachable_via_link_is_never_reemitted() -> None:
    s1 = _rec("S1", links=["S2"])
    s2 = _rec("S2")
    result = discover([s1, s2], _fetcher(s1, s2), _accept_all, max_depth=5, max_nodes=20)
    assert result.nodes == []
    assert result.truncated is False


# -- max_depth bound --------------------------------------------------------


def test_max_depth_bound_sets_truncated_when_more_exists() -> None:
    a = _rec("A", links=["B"])
    b = _rec("B", links=["C"])
    c = _rec("C")
    result = discover([a], _fetcher(b, c), _accept_all, max_depth=1, max_nodes=20)
    assert _ids(result) == ["B"]
    assert result.truncated is True


def test_max_depth_bound_reached_with_nothing_beyond_is_not_truncated() -> None:
    a = _rec("A", links=["B"])
    b = _rec("B")
    result = discover([a], _fetcher(b), _accept_all, max_depth=1, max_nodes=20)
    assert _ids(result) == ["B"]
    assert result.truncated is False


def test_depth_bound_with_only_dangling_edges_beyond_is_not_truncated() -> None:
    """A dangling id beyond the bound was never material, so nothing was cut.

    Counting unvisited edge ids without resolving them would flag truncation
    here and mislead a consumer into thinking visible material was withheld.
    """
    a = _rec("A", links=["B"])
    b = _rec("B", links=["gone"])
    result = discover([a], _fetcher(b), _accept_all, max_depth=1, max_nodes=20)
    assert _ids(result) == ["B"]
    assert result.truncated is False


def test_depth_bound_with_only_unservable_records_beyond_is_not_truncated() -> None:
    """An out-of-scope record beyond the bound must not set ``truncated``.

    Otherwise the flag becomes a side channel: a public caller could infer that
    a private record exists just past the boundary.
    """
    a = _rec("A", links=["B"])
    b = _rec("B", links=["secret"])
    secret = _rec("secret", scope=_PRIVATE)
    result = discover(
        [a],
        _fetcher(b, secret),
        lambda record: record.scope != _PRIVATE,
        max_depth=1,
        max_nodes=20,
    )
    assert _ids(result) == ["B"]
    assert result.truncated is False


def test_depth_bound_whose_only_edges_lead_back_to_visited_nodes_is_not_truncated() -> None:
    """A cycle closing exactly at the depth bound cut nothing — nothing lies beyond.

    ``B`` sits at the bound and its only edge points back to the already-visited
    seed. Counting that edge would report truncation for material the caller has
    already been given, which is the same over-reporting the dangling and
    out-of-scope cases guard against.
    """
    a = _rec("A", links=["B"])
    b = _rec("B", links=["A"])
    result = discover([a], _fetcher(a, b), _accept_all, max_depth=1, max_nodes=20)
    assert _ids(result) == ["B"]
    assert result.truncated is False


def test_max_depth_zero_discovers_nothing() -> None:
    a = _rec("A", links=["B"])
    b = _rec("B")
    result = discover([a], _fetcher(b), _accept_all, max_depth=0, max_nodes=20)
    assert result.nodes == []
    assert result.truncated is True


# -- max_nodes bound ---------------------------------------------------------


def test_max_nodes_bound_stops_at_exact_count_and_flags_truncated() -> None:
    a = _rec("A", links=["B"])
    b = _rec("B", links=["C"])
    c = _rec("C", links=["D"])
    d = _rec("D")
    result = discover([a], _fetcher(b, c, d), _accept_all, max_depth=10, max_nodes=2)
    assert _ids(result) == ["B", "C"]
    assert result.truncated is True


def test_max_nodes_matching_total_available_is_not_truncated() -> None:
    a = _rec("A", links=["B"])
    b = _rec("B")
    result = discover([a], _fetcher(b), _accept_all, max_depth=10, max_nodes=1)
    assert _ids(result) == ["B"]
    assert result.truncated is False


def test_max_nodes_counts_discovered_not_seeds() -> None:
    seeds = [_rec(f"S{i}") for i in range(5)]
    fetch = _fetcher()
    result = discover(seeds, fetch, _accept_all, max_depth=5, max_nodes=1)
    assert result.nodes == []
    assert result.truncated is False


def test_no_truncation_when_bounds_are_never_actually_hit() -> None:
    a = _rec("A", links=["B"])
    b = _rec("B")
    result = discover([a], _fetcher(b), _accept_all, max_depth=50, max_nodes=50)
    assert _ids(result) == ["B"]
    assert result.truncated is False


# -- can_serve runs on every hop (the discriminating regression) -----------


def test_can_serve_rejects_a_deep_private_record_reachable_only_through_an_accepted_hop() -> None:
    """Regression: a private record two hops from a public seed must be absent.

    Seed (public, depth 0) --links--> A (public, depth 1, ACCEPTED)
                                        --links--> B (private, depth 2, REJECTED)

    B is reachable ONLY through A, and A itself passes ``can_serve``. An
    implementation that checks the predicate only at the seed/entry level (or
    only on immediate seed-neighbors) would let A's already-accepted status
    wave B through too — this test requires the predicate to be re-applied at
    B's own hop, independent of A having passed.
    """
    seed = _rec("seed", scope=_PUBLIC, links=["A"])
    a = _rec("A", scope=_PUBLIC, links=["B"])
    b = _rec("B", scope=_PRIVATE)
    query_scope = _PUBLIC

    result = discover(
        [seed],
        _fetcher(a, b),
        _serve_from(query_scope),
        max_depth=5,
        max_nodes=20,
    )

    ids = _ids(result)
    assert "A" in ids
    assert "B" not in ids
    a_node = next(n for n in result.nodes if n.record.id == "A")
    assert a_node.depth == 1


def test_can_serve_rejection_is_a_dead_end_not_expanded_further() -> None:
    """A rejected record's own links are not walked — no leak via its children."""
    seed = _rec("seed", scope=_PUBLIC, links=["private_mid"])
    private_mid = _rec("private_mid", scope=_PRIVATE, links=["public_leaf"])
    public_leaf = _rec("public_leaf", scope=_PUBLIC)
    query_scope = _PUBLIC

    result = discover(
        [seed],
        _fetcher(private_mid, public_leaf),
        _serve_from(query_scope),
        max_depth=5,
        max_nodes=20,
    )

    assert result.nodes == []
    assert result.truncated is False


def test_can_serve_runs_at_every_depth_not_only_seed_neighbors() -> None:
    """A chain of three accepted hops with a rejected fourth is still caught."""
    seed = _rec("seed", scope=_PUBLIC, links=["A"])
    a = _rec("A", scope=_PUBLIC, links=["B"])
    b = _rec("B", scope=_PUBLIC, links=["C"])
    c = _rec("C", scope=_PRIVATE)
    query_scope = _PUBLIC

    result = discover(
        [seed],
        _fetcher(a, b, c),
        _serve_from(query_scope),
        max_depth=10,
        max_nodes=20,
    )

    ids = _ids(result)
    assert ids == ["A", "B"]
    assert "C" not in ids


# -- purity: no I/O, no clock, no store import ------------------------------


def test_module_is_pure_no_io_no_clock_no_store_import() -> None:
    source = inspect.getsource(traverse)
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    forbidden_modules = ("data_refinery", "datetime", "time", "os", "requests", "httpx")
    for line in import_lines:
        for module in forbidden_modules:
            assert not (
                line == f"import {module}"
                or line.startswith(f"import {module}.")
                or line.startswith(f"import {module} ")
                or line.startswith(f"from {module}")
                or line.startswith(f"from {module}.")
            ), f"forbidden import {line!r} found in traverse.py"
    assert "open(" not in source
