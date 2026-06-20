"""``eidetic-cli remember`` — ingest memory records.

Accepts a single JSON object as a positional argument, or NDJSON from stdin
for bulk ingest. Each record is upserted (idempotent by id) into the configured
memory backend.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

from eidetic.cli._commands.whoami import find_culture_yaml, read_agent_fields
from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.cli._output import emit_result
from eidetic.memory.backend import get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _resolve_nick() -> str | None:
    """Return the agent nick from culture.yaml, or None if culture.yaml is absent.

    Returns None only when culture.yaml cannot be found (e.g. a wheel install
    where no culture.yaml ships alongside the package). When culture.yaml IS
    present, the suffix it declares — even if it happens to equal the module
    default — is the agent's real configured mesh identity and is returned as-is.
    """
    # No culture.yaml at all (e.g. wheel install) => no agent identity to stamp.
    if find_culture_yaml() is None:
        return None
    nick = read_agent_fields().get("nick")
    return nick or None


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

    # t4: stamp created date if not provided by the caller
    if "created" not in d:
        d["created"] = datetime.now(timezone.utc).isoformat()

    # t2: stamp added_by if not present in the record JSON.
    # Resolution order: --added-by flag > agent nick > None.
    # An explicit value in the record JSON is always preserved verbatim.
    if "added_by" not in d:
        flag_value = getattr(args, "added_by", None)
        d["added_by"] = flag_value if flag_value is not None else _resolve_nick()

    # t4: preserve supersedes and links from input
    # (from_dict and Record() both handle these via defaults)

    if "scope" in d:
        sc = d["scope"]
        if not isinstance(sc, dict) or "name" not in sc or "visibility" not in sc:
            raise CliError(
                code=EXIT_USER_ERROR,
                message="record 'scope' must be an object with 'name' and 'visibility'",
                remediation="omit 'scope' to use the --scope/--visibility flags instead",
            )
        # hash/metadata are optional per the record contract (hash is derived from
        # text when blank). from_dict reads them as required keys, so supply the
        # same defaults the no-scope path below uses — otherwise an inline-scope
        # record without them KeyErrors instead of upserting (broke #3's NDJSON path).
        d.setdefault("hash", "")
        d.setdefault("metadata", {})
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
        created=d.get("created"),
        supersedes=d.get("supersedes"),
        links=d.get("links", []),
        added_by=d.get("added_by"),
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
        "--added-by",
        default=None,
        dest="added_by",
        help=(
            "Override the agent identity stamped on ingested records. "
            "Defaults to the agent's mesh nick; falls back to None."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON output.",
    )
    p.set_defaults(func=cmd_remember)
