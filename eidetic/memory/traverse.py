"""Pure bounded BFS traversal engine over links/supersedes (t1).

Given a set of seed records plus two caller-injected callables — a fetch-by-id
function and a serve-predicate — walk the memory graph breadth-first,
following each record's ``links`` (list of ids) and ``supersedes`` (single id
or ``None``) fields. This module performs **no I/O**: it never reads the
clock, never imports a store or ``data_refinery``, and never touches a
backend directly. The caller (a later CLI task) supplies ``fetch`` (typically
backed by ``StoreBackend.get_many``) and ``can_serve`` (typically
:func:`eidetic.memory.scope.can_serve` bound to the querying scope), so the
engine stays fully unit-testable in isolation, exactly like
:mod:`eidetic.memory.lifecycle`.

Discovery order is deterministic: breadth-first by hop depth, and within a
level, parents are walked in the order they were themselves discovered, with
each parent's own edges walked ``links`` (in list order) then ``supersedes``.
Seeds are depth 0 and are never re-emitted as discoveries, even when a cycle
or a duplicate link leads back to a seed id. ``max_nodes`` bounds the count of
*discovered* nodes only — seeds never count against it.

The ``can_serve`` predicate runs on **every** hop, not only at the seed/entry
level: a record it rejects never enters the result at any depth. Because an
unservable record cannot itself be shown to the caller, its own
``links``/``supersedes`` are not walked further either — traversal treats a
rejected record as a dead end, exactly as if ``fetch`` had returned ``None``
for it. A record already discovered (or a seed) is never re-fetched or
re-queued, so cycles terminate naturally.

Whenever a bound (``max_depth`` or ``max_nodes``) genuinely stops the walk
short of material that was there to find, :attr:`TraversalResult.truncated` is
set — the walk never cuts silently. "Genuinely" is load-bearing: at the depth
bound each unvisited edge is resolved and admission-tested before it counts as
truncation, so a dangling id or a record the predicate rejects never inflates
the flag, and ``truncated`` never becomes a side channel announcing that
out-of-scope material exists.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence

from eidetic.memory.record import Record

EdgeKind = Literal["links", "supersedes"]


@dataclass
class TraversalNode:
    """A single record discovered while walking the graph.

    ``record`` is the discovered record itself, ``depth`` is its hop distance
    from the nearest seed (a seed's direct neighbors are depth 1), and ``via``
    names the edge kind that led to it.
    """

    record: Record
    depth: int
    via: EdgeKind


@dataclass
class TraversalResult:
    """Outcome of :func:`discover`.

    ``nodes`` is the ordered, breadth-first list of discovered, servable
    records — seeds are never included. ``truncated`` is ``True`` when
    ``max_depth`` or ``max_nodes`` stopped the walk short of material that was
    genuinely reachable.
    """

    nodes: list[TraversalNode] = field(default_factory=list)
    truncated: bool = False


def _edges(record: Record) -> list[tuple[str, EdgeKind]]:
    edges: list[tuple[str, EdgeKind]] = [(rid, "links") for rid in record.links]
    if record.supersedes:
        edges.append((record.supersedes, "supersedes"))
    return edges


def discover(
    seeds: Sequence[Record],
    fetch: Callable[[str], Record | None],
    can_serve: Callable[[Record], bool],
    max_depth: int,
    max_nodes: int,
) -> TraversalResult:
    """Breadth-first walk of ``links``/``supersedes`` from *seeds* (PURE).

    ``fetch`` resolves an id to a :class:`Record`, or ``None`` when the id is
    dangling (skipped without error). ``can_serve`` decides, for each
    discovered record, whether it may be included at all; it is evaluated on
    every hop, not only at the seed level, and a rejected record is not
    expanded further. ``max_depth`` bounds hop distance from the seeds;
    ``max_nodes`` bounds the number of discovered nodes (seeds never count
    against it). Neither bound truncates silently — hitting either sets
    :attr:`TraversalResult.truncated`.
    """
    visited: set[str] = {seed.id for seed in seeds}
    queue: deque[tuple[Record, int]] = deque((seed, 0) for seed in seeds)
    nodes: list[TraversalNode] = []
    truncated = False

    while queue:
        parent, depth = queue.popleft()
        if depth >= max_depth:
            truncated = truncated or _has_admissible_beyond(parent, visited, fetch, can_serve)
            continue
        admitted, hit_budget = _expand(
            parent, depth, visited, fetch, can_serve, max_nodes - len(nodes)
        )
        nodes.extend(admitted)
        queue.extend((node.record, node.depth) for node in admitted)
        if hit_budget:
            return TraversalResult(nodes=nodes, truncated=True)

    return TraversalResult(nodes=nodes, truncated=truncated)


def _has_admissible_beyond(
    record: Record,
    visited: set[str],
    fetch: Callable[[str], Record | None],
    can_serve: Callable[[Record], bool],
) -> bool:
    """Whether *record* has an unvisited edge the caller could actually have seen.

    The depth bound only *truncates* if it stopped the walk short of material
    that was genuinely there to find, so each unvisited edge is resolved and
    admission-tested rather than merely counted: a dangling id, or a record the
    predicate would reject, was never visible material and cutting before it
    cuts nothing. This also keeps ``truncated`` from acting as a side channel —
    a private record beyond the bound must not make a public bundle announce
    that something was withheld.
    """
    for rid, _kind in _edges(record):
        if rid in visited:
            continue
        candidate = fetch(rid)
        if candidate is not None and can_serve(candidate):
            return True
    return False


def _expand(
    parent: Record,
    depth: int,
    visited: set[str],
    fetch: Callable[[str], Record | None],
    can_serve: Callable[[Record], bool],
    budget: int,
) -> tuple[list[TraversalNode], bool]:
    """Admissible children of *parent*, and whether *budget* stopped the walk.

    ``can_serve`` is consulted for every candidate at every depth — a rejected
    record is neither admitted nor expanded, so it can never act as a transit
    node to material the caller may not see.
    """
    admitted: list[TraversalNode] = []
    for rid, kind in _edges(parent):
        if rid in visited:
            continue
        candidate = fetch(rid)
        if candidate is None:
            continue
        visited.add(rid)
        if not can_serve(candidate):
            continue
        if len(admitted) >= budget:
            return admitted, True
        admitted.append(TraversalNode(record=candidate, depth=depth + 1, via=kind))
    return admitted, False
