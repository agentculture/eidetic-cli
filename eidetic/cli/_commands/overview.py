"""``eidetic-cli overview`` — read-only descriptive snapshot of the agent.

Describes the agent to an agent reader: identity (from culture.yaml), the verb
surface, and the sibling-pattern artifacts this template carries. The shared
section/render helpers here are reused by the ``cli`` noun's ``overview`` (see
:mod:`eidetic.cli._commands.cli`).

Descriptive verbs never hard-fail on a missing target path — an optional
positional ``target`` is accepted and ignored (overview describes this agent,
not an external target), so ``overview <bogus-path>`` still exits 0.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import threading
import time
from typing import Any

from eidetic.cli._commands.whoami import report
from eidetic.cli._errors import CliError
from eidetic.cli._output import emit_result
from eidetic.memory.backend import get_backend
from eidetic.memory.stats import compute_stats

# User-facing store labels. "graph" maps to the neo4j backend module — the
# operator asked for "files | mongo | graph", so the CLI speaks "graph" while the
# registry key stays "neo4j".
_STORE_LABELS: tuple[str, ...] = ("files", "mongo", "graph")
_LABEL_TO_BACKEND = {"files": "files", "mongo": "mongo", "graph": "neo4j"}

# Default server-selection / connection timeout for the store probe. Short by
# design: `overview` shows store status on every call, so a down mongo/neo4j must
# fail fast rather than block on the backends' normal 5s timeout. Override with
# EIDETIC_STORE_PROBE_TIMEOUT_MS (the test suite sets it very low).
_DEFAULT_PROBE_TIMEOUT_MS = 1000


def _probe_timeout_ms() -> int:
    try:
        return int(os.environ.get("EIDETIC_STORE_PROBE_TIMEOUT_MS", str(_DEFAULT_PROBE_TIMEOUT_MS)))
    except ValueError:
        return _DEFAULT_PROBE_TIMEOUT_MS


_ARTIFACTS = [
    "culture.yaml + CLAUDE.md — mesh identity (suffix + backend)",
    ".claude/skills/ — the canonical guildmaster skill kit (cite-don't-import)",
    "docs/skill-sources.md — skill provenance ledger",
    "pyproject.toml + .github/workflows/ — buildable, deployable package baseline",
]

_VERBS = [
    "whoami — identity probe (nick, version, backend, model)",
    "learn — structured self-teaching prompt",
    "explain <path> — markdown docs for a topic",
    "overview — this descriptive snapshot",
    "doctor — check the agent-identity invariants",
    "remember — ingest memory records (JSON or NDJSON)",
    "recall — search the memory store",
    "sweep — apply lifecycle transitions (shadow/archive) across the store",
    "migrate qq — import legacy QQ memory (files/mongo/neo4j)",
]


def agent_sections() -> list[dict[str, object]]:
    """Sections describing the agent (used by the global verb)."""
    ident = report()
    return [
        {
            "title": "Identity",
            "items": [
                f"nick: {ident['nick']}",
                f"version: {ident['version']}",
                f"backend: {ident['backend']}",
                f"model: {ident['model']}",
            ],
        },
        {"title": "Verbs", "items": list(_VERBS)},
        {"title": "Sibling-pattern artifacts", "items": list(_ARTIFACTS)},
    ]


def cli_sections() -> list[dict[str, object]]:
    """Sections describing the CLI surface itself (used by `cli overview`)."""
    return [
        {
            "title": "Verbs",
            "items": list(_VERBS) + ["cli overview — describe the CLI surface (this command)"],
        },
        {
            "title": "Conventions",
            "items": [
                "every command supports --json",
                "results to stdout, errors/diagnostics to stderr (never mixed)",
                "exit codes: 0 success, 1 user error, 2 environment error, 3+ reserved",
            ],
        },
    ]


def render_text(subject: str, sections: list[dict[str, object]]) -> str:
    lines = [f"# {subject}", ""]
    for section in sections:
        lines.append(f"## {section['title']}")
        for item in section["items"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip()


def emit_overview(subject: str, sections: list[dict[str, object]], *, json_mode: bool) -> None:
    if json_mode:
        emit_result({"subject": subject, "sections": sections}, json_mode=True)
    else:
        emit_result(render_text(subject, sections), json_mode=False)


def _first_line(text: str, *, limit: int = 200) -> str:
    """Collapse a (possibly multi-line, verbose) error to one trimmed line."""
    line = text.strip().splitlines()[0] if text.strip() else "unknown error"
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _probe_backend(label: str, scope_filter: str | None) -> dict[str, Any]:
    """Probe one backend and return a live-with-counts or unavailable payload.

    Never raises. A backend that is down — ``get_backend`` import failure, a
    wrapped :class:`CliError`, or an unwrapped driver exception (mongo's lazy
    ``find()`` raises ``ServerSelectionTimeoutError`` directly) — degrades to an
    ``unavailable`` line with the reason. This is the whole reason the always-on
    Store section is safe against a partially-down store: one dead backend never
    blocks the others and never breaks overview's exit-0 contract.
    """
    backend = None
    try:
        backend = get_backend(_LABEL_TO_BACKEND[label], timeout_ms=_probe_timeout_ms())
        records = backend.all()
        # Filtering + aggregation live INSIDE the try so a malformed record
        # (e.g. a None scope from a corrupt store) degrades to 'unavailable'
        # rather than raising past the exit-0 contract.
        if scope_filter is not None:
            records = [r for r in records if r.scope.name == scope_filter]
        stats = compute_stats(records)
    except CliError as exc:
        return {"backend": label, "status": "unavailable", "reason": _first_line(exc.message)}
    except Exception as exc:  # noqa: BLE001 — driver errors are intentionally swallowed
        return {"backend": label, "status": "unavailable", "reason": _first_line(str(exc))}
    finally:
        close = getattr(backend, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):  # best-effort cleanup
                close()

    return {"backend": label, "status": "live", **stats}


def _probe_into(label: str, scope_filter: str | None, box: dict[str, Any]) -> None:
    """Run a probe and stash its result in *box* (target of a daemon thread)."""
    box["result"] = _probe_backend(label, scope_filter)


def store_payload(backend: str | None, scope_filter: str | None) -> dict[str, Any]:
    """Build the structured Store payload: per-backend probes (+ scope filter).

    Probes run **concurrently**, each under a hard wall-clock **deadline**, so
    total latency is ~max(probe), not the sum, and a reachable-but-slow backend
    (whose ``all()`` enumeration outruns the connect timeout) is force-bounded to
    ``unavailable`` instead of stalling overview. The connect timeout (``timeout_ms``)
    still gives a down backend its fast, descriptive error first; the deadline is
    the outer guarantee for the slow-but-reachable case. Threads are daemon so a
    wedged probe can never block process exit.
    """
    labels = [backend] if backend else list(_STORE_LABELS)
    # 2x the connect timeout: a down backend reports its real reason at ~timeout_ms
    # (well inside this), while a slow enumeration is capped here.
    deadline_s = max(0.05, (_probe_timeout_ms() * 2) / 1000.0)

    boxes: list[dict[str, Any]] = [{} for _ in labels]
    threads: list[threading.Thread] = []
    for label, box in zip(labels, boxes):
        t = threading.Thread(target=_probe_into, args=(label, scope_filter, box), daemon=True)
        t.start()
        threads.append(t)

    deadline = time.monotonic() + deadline_s
    backends: list[dict[str, Any]] = []
    for label, box, t in zip(labels, boxes, threads):
        t.join(max(0.0, deadline - time.monotonic()))
        if t.is_alive() or "result" not in box:
            backends.append(
                {
                    "backend": label,
                    "status": "unavailable",
                    "reason": f"probe exceeded {int(deadline_s * 1000)}ms",
                }
            )
        else:
            backends.append(box["result"])

    return {"scope_filter": scope_filter, "backends": backends}


def render_store_text(payload: dict[str, Any]) -> str:
    """Render the Store payload as a markdown section appended to overview text."""
    lines = ["## Store"]
    scope_filter = payload.get("scope_filter")
    if scope_filter:
        lines.append(f"(scope filter: {scope_filter})")
    for b in payload["backends"]:
        if b["status"] != "live":
            lines.append(f"- {b['backend']} — unavailable: {b['reason']}")
            continue
        lines.append(
            f"- {b['backend']} — live: {b['total']} record(s), "
            f"{len(b['scopes'])} scope(s), {b['connections']} link-connection(s)"
        )
        for s in b["scopes"]:
            detail = ", ".join(f"{k} {s[k]}" for k in ("active", "shadowed", "archived") if s[k])
            lines.append(f"  - {s['name']}/{s['visibility']}: {s['total']} ({detail})")
    return "\n".join(lines)


def cmd_overview(args: argparse.Namespace) -> int:
    # `target` is accepted for rubric compatibility (descriptive verbs must not
    # hard-fail on a missing path) but overview describes this agent itself.
    json_mode = bool(getattr(args, "json", False))
    sections = agent_sections()

    # The Store section is ALWAYS shown: bare `overview` covers all stores'
    # statistics + status. --backend narrows to one store, --scope to one scope.
    # A down backend degrades to 'unavailable' (fast-timeout probe), never a crash,
    # so overview still exits 0. (`cli overview` uses cli_sections() and is
    # unaffected — it describes the CLI surface, not the store.)
    backend = getattr(args, "backend", None)
    scope_filter = getattr(args, "scope", None)
    store = store_payload(backend, scope_filter)
    if json_mode:
        emit_result(
            {"subject": "eidetic-cli", "sections": sections, "store": store},
            json_mode=True,
        )
    else:
        text = render_text("eidetic-cli", sections) + "\n\n" + render_store_text(store)
        emit_result(text, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "overview",
        help="Read-only snapshot of the agent (identity, verbs, artifacts) + live store stats.",
    )
    p.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this agent itself. Accepted so a "
        "stray path argument never hard-fails.",
    )
    p.add_argument(
        "--backend",
        choices=list(_STORE_LABELS),
        help="Narrow the Store section to one backend ('graph' is the neo4j store). "
        "Default: all three (files/mongo/graph).",
    )
    p.add_argument(
        "--scope",
        metavar="NAME",
        help="Narrow Store counts to records whose scope name matches NAME. "
        "An unknown scope yields a zero-count section.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_overview)
