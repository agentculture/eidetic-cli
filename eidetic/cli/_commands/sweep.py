"""``eidetic-cli sweep`` — apply lifecycle transitions across the store.

Loads every stored record (via the backend's ``all()`` enumeration), runs the
PURE lifecycle engine (:mod:`eidetic.memory.lifecycle`) against a fixed ``now``,
upserts the records whose ``lifecycle`` changed, and reports counts plus advisory
conflict suggestions. Supports ``--json`` and ``--dry-run`` (report without
writing). Never deletes a record — it only ever flips ``lifecycle`` to
``shadowed`` / ``archived`` and persists the record in place.

Agent-first: register + handler; ``--json`` supported; failures raise
:class:`CliError`, never a traceback.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

from eidetic.cli._output import emit_result
from eidetic.memory.backend import BACKEND_CHOICES, get_backend
from eidetic.memory.lifecycle import compute_transitions


def cmd_sweep(args: argparse.Namespace) -> int:
    backend = get_backend(args.backend)
    # ``now`` is threaded through so the lifecycle pass is deterministic in tests;
    # it defaults to current UTC only here at the entry point.
    now = getattr(args, "now", None) or datetime.now(timezone.utc).isoformat()

    records = backend.all()
    result = compute_transitions(records, now)

    shadowed = sum(1 for r in result.changed if r.lifecycle == "shadowed")
    archived = sum(1 for r in result.changed if r.lifecycle == "archived")
    dry_run = bool(getattr(args, "dry_run", False))

    if not dry_run:
        # Never delete: only re-upsert the records whose lifecycle changed.
        for record in result.changed:
            backend.upsert(record)

    if getattr(args, "json", False):
        payload: dict[str, Any] = {
            "dry_run": dry_run,
            "scanned": len(records),
            "shadowed": shadowed,
            "archived": archived,
            "changed": [{"id": r.id, "lifecycle": r.lifecycle} for r in result.changed],
            "suggestions": result.suggestions,
        }
        emit_result(payload, json_mode=True)
    else:
        lines = [
            f"Scanned {len(records)} record(s).",
            f"  shadowed: {shadowed}",
            f"  archived: {archived}",
        ]
        if dry_run:
            lines.append("(dry-run: no changes written)")
        if result.suggestions:
            lines.append(f"Suggestions ({len(result.suggestions)}) — confirm manually:")
            for s in result.suggestions:
                lines.append(f"  - {s['reason']}: {', '.join(s['ids'])}")
        emit_result("\n".join(lines), json_mode=False)

    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "sweep",
        help="Apply lifecycle transitions (shadow/archive) across the store.",
    )
    p.add_argument(
        "--backend",
        choices=list(BACKEND_CHOICES),
        default="files",
        help="Memory backend to sweep (default: files; 'graph' is an alias for 'neo4j').",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report transitions without writing any change.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON output.",
    )
    p.set_defaults(func=cmd_sweep)
