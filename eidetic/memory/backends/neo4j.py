"""Neo4j memory backend for eidetic-cli."""

from __future__ import annotations

import json
import os
from typing import Any

from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.backend import Backend
from eidetic.memory.embed import EmbedClient, cosine
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope, can_serve

_DEFAULT_URI = "bolt://localhost:7687"
_DEFAULT_USER = "neo4j"
_DEFAULT_PASSWORD = "REDACTED"


class Neo4jBackend:
    """Persist records as Neo4j nodes, one node per record."""

    def __init__(
        self,
        driver: Any = None,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        self._driver = driver
        self._uri = uri
        self._user = user
        self._password = password
        self._embed = EmbedClient()

    # -- Backend protocol ------------------------------------------------

    def upsert(self, record: Record) -> None:
        """Insert or update *record* idempotently (by id)."""
        embedding = self._embed.embed([record.text])[0]
        metadata_json = json.dumps(record.metadata)

        query = (
            "MERGE (m:Memory {id: $id}) "
            "SET m.text = $text, m.type = $type, m.hash = $hash, "
            "m.metadata = $metadata, m.scope_name = $scope_name, "
            "m.scope_visibility = $scope_visibility, m.embedding = $embedding "
            "RETURN m.id"
        )
        params = {
            "id": record.id,
            "text": record.text,
            "type": record.type,
            "hash": record.hash,
            "metadata": metadata_json,
            "scope_name": record.scope.name,
            "scope_visibility": record.scope.visibility,
            "embedding": embedding,
        }
        self._run(query, params)

    def search(
        self,
        query: str,
        top_k: int,
        scope: Scope,
        filters: dict | None,
    ) -> list[Record]:
        query_emb = self._embed.embed([query])[0]

        rows = self._run("MATCH (m:Memory) RETURN m", {})

        candidates: list[Record] = []
        for row in rows:
            node = row["m"]
            record = self._node_to_record(node)
            if not can_serve(scope, record.scope):
                continue
            if filters and not self._matches_filters(record, filters):
                continue
            score = cosine(query_emb, self._embed.embed([record.text])[0])
            record.score = score
            candidates.append(record)

        candidates.sort(key=lambda r: r.score if r.score is not None else 0.0, reverse=True)
        return candidates[:top_k]

    # -- internal helpers ------------------------------------------------

    def _get_driver(self) -> Any:
        if self._driver is not None:
            return self._driver
        try:
            import neo4j
        except ImportError as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message="neo4j driver not installed",
                remediation="install the neo4j package (e.g. pip install neo4j)",
            ) from exc
        uri = self._uri or os.environ.get("NEO4J_URI", _DEFAULT_URI)
        user = self._user or os.environ.get("NEO4J_USER", _DEFAULT_USER)
        password = self._password or os.environ.get("NEO4J_PASSWORD", _DEFAULT_PASSWORD)
        try:
            self._driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
        except Exception as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"failed to connect to Neo4j at {uri}: {exc}",
                remediation="check NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD environment variables",
            ) from exc
        return self._driver

    def _run(self, query: str, params: dict) -> list[Any]:
        driver = self._get_driver()
        try:
            with driver.session() as session:
                result = session.run(query, params)
                return list(result)
        except Exception as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"Neo4j query failed: {exc}",
                remediation="check your Neo4j connection and retry",
            ) from exc

    @staticmethod
    def _node_to_record(node: dict[str, Any]) -> Record:
        metadata = node.get("metadata", "{}")
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        scope = Scope(
            name=node.get("scope_name", "default"),
            visibility=node.get("scope_visibility", "public"),
        )
        return Record(
            id=node["id"],
            text=node.get("text", ""),
            type=node.get("type", "note"),
            hash=node.get("hash", ""),
            metadata=metadata,
            scope=scope,
        )

    @staticmethod
    def _matches_filters(record: Record, filters: dict[str, Any]) -> bool:
        for key, value in filters.items():
            if record.metadata.get(key) != value:
                return False
        return True


def build() -> Backend:
    """Factory: return a default Neo4jBackend instance."""
    return Neo4jBackend()
