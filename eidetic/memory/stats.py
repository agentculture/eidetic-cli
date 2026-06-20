"""Pure aggregation of memory records into store statistics.

This module is **pure**: it takes an already-enumerated list of
:class:`~eidetic.memory.record.Record` and returns plain dicts of counts. It
performs no I/O, opens no connection, and reads no clock — so it is fully
deterministic and unit-testable without a live backend. The I/O (calling each
backend's ``all()`` and degrading gracefully when one is down) lives in the
``overview`` command handler; this module only does the arithmetic.

The ``connections`` figure is deliberately *not* a graph traversal: it counts
**link-references** (``len(record.links)`` plus one for a present ``supersedes``)
summed across the counted records. The neo4j backend stores ``links`` /
``supersedes`` as node *properties*, not edges, so there are no real edges to
walk — this count reflects exactly the references the records declare.
"""

from __future__ import annotations

from typing import Any

from eidetic.memory.record import Record

# Lifecycle states a record may carry. Anything else is bucketed as "active"
# (matching Record's default and the lenient loaders in the backends).
_LIFECYCLES = ("active", "shadowed", "archived")


def _empty_lifecycle_bucket() -> dict[str, int]:
    return {"total": 0, "active": 0, "shadowed": 0, "archived": 0}


def link_references(record: Record) -> int:
    """Count the link-references a single record declares.

    ``len(record.links)`` plus one when ``supersedes`` is set. This is the unit
    summed into the store-wide ``connections`` figure — references, not edges.
    """
    return len(record.links) + (1 if record.supersedes else 0)


def compute_stats(records: list[Record]) -> dict[str, Any]:
    """Aggregate *records* into a structured store-stats payload.

    Returns a dict with::

        {
          "total": <int>,
          "scopes": [
            {"name", "visibility", "total", "active", "shadowed", "archived"},
            ...  # sorted by (name, visibility)
          ],
          "connections": <int>,  # summed link-references (not graph edges)
        }

    An empty *records* list yields ``total=0``, ``scopes=[]``, ``connections=0``
    — a reachable-but-empty backend, distinct from an unreachable one (which the
    caller renders as ``unavailable`` and never reaches this function for).
    """
    scopes: dict[tuple[str, str], dict[str, int]] = {}
    connections = 0

    for record in records:
        key = (record.scope.name, record.scope.visibility)
        bucket = scopes.setdefault(key, _empty_lifecycle_bucket())
        bucket["total"] += 1
        lifecycle = record.lifecycle if record.lifecycle in _LIFECYCLES else "active"
        bucket[lifecycle] += 1
        connections += link_references(record)

    scope_list = [
        {"name": name, "visibility": visibility, **counts}
        for (name, visibility), counts in sorted(scopes.items())
    ]

    return {
        "total": len(records),
        "scopes": scope_list,
        "connections": connections,
    }
