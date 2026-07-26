"""``eidetic-cli recall`` — search the memory store and return a composite bundle.

Recall's result is ONE bundle object, not a flat hit list. A call runs the
requested search mode, then walks the ``links``/``supersedes`` graph outward
from those hits (:mod:`eidetic.memory.traverse`, resolving ids through
:meth:`~eidetic.memory.backend.StoreBackend.get_many` so both candidate stores
are spanned) and returns everything it found in one payload::

    {"query": ..., "mode": ..., "truncated": ...,
     "items": [{<every record field>, "tier": ..., "depth": ...}, ...]}

``tier`` is ``primary`` for a search hit (``depth`` 0) and ``traversal`` for a
record the walk discovered (``depth`` = hop distance), so a consumer attributes
every item without heuristics. Items keep the full record shape — id, verbatim
text, complete metadata, score, signal — because provenance is mandatory
(issue #3: recall without metadata is unusable).

Nothing on this path generates text: items are raw stored records, byte-for-byte,
and the only network call remains the embeddings endpoint the ranking modes
already use. The verb takes no caller-supplied content and persists nothing but
its own reinforcement bumps.

Bounds are the caller's to state: ``--depth`` (default 1) bounds hop distance and
``--max-nodes`` (default 20) bounds discovered nodes. Either bound cutting the
walk short sets ``truncated`` — never a silent cut. ``--depth 0`` skips the walk
entirely and reproduces a flat, primary-only bundle.

The public/private no-leak invariant holds at every hop: the predicate handed to
the traversal engine re-applies :func:`~eidetic.memory.scope.can_serve` to each
discovered record, so a private record reachable via ``links`` from a public hit
never enters a public bundle at any depth.

Agent-first: register + handler; --json supported; failures raise CliError,
never a traceback.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from typing import Any, Callable

from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.cli._output import emit_result
from eidetic.memory.backend import BACKEND_CHOICES, Backend, get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope, can_serve
from eidetic.memory.scoring import DECAY, signal_strength
from eidetic.memory.traverse import TraversalNode, TraversalResult, discover

# Caller-stated traversal bounds. Safe defaults: one hop out, twenty records.
DEFAULT_DEPTH = 1
DEFAULT_MAX_NODES = 20

# Bundle tier labels.
TIER_PRIMARY = "primary"
TIER_TRAVERSAL = "traversal"


def _parse_filters(raw: list[str] | None) -> dict[str, str] | None:
    """Parse ``--filter KEY=VALUE`` entries into a dict.

    A malformed entry (no ``=``) raises :class:`CliError`.
    Returns ``None`` when no filters were given.
    """
    if not raw:
        return None
    result: dict[str, str] = {}
    for entry in raw:
        if "=" not in entry:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"malformed filter: {entry!r}",
                remediation="filters must be in KEY=VALUE form",
            )
        key, _, value = entry.partition("=")
        result[key] = value
    return result


def _merge_source(filters: dict[str, str] | None, source: str | None) -> dict[str, str] | None:
    """Fold ``--source`` into the primary-search facet *filters*.

    ``--source`` is the first-class facet on ``metadata.source``; it constrains
    every tier (the traversal predicate applies it too), whereas the generic
    ``--filter`` facets select what the primary search matches. Giving both a
    ``source`` constraint with different values is contradictory and raises
    :class:`CliError` rather than silently returning nothing.
    """
    if source is None:
        return filters
    existing = (filters or {}).get("source")
    if existing is not None and existing != source:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"conflicting source constraint: --source {source!r} vs --filter source={existing!r}",
            remediation="pass one of them, or give both the same value",
        )
    merged = dict(filters or {})
    merged["source"] = source
    return merged


def _validate_bounds(depth: int, max_nodes: int) -> None:
    """Reject negative traversal bounds with a structured user error."""
    if depth < 0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--depth must be >= 0 (got {depth})",
            remediation="use --depth 0 for a primary-only bundle, or a positive hop count",
        )
    if max_nodes < 0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--max-nodes must be >= 0 (got {max_nodes})",
            remediation="pass a non-negative node budget, e.g. --max-nodes 20",
        )


def _lifecycle_visible(record: Record, include_shadowed: bool, include_archived: bool) -> bool:
    """Return True when *record*'s lifecycle state is visible under these flags."""
    lc = getattr(record, "lifecycle", "active")
    if lc == "shadowed" and not include_shadowed:
        return False
    if lc == "archived" and not include_archived:
        return False
    return True


