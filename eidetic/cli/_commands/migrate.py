"""``eidetic-cli migrate`` — one-shot maintenance imports/upgrades.

Exposes two targets:

* ``migrate qq`` reads the three legacy "QQ" memory layers (markdown files,
  MongoDB, Neo4j) and upserts every mapped record idempotently into the
  configured backend. Each source reader is guarded: a down/absent Mongo or
  Neo4j is skipped with a warning (to stderr) and the run completes with the
  remaining sources. QQ files hold PERSONAL data, so migration writes into a
  PRIVATE scope by default (``--scope qq --visibility private``) — migrated
  personal knowledge never surfaces in a public recall.
* ``migrate store`` upgrades an existing store's on-disk format in place from
  the legacy Record JSONL to data-refinery's Envelope JSONL (issue #13). It is
  idempotent — already-migrated lines pass through untouched.

Agent-first: register + handler; ``--json`` supported; failures raise
:class:`CliError`, never a traceback.
"""

from __future__ import annotations

import argparse

from eidetic.cli._output import emit_result
from eidetic.memory import migrate_qq
from eidetic.memory.backend import BACKEND_CHOICES, get_backend, migrate_store
from eidetic.memory.scope import Scope


def cmd_migrate_qq(args: argparse.Namespace) -> int:
    scope = Scope(args.scope, args.visibility)
    file_paths = args.files if args.files else None

    report = migrate_qq.migrate_all(
        backend=get_backend(args.backend),
        file_paths=file_paths,
        scope=scope,
    )

    if getattr(args, "json", False):
        emit_result(report, json_mode=True)
    else:
        dest = f"{scope.name}/{scope.visibility}"
        lines: list[str] = [
            f"Migrated {report['total']} record(s) into scope {dest}.",
        ]
        for source, count in report["counts"].items():
            note = " (skipped — unavailable)" if source in report["skipped"] else ""
            lines.append(f"  {source}: {count}{note}")
        emit_result("\n".join(lines), json_mode=False)
    return 0


def cmd_migrate_store(args: argparse.Namespace) -> int:
    # eidetic constructs no write path — data-refinery owns the rewrite. The
    # returned summary is file-granularity: {backend, files, migrated,
    # migrated_files, skipped, dry_run}.
    report = migrate_store(data_dir=args.data_dir, dry_run=args.dry_run)

    if getattr(args, "json", False):
        emit_result(report, json_mode=True)
    else:
        verb = "Would rewrite" if report["dry_run"] else "Rewrote"
        emit_result(
            f"{verb} {report['migrated']} of {report['files']} store file(s) "
            f"to Envelope format ({report['skipped']} already current).",
            json_mode=False,
        )
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "migrate",
        help="One-shot import of legacy memory sources into the eidetic store.",
    )
    targets = p.add_subparsers(dest="target")
    qq = targets.add_parser(
        "qq",
        help="Migrate the legacy QQ memory (files + MongoDB + Neo4j).",
    )
    qq.add_argument(
        "--file",
        action="append",
        dest="files",
        default=[],
        metavar="PATH",
        help=(
            "QQ markdown source to read (repeatable). Defaults to the known "
            "core.md/notes.md paths when omitted."
        ),
    )
    qq.add_argument(
        "--files",
        action="store_true",
        dest="_files_flag",
        help=(
            "No-op marker accepted for readability (migration always reads "
            "files unless --file lists none on a machine without them)."
        ),
    )
    qq.add_argument(
        "--backend",
        choices=list(BACKEND_CHOICES),
        default="files",
        help="Destination memory backend (default: files; 'graph' is an alias for 'neo4j').",
    )
    qq.add_argument(
        "--scope",
        default="qq",
        help="Destination scope name (default: qq).",
    )
    qq.add_argument(
        "--visibility",
        choices=["public", "private"],
        default="private",
        help=(
            "Destination scope visibility (default: private). QQ data is "
            "personal — keep it private so it never leaks to a public recall."
        ),
    )
    qq.add_argument(
        "--json",
        action="store_true",
        help="Emit the per-source migration report as JSON to stdout.",
    )
    qq.set_defaults(func=cmd_migrate_qq)

    store = targets.add_parser(
        "store",
        help="Upgrade an existing store's on-disk format (Record -> Envelope JSONL).",
    )
    store.add_argument(
        "--data-dir",
        default=None,
        metavar="PATH",
        help="Store directory to migrate (default: EIDETIC_DATA_DIR, else ~/.eidetic/memory).",
    )
    store.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything.",
    )
    store.add_argument(
        "--json",
        action="store_true",
        help="Emit the migration stats as JSON to stdout.",
    )
    store.set_defaults(func=cmd_migrate_store)

    # `migrate` with no target prints help instead of crashing.
    p.set_defaults(func=_require_target)


def _require_target(args: argparse.Namespace) -> int:
    from eidetic.cli._errors import EXIT_USER_ERROR, CliError

    raise CliError(
        code=EXIT_USER_ERROR,
        message="missing migration target",
        remediation="specify a target, e.g. 'eidetic-cli migrate qq'",
    )
