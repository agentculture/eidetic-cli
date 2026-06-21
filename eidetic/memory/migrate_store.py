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

    for path in sorted(base.glob("*.jsonl")):
        stats.files_scanned += 1
        changed = False
        out_lines: list[str] = []

        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CliError(
                    code=EXIT_ENV_ERROR,
                    message=f"corrupt line in {path.name}: {exc}",
                    remediation=f"remove or repair the corrupt line in {path}",
                ) from exc

            if "content" in obj:
                # Already an Envelope — pass through untouched (idempotent).
                stats.already_envelope += 1
                out_lines.append(json.dumps(obj))
                continue

            # A legacy Record line (has "text"): convert via the canonical mapping.
            envelope = record_to_envelope(Record.from_dict(obj))
            out_lines.append(json.dumps(envelope.to_dict()))
            stats.records_converted += 1
            changed = True

        if changed:
            stats.files_rewritten += 1
            if not dry_run:
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
                os.replace(tmp, path)

    return stats
