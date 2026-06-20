"""Files-based memory backend for eidetic-cli."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.backend import Backend
from eidetic.memory.embed import EmbedClient
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope, can_serve
from eidetic.memory.scoring import rank


class FilesBackend:
    """Persist records as JSONL files, one file per scope."""

    def __init__(self, base_dir: str | None = None) -> None:
        if base_dir is None:
            base_dir = os.environ.get("EIDETIC_DATA_DIR") or str(
                Path.home() / ".eidetic" / "memory"
            )
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._embed = EmbedClient()

    # -- Backend protocol -----------------------------------------------

    def upsert(self, record: Record) -> None:
        """Insert or update *record* idempotently (by id, also dedup by hash)."""
        path = self._scope_file(record.scope)
        records = self._load(path)

        # Replace by id
        replaced = False
        for i, r in enumerate(records):
            if r.id == record.id:
                records[i] = record
                replaced = True
                break

        if not replaced:
            # Dedup by hash: remove any record with the same hash
            records = [r for r in records if r.hash != record.hash]
            records.append(record)

        self._save(path, records)

    def search(
        self,
        query: str,
        top_k: int,
        scope: Scope,
        filters: dict | None,
        mode: str = "hybrid",
        *,
        alpha: float = 0.5,
        case_sensitive: bool = False,
    ) -> list[Record]:
        candidates: list[Record] = []
        for path in self._base.glob("*.jsonl"):
            for record in self._load(path):
                if not can_serve(scope, record.scope):
                    continue
                if filters and not self._matches_filters(record, filters):
                    continue
                candidates.append(record)

        return rank(
            mode,
            query,
            candidates,
            self._embed,
            top_k,
            alpha=alpha,
            case_sensitive=case_sensitive,
        )

    def all(self) -> list[Record]:
        """Enumerate every stored record across all scope files (no filtering)."""
        records: list[Record] = []
        for path in sorted(self._base.glob("*.jsonl")):
            records.extend(self._load(path))
        return records

    # -- internal helpers ------------------------------------------------

    def _scope_file(self, scope: Scope) -> Path:
        safe = scope.name.replace("/", "_").replace("\\", "_")
        return self._base / f"{safe}__{scope.visibility}.jsonl"

    def _load(self, path: Path) -> list[Record]:
        if not path.exists():
            return []
        records: list[Record] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(Record.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError) as exc:
                    raise CliError(
                        code=EXIT_ENV_ERROR,
                        message=f"corrupt line in {path.name}: {exc}",
                        remediation=f"remove or repair the corrupt line in {path}",
                    ) from exc
        return records

    def _save(self, path: Path, records: list[Record]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r.to_dict()) + "\n")

    @staticmethod
    def _matches_filters(record: Record, filters: dict[str, Any]) -> bool:
        for key, value in filters.items():
            if record.metadata.get(key) != value:
                return False
        return True


def build() -> Backend:
    """Factory: return a default FilesBackend instance."""
    return FilesBackend()
