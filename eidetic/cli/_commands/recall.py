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

``--rerank`` adds an OPT-IN second pass over the primary tier. The stage sits at
one specific place in the pipeline and the position is load-bearing::

    backend.search()  ->  lifecycle filter  ->  rerank pool  ->  threshold  ->  [:top_k]

*After* the lifecycle filter, so a shadowed/archived record is never shipped to
the reranker; *before* the ``--top-k`` slice, so the wider ``--rerank-pool``
(default 50) can RESCUE a record the search mode ranked below k — which is the
whole reason the pool exists. Reordering is by the reranker's score, published
on each item as ``rerank_score``; the hybrid/BM25 ``score`` keeps its value and
is never overwritten, so a consumer can see both judgements.

The stage FAILS CLOSED. ``--rerank`` asks for the remote cross-encoder; if that
lane does not answer, the offline lexical-overlap fallback produces numbers on a
completely different distribution, and silently serving those as "reranked"
would be a lie. So an unanswered remote lane raises :class:`CliError` unless the
caller explicitly opts in with ``--rerank-allow-fallback``, and when the
fallback IS taken the bundle names the lane it used, a once-per-process warning
goes to stderr, and the (remote-calibrated) ``--rerank-threshold`` is not
applied to lexical scores.

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

from eidetic.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from eidetic.cli._output import emit_diagnostic, emit_result
from eidetic.memory.backend import BACKEND_CHOICES, Backend, get_backend
from eidetic.memory.record import Record
from eidetic.memory.rerank import LOCAL_LANE, REMOTE_LANE, RerankLane, apply_rerank
from eidetic.memory.scope import Scope, can_serve
from eidetic.memory.scoring import DECAY, signal_strength
from eidetic.memory.traverse import TraversalNode, TraversalResult, discover

# Caller-stated traversal bounds. Safe defaults: one hop out, twenty records.
DEFAULT_DEPTH = 1
DEFAULT_MAX_NODES = 20

# How many lifecycle-visible hits the rerank stage reconsiders. Deliberately
# much wider than the default --top-k of 5: the pool is the only place a record
# the search mode ranked below k can be rescued, so a pool no wider than k would
# make the stage a pure reshuffle of an already-decided answer.
DEFAULT_RERANK_POOL = 50

# Bundle tier labels.
TIER_PRIMARY = "primary"
TIER_TRAVERSAL = "traversal"

# The environment VARIABLES the embed/rerank client reads a bearer token from,
# in the order it checks them. Named in the fail-closed remediation because a
# 401 from a missing token is the likeliest reason the remote lane went quiet.
# These are variable NAMES only — a resolved key VALUE must never reach an
# error message, a log line, or the payload. Kept in step with
# ``eidetic.memory.embed``; tests/test_recall_rerank.py pins the two together.
RERANK_KEY_VARS: tuple[str, ...] = (
    "EIDETIC_EMBED_API_KEY",
    "COLLEAGUE_API_KEY",
    "CULTURE_VLLM_API_KEY",
)

# Emitted at most once per process, mirroring the withheld-key warning in
# eidetic.memory.embed: a lexical-lane rerank is a real degradation, so it is
# never silent — but repeating it per call would drown a batch consumer's
# stderr.
_warned_rerank_fallback = False


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


def _validate_pool(pool: int) -> None:
    """Reject a non-positive rerank pool with a structured user error.

    A zero/negative pool would silently empty the primary tier rather than
    "rerank nothing", so it is a caller mistake, not a documented escape hatch —
    the way to skip the stage is to omit ``--rerank``.
    """
    if pool < 1:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--rerank-pool must be >= 1 (got {pool})",
            remediation="omit --rerank to skip the rerank stage, or pass a positive pool size",
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


def _warn_rerank_fallback() -> None:
    """Warn once per process that the LEXICAL rerank lane produced the scores."""
    global _warned_rerank_fallback
    if _warned_rerank_fallback:
        return
    _warned_rerank_fallback = True
    emit_diagnostic(
        "warning: the remote reranker did not answer; --rerank-allow-fallback "
        "permitted the local lexical-overlap lane, whose scores are NOT "
        "comparable to cross-encoder scores (--rerank-threshold is skipped on "
        "this lane).\n"
        f"hint: check the reranker endpoint and the bearer token in "
        f"{' / '.join(RERANK_KEY_VARS)}, then rerun without "
        f"--rerank-allow-fallback to require the remote lane."
    )


def _rerank_unavailable_error() -> CliError:
    """Build the fail-closed error for a remote rerank lane that did not answer.

    The remediation names the credential VARIABLES only. It must never
    interpolate a resolved key value: this message reaches stderr, agent logs,
    and CI output, and a leaked bearer token there is unrecoverable.
    """
    return CliError(
        code=EXIT_ENV_ERROR,
        message=(
            "--rerank requested but the remote reranker did not answer; "
            "refusing to serve local lexical-overlap scores as reranked results"
        ),
        remediation=(
            "check that the reranker endpoint is reachable and that a bearer token is set "
            f"in one of {', '.join(RERANK_KEY_VARS)} (a 401 from a missing token is the "
            "usual cause; EIDETIC_EMBED_URL points the client). To accept the local "
            "lexical lane instead, rerun with --rerank-allow-fallback."
        ),
    )


