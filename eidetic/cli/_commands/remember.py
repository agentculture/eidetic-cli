"""``eidetic-cli remember`` — ingest memory records.

Accepts a single JSON object as a positional argument, or NDJSON from stdin
for bulk ingest. Each record is upserted (idempotent by id) into the configured
memory backend.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.cli._output import emit_result
from eidetic.memory.backend import get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _collect_inputs(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Parse input into a list of raw dicts (one JSON object or NDJSON stdin)."""
    if args.record is not None:
        try:
            data = json.loads(args.record)
        except json.JSONDecodeError as exc:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"invalid JSON: {exc}",
                remediation="pass one JSON object string, or NDJSON on stdin",
            ) from exc
        return [data]

    raw = sys.stdin.read()
    inputs: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            inputs.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"invalid JSON on line: {exc}",
                remediation="each non-blank stdin line must be a valid JSON object",
            ) from exc
    return inputs


def _record_from_input(d: dict[str, Any], args: argparse.Namespace) -> Record:
    """Validate *d* and construct a Record, using *args* for scope defaults."""
    missing = [k for k in ("id", "text", "type") if k not in d]
    if missing:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"record missing required key(s): {', '.join(missing)}",
            remediation="each record must have 'id', 'text', and 'type' keys",
        )
    # score is recall-only; never store a caller-supplied score
    d = {k: v for k, v in d.items() if k != "score"}
    if "scope" in d:
        sc = d["scope"]
        if not isinstance(sc, dict) or "name" not in sc or "visibility" not in sc:
            raise CliError(
                code=EXIT_USER_ERROR,
                message="record 'scope' must be an object with 'name' and 'visibility'",
                remediation="omit 'scope' to use the --scope/--visibility flags instead",
            )
        record = Record.from_dict(d)
        record.score = None
        return record
    record = Record(
        id=d["id"],
        text=d["text"],
        type=d["type"],
        hash=d.get("hash", ""),
        metadata=d.get("metadata", {}),
        scope=Scope(args.scope, args.visibility),
    )
    record.score = None
    return record


def cmd_remember(args: argparse.Namespace) -> int:
    inputs = _collect_inputs(args)
    backend = get_backend(args.backend)
    ids: list[str] = []
    for d in inputs:
        record = _record_from_input(d, args)
        backend.upsert(record)
        ids.append(record.id)

    if getattr(args, "json", False):
        emit_result({"upserted": len(ids), "ids": ids}, json_mode=True)
    else:
        emit_result(f"Upserted {len(ids)} record(s).", json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "remember",
        help="Ingest one or more memory records (JSON arg or NDJSON stdin).",
    )
    p.add_argument(
        "record",
        nargs="?",
        default=None,
        help="A single JSON object string; omit to read NDJSON from stdin.",
    )
    p.add_argument(
        "--backend",
        choices=["files", "neo4j", "mongo"],
        default="files",
        help="Memory backend to use (default: files).",
    )
    p.add_argument(
        "--scope",
        default="default",
        help="Scope name for the record(s) (default: default).",
    )
    p.add_argument(
        "--visibility",
        choices=["public", "private"],
        default="public",
        help="Record visibility (default: public).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON output.",
    )
    p.set_defaults(func=cmd_remember)