def _filter_lifecycle(
    hits: list,
    include_shadowed: bool,
    include_archived: bool,
) -> list:
    """Remove shadowed/archived records unless the corresponding flag is set."""
    return [hit for hit in hits if _lifecycle_visible(hit, include_shadowed, include_archived)]


def _serve_predicate(
    scope: Scope,
    include_shadowed: bool,
    include_archived: bool,
    source: str | None,
) -> Callable[[Record], bool]:
    """Build the per-hop admission predicate handed to :func:`discover`.

    A discovered record is admitted only when it passes all three of the
    policies the primary tier already enforces: scope visibility
    (:func:`can_serve` — the no-leak invariant, re-checked at EVERY hop because
    ``remember`` accepts arbitrary cross-scope link ids), lifecycle filtering,
    and the ``--source`` facet. A rejected record is a dead end: the engine does
    not walk through it, so a filtered-out neighbour never acts as a transit
    node to material the caller asked not to see.
    """

    def predicate(record: Record) -> bool:
        if not can_serve(scope, record.scope):
            return False
        if not _lifecycle_visible(record, include_shadowed, include_archived):
            return False
        if source is not None and record.metadata.get("source") != source:
            return False
        return True

    return predicate


def _edge_ids(records: list[Record]) -> list[str]:
    """Return the de-duplicated ``links`` + ``supersedes`` ids of *records* in order."""
    ids: list[str] = []
    seen: set[str] = set()
    for record in records:
        for rid in [*record.links, *([record.supersedes] if record.supersedes else [])]:
            if rid not in seen:
                seen.add(rid)
                ids.append(rid)
    return ids


def _make_fetch(
    backend: Backend, scope: Scope, prefetch_ids: list[str]
) -> Callable[[str], Record | None]:
    """Return an id -> record resolver backed by :meth:`Backend.get_many`.

    ``get_many`` spans BOTH candidate store dirs (``data_refinery``'s own ``get``
    is single-store), so the walk resolves ids exactly like a search does. The
    first hop's ids — the whole traversal at the default ``--depth 1`` — are
    resolved in ONE batch call; deeper hops fall back to a single-id lookup.
    Results are memoised, and an id no store knows resolves to ``None`` so the
    engine treats a dangling link as a skip rather than an error.
    """
    cache: dict[str, Record | None] = {}
    if prefetch_ids:
        found = backend.get_many(prefetch_ids, scope)
        cache.update({rid: found.get(rid) for rid in prefetch_ids})

    def fetch(rid: str) -> Record | None:
        if rid not in cache:
            cache[rid] = backend.get_many([rid], scope).get(rid)
        return cache[rid]

    return fetch


def _traverse(
    backend: Backend,
    scope: Scope,
    seeds: list[Record],
    predicate: Callable[[Record], bool],
    depth: int,
    max_nodes: int,
) -> TraversalResult:
    """Walk the memory graph out from *seeds* (the primary hits).

    ``--depth 0`` is the documented escape hatch: no walk is attempted at all,
    so the result is empty and ``truncated`` stays False — opting out of the
    neighborhood is not a cut of a requested walk.
    """
    if depth <= 0 or not seeds:
        return TraversalResult()
    fetch = _make_fetch(backend, scope, _edge_ids(seeds))
    return discover(seeds, fetch, predicate, depth, max_nodes)


def _bump_amount(depth: int) -> float:
    """Reinforcement bump for a record recalled at hop *depth* (0 = primary hit).

    A primary hit (``depth <= 0``) reinforces fully; a traversal discovery
    decays by :data:`~eidetic.memory.scoring.DECAY` per hop (depth 1 -> 0.5,
    depth 2 -> 0.25, ...), so a background neighbourhood fetch does not age a
    record as aggressively as a deliberate foreground hit, while a record
    that keeps turning up adjacent to relevant matches is still recognised
    as genuinely used.
    """
    if depth <= 0:
        return 1.0
    return DECAY**depth


def _reinforcement_targets(
    hits: list[Record], nodes: list[TraversalNode]
) -> list[tuple[Record, int]]:
    """Return the (record, depth) pairs to reinforce, each record id once.

    Primary hits (depth 0) are collected first and win any id collision with
    a traversal discovery of the SAME record — the traversal engine's own
    visited-seed bookkeeping already keeps a primary hit's id out of *nodes*,
    but resolving the collision here too means a record reachable both ways
    is still bumped exactly once, at the fuller (primary) amount, rather than
    risking a double write to the store.
    """
    targets: dict[str, tuple[Record, int]] = {hit.id: (hit, 0) for hit in hits}
    for node in nodes:
        targets.setdefault(node.record.id, (node.record, node.depth))
    return list(targets.values())


