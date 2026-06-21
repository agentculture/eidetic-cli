"""One-shot, idempotent migration of an eidetic store from the legacy
Record-format JSONL to data-refinery's Envelope-format JSONL.

eidetic used to persist each memory as a ``Record`` dict (a top-level ``text``
plus the memory fields). Storage now belongs to the sibling data-refinery, whose
files backend persists opaque ``Envelope`` dicts (``content`` + an opaque
``metadata`` bag, with the memory fields riding inside ``metadata``). An existing
on-disk store therefore needs a one-time, in-place remap before the new Envelope
reader can interpret it (an unmigrated Record line would read as empty content).

The migration is **idempotent** — a line already in Envelope format passes
through untouched, so re-running converts nothing — and **atomic per file**
(write a sibling temp file, then :func:`os.replace`). It reuses the canonical
:func:`eidetic.memory.backend.record_to_envelope` mapping so it can never drift
from how the live store reads and writes. See issue #13.

Atomicity is *per file*, not per store: files are processed sequentially, so an
interrupted run leaves the store briefly in a mixed Record/Envelope state. That
is safe to resume — the idempotency check completes the remaining files on a
re-run — but **concurrent access during a migration is unsupported**; run it
while the store is otherwise idle.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.backend import record_to_envelope
from eidetic.memory.record import Record


@dataclass
class MigrationStats:
    """Counts describing what a :func:`migrate_store` run did (or, in a dry run,
    would do)."""

    files_scanned: int = 0
    records_converted: int = 0
    already_envelope: int = 0
    files_rewritten: int = 0


def _default_data_dir() -> str:
    """Resolve the store directory the same way the files backend does."""
    return os.environ.get("EIDETIC_DATA_DIR") or str(Path.home() / ".eidetic" / "memory")


def _convert_line(raw: str, path: Path) -> tuple[str, bool]:
    """Parse and classify one raw JSONL line from *path*.

    Returns ``(serialised_line, converted)`` where *converted* is ``True`` when
    a legacy Record was remapped to Envelope format (i.e. the file has changed).

    Raises :class:`CliError` (``EXIT_ENV_ERROR``) when the line is unparseable
    JSON *or* valid JSON that cannot be coerced into a :class:`Record` (missing
    required fields, wrong types, etc.).  Blank lines must be filtered by the
    caller before invoking this function.
    """
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"corrupt line in {path.name}: {exc}",
            remediation=f"remove or repair the corrupt line in {path}",
        ) from exc

    if "content" in obj:
        # Already an Envelope — pass through untouched (idempotent).
        return json.dumps(obj), False

    # A legacy Record line (has "text"): convert via the canonical mapping.
    try:
        envelope = record_to_envelope(Record.from_dict(obj))
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"corrupt line in {path.name}: {exc}",
            remediation=f"remove or repair the corrupt line in {path}",
        ) from exc

    return json.dumps(envelope.to_dict()), True


def _ensure_within(base: str, candidate: Path) -> Path:
    """Return *candidate*'s canonical path, asserting it stays inside *base*.

    The store directory is operator-supplied (``EIDETIC_DATA_DIR`` /
    ``--data-dir``), so before writing we canonicalise the target (resolving any
    symlinks) and confirm it does not escape the resolved store directory. This
    makes the trust boundary explicit: a crafted data-dir can never redirect a
    migration write outside the store. *base* must already be canonical (an
    :func:`os.path.realpath`).
    """
    resolved = os.path.realpath(candidate)
    if os.path.commonpath((base, resolved)) != base:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"refusing to write outside the store directory: {candidate}",
            remediation=f"ensure {base} is a normal directory holding only its own JSONL files",
        )
    return Path(resolved)


def _migrate_file(path: Path, base: str, *, dry_run: bool) -> tuple[int, int, bool]:
    """Migrate a single JSONL file in place.

    *base* is the canonical (``realpath``) store directory; the rewritten temp
    file is validated to live inside it before any bytes are written.

    Returns ``(records_converted, already_envelope, file_rewritten)``.  When
    *dry_run* is ``True`` the return value reflects what *would* happen but no
    bytes are written to disk.
    """
    out_lines: list[str] = []
    records_converted = 0
    already_envelope = 0
    changed = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        serialised, converted = _convert_line(line, path)
        out_lines.append(serialised)
        if converted:
            records_converted += 1
            changed = True
        else:
            already_envelope += 1

    file_rewritten = changed
    if changed and not dry_run:
        tmp = _ensure_within(base, path.with_suffix(path.suffix + ".tmp"))
        tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    return records_converted, already_envelope, file_rewritten


def migrate_store(data_dir: str | None = None, *, dry_run: bool = False) -> MigrationStats:
    """Remap every Record-format line under *data_dir* to Envelope format in place.

    Resolves *data_dir* from the argument, else ``EIDETIC_DATA_DIR``, else
    ``~/.eidetic/memory``. A non-existent directory yields zeroed stats. Blank
    lines are skipped; a corrupt (unparseable) line raises :class:`CliError`
    naming the file, mirroring how the files backend reports corruption. With
    ``dry_run=True`` nothing is written, but the returned :class:`MigrationStats`
    still reports what *would* change.
    """
    base = Path(data_dir or _default_data_dir())
    stats = MigrationStats()
    if not base.is_dir():
        return stats

    # Canonicalise once; every per-file write is then validated to stay inside it.
    base_canonical = os.path.realpath(base)
    for path in sorted(base.glob("*.jsonl")):
        stats.files_scanned += 1
        converted, already, rewritten = _migrate_file(path, base_canonical, dry_run=dry_run)
        stats.records_converted += converted
        stats.already_envelope += already
        if rewritten:
            stats.files_rewritten += 1

    return stats