def _rerank_stage(
    backend: Backend,
    query: str,
    pool: list[Record],
    *,
    threshold: float | None,
    allow_fallback: bool,
) -> tuple[list[Record], RerankLane | None, int]:
    """Rerank *pool* and return ``(records, lane, dropped)``.

    *pool* is the already-lifecycle-filtered, not-yet-top-k-sliced candidate
    list — see the module docstring for why that exact position matters. The
    reranker is reached through the backend's named ``embed_client`` seam, never
    the private ``_embed`` attribute.

    Fail-closed: when the remote lane did not answer and *allow_fallback* is
    False, this raises rather than returning lexical numbers dressed up as
    cross-encoder ones. An empty pool short-circuits without contacting the
    reranker at all — there is nothing to score, and a call with zero documents
    would be an unanswerable probe rather than evidence about the lane, so the
    returned lane is ``None``.

    Each surviving record gets its ``rerank_score`` set; ``score`` keeps its
    search-mode value and is never overwritten, so the bundle carries both
    judgements.
    """
    if not pool:
        return [], None, 0

    scores, online = backend.embed_client.rerank_detect(query, [record.text for record in pool])
    if not online:
        if not allow_fallback:
            raise _rerank_unavailable_error()
        _warn_rerank_fallback()
    lane: RerankLane = REMOTE_LANE if online else LOCAL_LANE

    # `threshold` is calibrated against the remote distribution; the engine
    # itself refuses to apply it on the local lane (we pass no
    # `lexical_threshold`, so the fallback lane drops nothing).
    result = apply_rerank(pool, scores, lane=lane, threshold=threshold)
    for record, score in zip(result.records, result.scores):
        record.rerank_score = score
    return result.records, lane, result.dropped


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
    rerank = payload.get("rerank")
    if rerank is not None:
        header += f"  rerank: {rerank['lane'] or 'none'} (dropped: {rerank['dropped']})"
    blocks: list[str] = []
    for item in payload["items"]:
        score = item["score"]
        rerank_score = item.get("rerank_score")
        lines = [
            f"[{item['tier']} depth={item['depth']}] id: {item['id']}",
            f"score: {score:.4f}" if isinstance(score, (int, float)) else "score: n/a",
        ]
        if isinstance(rerank_score, (int, float)):
            lines.append(f"rerank_score: {rerank_score:.4f}")
        lines.append(f"text: {item['text']}")
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

    # The rerank stage sits HERE — after the lifecycle filter (so a shadowed or
    # archived record is never sent to the reranker) and before the top-k slice
    # (so the wider pool can promote a record the search mode ranked below k).
    # Moving it either side of those two neighbours breaks one of those
    # guarantees; see the module docstring.
    rerank_lane: RerankLane | None = None
    rerank_dropped = 0
    if getattr(args, "rerank", False):
        pool_size = int(getattr(args, "rerank_pool", DEFAULT_RERANK_POOL))
        _validate_pool(pool_size)
        reranked, rerank_lane, rerank_dropped = _rerank_stage(
            backend,
            args.query,
            visible[:pool_size],
            threshold=getattr(args, "rerank_threshold", None),
            allow_fallback=bool(getattr(args, "rerank_allow_fallback", False)),
        )
        # Records the threshold dropped are gone from `visible` entirely: they
        # cannot be emitted in any tier, cannot seed the traversal, and cannot
        # be reinforced — all three read from `hits` below.
        visible = reranked
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
    if getattr(args, "rerank", False):
        # Present ONLY when the stage ran, so a default recall's payload keeps
        # exactly the keys it has always had. `lane` names which reranker
        # produced the ordering — a consumer must be able to tell a
        # cross-encoder ordering from a lexical-fallback one, since the two
        # score distributions mean different things. `dropped` reports a
        # relevance cut for the same reason `truncated` reports a bound cut
        # (issue #37): a cut is never silent. `lane` is null when the pool was
        # empty and no reranker ran.
        payload["rerank"] = {"lane": rerank_lane, "dropped": rerank_dropped}
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
        "--rerank",
        action="store_true",
        default=False,
        help=(
            "Rerank the primary hits with the cross-encoder reranker (opt-in; works "
            "with every --mode). Runs AFTER lifecycle filtering and BEFORE --top-k, so "
            "the pool can promote a record the search mode ranked below k. Items are "
            "ordered by rerank_score; each record's own search score is preserved. "
            "Fails closed if the remote reranker does not answer — see "
            "--rerank-allow-fallback."
        ),
    )
    p.add_argument(
        "--rerank-pool",
        type=int,
        dest="rerank_pool",
        default=DEFAULT_RERANK_POOL,
        metavar="N",
        help=(
            f"How many lifecycle-visible hits --rerank reconsiders (default: "
            f"{DEFAULT_RERANK_POOL}). Only pooled records can reach the primary tier, so "
            "keep this comfortably wider than --top-k. Ignored without --rerank."
        ),
    )
    p.add_argument(
        "--rerank-threshold",
        type=float,
        dest="rerank_threshold",
        default=None,
        metavar="F",
        help=(
            "Opt in to DROPPING primary hits scoring at or below this rerank score "
            "(default: none — --rerank reorders but never filters). The count cut is "
            "reported in the bundle, never silently. Calibrated against the remote "
            "cross-encoder, so it is not applied to lexical-fallback scores."
        ),
    )
    p.add_argument(
        "--rerank-allow-fallback",
        action="store_true",
        dest="rerank_allow_fallback",
        default=False,
        help=(
            "Permit --rerank to use the local lexical-overlap lane when the remote "
            "reranker does not answer. Without this, an unanswered remote lane is an "
            "error rather than a silent downgrade. When the fallback is taken the "
            "bundle names the lane and a warning goes to stderr."
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
