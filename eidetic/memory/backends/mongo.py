"""MongoDB memory backend for eidetic-cli."""

from __future__ import annotations

import os
from typing import Any

from pymongo import MongoClient

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.backend import Backend
from eidetic.memory.embed import EmbedClient
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope, can_serve
from eidetic.memory.scoring import rank


class MongoBackend:
    """Persist records in a MongoDB collection."""

    def __init__(
        self,
        client: MongoClient | None = None,
        uri: str | None = None,
        db: str | None = None,
    ) -> None:
        self._client: MongoClient | None = client
        self._uri = uri or os.environ.get("EIDETIC_MONGO_URI") or "mongodb://localhost:27018"
        self._db_name = db or os.environ.get("EIDETIC_MONGO_DB") or "eidetic_memory"
        self._embed = EmbedClient()

    # -- lazy client ----------------------------------------------------

    def _ensure_client(self) -> MongoClient:
        if self._client is None:
            try:
                self._client = MongoClient(self._uri, serverSelectionTimeoutMS=5000)
            except Exception as exc:
                raise CliError(
                    code=EXIT_ENV_ERROR,
                    message=f"failed to connect to MongoDB: {exc}",
                    remediation="check EIDETIC_MONGO_URI and ensure MongoDB is running",
                ) from exc
        return self._client

    @property
    def _collection(self):
        return self._ensure_client()[self._db_name]["records"]

    # -- resource cleanup ------------------------------------------------

    def close(self) -> None:
        """Close the MongoDB client connection (no-op if never connected)."""
        if self._client is not None:
            self._client.close()

    # -- Backend protocol ------------------------------------------------

    def upsert(self, record: Record) -> None:
        """Insert or update *record* idempotently (by id)."""
        embedding = self._embed.embed([record.text])[0]
        doc = record.to_dict()
        doc["embedding"] = embedding
        self._collection.replace_one({"_id": record.id}, doc, upsert=True)

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
        # Build find query — push metadata facet filters into MongoDB
        find_query: dict[str, Any] = {}
        if filters:
            for key, value in filters.items():
                find_query[f"metadata.{key}"] = value

        candidates: list[Record] = []
        for doc in self._collection.find(find_query):
            record = Record.from_dict(doc)
            if not can_serve(scope, record.scope):
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
        """Enumerate every stored record (no scope filtering, no ranking)."""
        return [Record.from_dict(doc) for doc in self._collection.find({})]


def build() -> Backend:
    """Factory: return a default MongoBackend instance."""
    return MongoBackend()
