"""``eidetic-cli recall`` — search the memory store.

Agent-first: register + handler; --json supported; failures raise CliError,
never a traceback.
"""

from __future__ import annotations

import argparse
from typing import Any

from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.cli._output import emit_result
from eidetic.memory.backend import get_backend
from eidetic.memory.scope import Scope


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


def cmd_recall(args: argparse.Namespace) -> int:
    filters = _parse_filters(getattr(args, "filters", None))
    scope = Scope(args.scope, args.visibility)
    hits = get_backend(args.backend).search(
        args.query,
        args.top_k,
        scope,
        filters,
        args.mode,
        alpha=args.alpha,
        case_sensitive=args.case_sensitive,
    )

    # Provenance check: every hit must carry a numeric score.
    for hit in hits:
        if hit.score is None:
            raise CliError(
                code=EXIT_USER_ERROR,
                message="hit missing required score field",
                remediation="this is a backend bug; report it",
            )

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
        choices=["files", "neo4j", "mongo"],
        default="files",
        help="Storage backend to query (default: files).",
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
        "--json",
        action="store_true",
        help="Emit results as a JSON list to stdout.",
    )
    p.set_defaults(func=cmd_recall)
