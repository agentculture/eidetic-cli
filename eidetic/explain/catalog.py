"""Markdown catalog for ``eidetic-cli explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("eidetic-cli",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# eidetic-cli

A clonable template for AgentCulture mesh agents. It carries an agent-first CLI
(cited from the teken `python-cli` reference), a mesh identity (`culture.yaml` +
`CLAUDE.md`), the canonical guildmaster skill kit under `.claude/skills/`, and a
buildable/deployable package baseline. Clone it, rename the package, edit
`culture.yaml`, and you have a new agent.

## Verbs

- `eidetic-cli whoami` — identity probe from `culture.yaml`.
- `eidetic-cli learn` — structured self-teaching prompt.
- `eidetic-cli explain <path>` — markdown docs for any noun/verb.
- `eidetic-cli overview` — descriptive snapshot of the agent.
- `eidetic-cli doctor` — check the agent-identity invariants.
- `eidetic-cli cli overview` — describe the CLI surface.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `eidetic-cli explain whoami`
- `eidetic-cli explain doctor`
"""

_WHOAMI = """\
# eidetic-cli whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    eidetic-cli whoami
    eidetic-cli whoami --json
"""

_LEARN = """\
# eidetic-cli learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    eidetic-cli learn
    eidetic-cli learn --json
"""

_EXPLAIN = """\
# eidetic-cli explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    eidetic-cli explain eidetic-cli
    eidetic-cli explain whoami
    eidetic-cli explain --json <path>
"""

_OVERVIEW = """\
# eidetic-cli overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    eidetic-cli overview
    eidetic-cli overview --json
"""

_DOCTOR = """\
# eidetic-cli doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`claude` → `CLAUDE.md`), plus a
skills-present check. Exits 1 when unhealthy.

## Usage

    eidetic-cli doctor
    eidetic-cli doctor --json
"""

_CLI = """\
# eidetic-cli cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    eidetic-cli cli overview
    eidetic-cli cli overview --json
"""

_REMEMBER = """\
# eidetic-cli remember

Ingest one or more memory records. Accepts a single JSON object as a positional
argument, or NDJSON from stdin for bulk ingest. Each record is upserted
(idempotent by id) into the configured memory backend.

## Flags

- `--backend` — memory backend to use: `files`, `neo4j`, or `mongo` (default:
  `files`).
- `--scope` — scope name for the record(s) (default: `default`).
- `--visibility` — record visibility: `public` or `private` (default: `public`).
- `--json` — emit structured JSON output.

## Exit codes

- `0` success
- `1` user-input error (invalid JSON, missing required keys)

## Behavior

Each record must contain `id` and `text` keys. When a positional JSON argument
is given, it is parsed as a single record. When omitted, stdin is read as
NDJSON (one JSON object per line). Upsert is idempotent: re-submitting a record
with the same `id` overwrites the previous value.
"""

_RECALL = """\
# eidetic-cli recall

Search the memory store and return matching records. Returns top-k hits, each
with text, full metadata, and a relevance score. Scope-aware: queries respect
the configured scope and visibility, with no private-to-public leak.

## Flags

- `--top-k` — maximum number of results to return (default: 5).
- `--filter KEY=VALUE` — metadata facet filter; repeatable.
- `--backend` — storage backend to query: `files`, `neo4j`, or `mongo` (default:
  `files`).
- `--scope` — query scope name (default: `default`).
- `--visibility` — query scope visibility: `public` or `private` (default:
  `public`).
- `--json` — emit results as a JSON list to stdout.

## Exit codes

- `0` success
- `1` user-input error (malformed filter, missing query)

## Behavior

Returns up to `--top-k` hits sorted by relevance score. Each hit includes the
record text, all metadata fields, and a numeric score. Scope is enforced at
query time: a query with `--visibility public` never returns records marked
private, preventing accidental private-to-public leaks.
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("eidetic-cli",): _ROOT,
    # The console script / package is named `eidetic` (see [project.scripts]),
    # so `explain eidetic` must also resolve — the agent-first rubric probes the
    # tool by its self-name. Alias it to the same root entry as `eidetic-cli`.
    ("eidetic",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
    ("remember",): _REMEMBER,
    ("recall",): _RECALL,
}
