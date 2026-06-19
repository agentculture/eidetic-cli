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

from eidetic.cli._errors import CliError, EXIT_USER_ERROR
from eidetic.cli._output import emit_result
from eidetic.memory.backend import get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _build_records(args: argparse.Namespace) -> list[Record]:
    """Parse input into a list of Record instances."""
    if args.record is not None:
        try:
            data = json.loads(args.record)
        except json.JSONDecodeError as exc:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"invalid JSON: {exc}",
                remediation="pass a single JSON object string, e.g. {{'id': 'r1', 'text': 'hello'}}",
            ) from exc
        inputs: list[dict[str, Any]] = [data]
    else:
        raw = sys.stdin.read()
        inputs = []
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

    records: list[Record] = []
    for d in inputs:
        if "id" not in d or "text" not in d:
            raise CliError(
                code=EXIT_USER_ERROR,
                message="record missing required key 'id' or 'text'",
                remediation="each record must have 'id' and 'text' keys",
            )
        if "scope" in d:
            records.append(Record.from_dict(d))
        else:
            records.append(
                Record(
                    id=d["id"],
                    text=d["text"],
                    type=d.get("type", "note"),
                    hash=d.get("hash", ""),
                    metadata=d.get("metadata", {}),
                    scope=Scope(args.scope, args.visibility),
                )
            )
    return records


def cmd_remember(args: argparse.Namespace) -> int:
    records = _build_records(args)
    backend = get_backend(args.backend)
    ids: list[str] = []
    for record in records:
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
