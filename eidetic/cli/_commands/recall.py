"""``eidetic-cli recall`` — search the memory store.

Agent-first: register + handler; --json supported; failures raise CliError,
never a traceback.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from typing import Any

from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.cli._output import emit_result
from eidetic.memory.backend import BACKEND_CHOICES, get_backend
from eidetic.memory.scope import Scope
from eidetic.memory.scoring import signal_strength


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


def _filter_lifecycle(
    hits: list,
    include_shadowed: bool,
    include_archived: bool,
) -> list:
    """Remove shadowed/archived records unless the corresponding flag is set."""
    result = []
    for hit in hits:
        lc = getattr(hit, "lifecycle", "active")
        if lc == "shadowed" and not include_shadowed:
            continue
        if lc == "archived" and not include_archived:
            continue
        result.append(hit)
    return result


def cmd_recall(args: argparse.Namespace) -> int:
    filters = _parse_filters(getattr(args, "filters", None))
    scope = Scope(args.scope, args.visibility)
    include_shadowed: bool = getattr(args, "include_shadowed", False)
    include_archived: bool = getattr(args, "include_archived", False)

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

    # Build output payload from the query-time (pre-bump) state.
    if getattr(args, "json", False):
        payload: list[dict[str, Any]] = [hit.to_dict() for hit in hits]
        emit_result(payload, json_mode=True)
    else:
        out: list[str] = []
        for hit in hits:
            lines: list[str] = [f"score: {hit.score:.4f}", f"text: {hit.text}"]
            for k, v in hit.metadata.items():
                lines.append(f"  {k}: {v}")
            out.append("\n".join(lines))
        emit_result("\n\n".join(out) if out else "(no results)", json_mode=False)

    # Passive reinforcement: bump recall_count and last_recall on COPIES and
    # persist via upsert.  We use copies so the already-emitted objects (above)
    # are untouched — their recall_count / last_recall remain at the pre-bump
    # values, keeping this call's emitted payload stable.
    now_iso = now.isoformat()
    for hit in hits:
        bumped = copy.copy(hit)
        bumped.recall_count = hit.recall_count + 1
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
        help="Search the memory store and return matching records.",
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
        help="Maximum number of results to return (default: 5).",
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
        help="Metadata facet filter (repeatable).",
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
        help="Emit results as a JSON list to stdout.",
    )
    p.set_defaults(func=cmd_recall)