def _bundle_item(record: Record, tier: str, depth: int) -> dict[str, Any]:
    """Serialise *record* as a bundle item: every record field plus tier + depth."""
    item = record.to_dict()
    item["tier"] = tier
    item["depth"] = depth
    return item


def _render_text(payload: dict[str, Any]) -> str:
    """Render the bundle for humans, keeping the tiers distinguishable."""
    header = (
        f"query: {payload['query']}  mode: {payload['mode']}  "
        f"truncated: {'yes' if payload['truncated'] else 'no'}"
    )
    blocks: list[str] = []
    for item in payload["items"]:
        score = item["score"]
        lines = [
            f"[{item['tier']} depth={item['depth']}] id: {item['id']}",
            f"score: {score:.4f}" if isinstance(score, (int, float)) else "score: n/a",
            f"text: {item['text']}",
        ]
        lines.extend(f"  {k}: {v}" for k, v in item["metadata"].items())
        blocks.append("\n".join(lines))
    return header + "\n\n" + ("\n\n".join(blocks) if blocks else "(no results)")


def cmd_recall(args: argparse.Namespace) -> int:
    source: str | None = getattr(args, "source", None)
    filters = _merge_source(_parse_filters(getattr(args, "filters", None)), source)
    scope = Scope(args.scope, args.visibility)
    include_shadowed: bool = getattr(args, "include_shadowed", False)
    include_archived: bool = getattr(args, "include_archived", False)
    depth: int = int(getattr(args, "depth", DEFAULT_DEPTH))
    max_nodes: int = int(getattr(args, "max_nodes", DEFAULT_MAX_NODES))
    _validate_bounds(depth, max_nodes)

    # Lifecycle filtering is applied BEFORE top-k so that top-k counts only
    # visible records.  We fetch all candidates from the backend (passing a
    # large sentinel for top_k would work, but better to fetch all and filter
    # here explicitly).  The backend's top_k cap is lifted by passing the
    # total record count via a very large number; the lifecycle filter then
    # brings the candidate set down to what the caller is allowed to see, and
    # we slice to args.top_k after.
    #
    # Implementation: pass top_k=2**31 so rank() never truncates, then we
    # truncate after lifecycle filtering.
    backend = get_backend(args.backend)
    all_hits = backend.search(
        args.query,
        2**31,  # fetch all ranked results; we apply top_k after lifecycle filter
        scope,
        filters,
        args.mode,
        alpha=args.alpha,
        case_sensitive=args.case_sensitive,
    )

    # Apply lifecycle filter BEFORE top-k truncation.
    visible = _filter_lifecycle(all_hits, include_shadowed, include_archived)
    hits = visible[: args.top_k]

    # Provenance check: every hit must carry a numeric score.
    for hit in hits:
        if hit.score is None:
            raise CliError(
                code=EXIT_USER_ERROR,
                message="hit missing required score field",
                remediation="this is a backend bug; report it",
            )

    # Single 'now' for the whole call — used for signal computation and
    # passive reinforcement timestamps.
    now = datetime.now(timezone.utc)

    # Set computed signal on each hit BEFORE serialising (for output).
    # We set signal directly on the record objects; these are the objects we
    # will emit.  We must NOT mutate recall_count / last_recall on the emitted
    # objects (those must reflect pre-bump state), so we emit first, bump copies.
    for hit in hits:
        hit.signal = signal_strength(hit, now)

    # The primary hits are the traversal seeds. Every discovered record passes
    # the same scope / lifecycle / source policy the primary tier enforces.
    traversal = _traverse(
        backend,
        scope,
        hits,
        _serve_predicate(scope, include_shadowed, include_archived, source),
        depth,
        max_nodes,
    )
    for node in traversal.nodes:
        node.record.signal = signal_strength(node.record, now)

    # Build output payload from the query-time (pre-bump) state.
    items = [_bundle_item(hit, TIER_PRIMARY, 0) for hit in hits]
    items.extend(_bundle_item(node.record, TIER_TRAVERSAL, node.depth) for node in traversal.nodes)
    payload: dict[str, Any] = {
        "query": args.query,
        "mode": args.mode,
        "truncated": traversal.truncated,
        "items": items,
    }
    emit_result(
        payload if getattr(args, "json", False) else _render_text(payload),
        json_mode=bool(getattr(args, "json", False)),
    )

    # Passive reinforcement: bump recall_count and last_recall on COPIES and
    # persist via upsert.  We use copies so the already-emitted objects (above)
    # are untouched — their recall_count / last_recall remain at the pre-bump
    # values, keeping this call's emitted payload stable.  Primary hits bump by
    # the full 1.0; traversal discoveries bump by the graded, depth-decayed
    # amount (_bump_amount).  A record excluded by scope or lifecycle never
    # reaches `hits` or `traversal.nodes` in the first place, so it is never a
    # reinforcement target — no separate check is needed here.
    now_iso = now.isoformat()
    for record, depth in _reinforcement_targets(hits, traversal.nodes):
        bumped = copy.copy(record)
        bumped.recall_count = record.recall_count + _bump_amount(depth)
        bumped.last_recall = now_iso
        # Query-time fields must never be persisted: `score` is recall-output
        # only, and `signal` is recomputed on every recall.  Clear them on the
        # copy so reinforcement writes back durable state only (and so the
        # mongo/neo4j upsert path is not handed a stale score to store).
        bumped.score = None
        bumped.signal = None
        backend.upsert(bumped)

    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "recall",
        help="Search the memory store and return a composite bundle of matches + neighbours.",
    )
    p.add_argument("query", help="Required search string.")
    p.add_argument(
        "--mode",
        choices=["exact", "approximate", "keyword", "hybrid"],
        default="hybrid",
        help=(
            "Search mode (default: hybrid). exact = case-insensitive substring; "
            "approximate = vector cosine (semantic); keyword = BM25 lexical; "
            "hybrid = weighted alpha blend of approximate + keyword."
        ),
    )
    p.add_argument(
        "--alpha",
        type=float,
        default=0.5,
        help=(
            "Hybrid blend weight in [0,1] (default: 0.5). final = "
            "alpha*approximate + (1-alpha)*keyword. Ignored unless --mode hybrid."
        ),
    )
    p.add_argument(
        "--case-sensitive",
        action="store_true",
        help="For --mode exact: require matching case (default: case-insensitive).",
    )
    p.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Maximum number of primary (search-hit) results to return (default: 5).",
    )
    p.add_argument(
        "--backend",
        choices=list(BACKEND_CHOICES),
        default="files",
        help="Storage backend to query (default: files; 'graph' is an alias for 'neo4j').",
    )
    p.add_argument(
        "--scope",
        default="default",
        help="Query scope name (default: default).",
    )
    p.add_argument(
        "--visibility",
        choices=["public", "private"],
        default="public",
        help="Query scope visibility (default: public).",
    )
    p.add_argument(
        "--filter",
        action="append",
        dest="filters",
        default=[],
        metavar="KEY=VALUE",
        help="Metadata facet filter on the primary search (repeatable).",
    )
    p.add_argument(
        "--source",
        default=None,
        metavar="SOURCE",
        help=(
            "Filter on metadata.source across BOTH tiers: primary hits and "
            "traversal discoveries alike (unlike --filter, which constrains the "
            "primary search only)."
        ),
    )
    p.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_DEPTH,
        metavar="N",
        help=(
            f"Traversal hop bound from the primary hits (default: {DEFAULT_DEPTH}). "
            "0 skips the traversal entirely for a flat, primary-only bundle."
        ),
    )
    p.add_argument(
        "--max-nodes",
        type=int,
        dest="max_nodes",
        default=DEFAULT_MAX_NODES,
        metavar="N",
        help=(
            f"Maximum number of traversal-discovered records (default: {DEFAULT_MAX_NODES}). "
            "Hitting this bound — or --depth — reports truncated=true in the payload."
        ),
    )
    p.add_argument(
        "--include-shadowed",
        action="store_true",
        dest="include_shadowed",
        default=False,
        help="Include records with lifecycle='shadowed' in results (excluded by default).",
    )
    p.add_argument(
        "--include-archived",
        action="store_true",
        dest="include_archived",
        default=False,
        help="Include records with lifecycle='archived' in results (excluded by default).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit the composite bundle object as JSON to stdout.",
    )
    p.set_defaults(func=cmd_recall)
