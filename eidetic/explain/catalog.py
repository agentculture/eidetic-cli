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

Each record must contain `id`, `text`, and `type` keys. When a positional JSON
argument is given, it is parsed as a single record. When omitted, stdin is read
as NDJSON (one JSON object per line). Upsert is idempotent: re-submitting a
record with the same `id` overwrites the previous value.
"""

_RECALL = """\
# eidetic-cli recall

Search the memory store and return matching records. Returns top-k hits, each
with text, full metadata, and a relevance score. Scope-aware: queries respect
the configured scope and visibility, with no private-to-public leak.

## Search modes (`--mode`, default `hybrid`)

- `exact` — case-insensitive verbatim substring match (`--case-sensitive`
  tightens it). Pure lexical; works with the embed server offline.
- `approximate` — vector cosine (semantic) similarity. Needs the embed server.
- `keyword` — BM25 lexical scoring; only records sharing a query term match.
  Works offline.
- `hybrid` — weighted alpha blend of min-max-normalised `approximate` +
  `keyword`: `score = alpha*approximate + (1-alpha)*keyword`. When the embed
  server is unreachable, `alpha` collapses to 0 (keyword-only) so hybrid never
  fuses meaningless offline-fallback cosine.

## Flags

- `--mode` — search mode: `exact`, `approximate`, `keyword`, `hybrid` (default:
  `hybrid`).
- `--alpha` — hybrid blend weight in `[0,1]` (default: `0.5`); only used by
  `--mode hybrid`.
- `--case-sensitive` — only used by `--mode exact`; require matching case.
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
- `1` user-input error (malformed filter, missing query, bad `--mode`/`--alpha`)

## Behavior

Returns up to `--top-k` hits sorted by relevance score. Each hit includes the
record text, all metadata fields, and a numeric score. Scope is enforced at
query time across every mode: a query with `--visibility public` never returns
records marked private, preventing accidental private-to-public leaks.
"""


_SWEEP = """\
# eidetic-cli sweep

Apply lifecycle transitions across the whole memory store. Loads every record,
runs the pure lifecycle engine, and (unless `--dry-run`) upserts the records
whose `lifecycle` changed. It never deletes — it only ever flips `lifecycle` to
`shadowed` or `archived` and persists the record in place.

## Rules

- **Shadowing (authoritative, within-scope only).** If record A declares
  `supersedes == B.id` and A and B share the SAME scope (name AND visibility),
  B is marked `shadowed`. A `supersedes` link that crosses scopes never shadows,
  preserving the public/private no-leak invariant.
- **Archival (age OR signal).** A record is marked `archived` when it is older
  than ~1 year (`created`; an unknown date is age-neutral) OR its freshness
  signal falls below the archive threshold.
- **Protected exemption.** A record whose `metadata.protected` is truthy is
  never shadowed and never archived.
- **Suggestions.** Likely same-scope conflicts (high text overlap) are RETURNED
  for human confirmation only — never auto-applied.

## Flags

- `--backend` — memory backend to sweep: `files`, `neo4j`, or `mongo` (default:
  `files`).
- `--dry-run` — report transitions without writing any change.
- `--json` — emit structured JSON output.
"""


_MIGRATE = """\
# eidetic-cli migrate

One-shot import of legacy memory sources into the eidetic store. Currently
exposes a single target, `migrate qq`, which reads the three legacy "QQ" memory
layers and upserts every mapped record idempotently.

## migrate qq

Reads the QQ markdown files (`core.md` / `notes.md`, one record per `##`
section), the QQ MongoDB `claude_notes` collection, and the QQ Neo4j entities
tagged `knowledge_context="claude"`. Each source is guarded: a down/absent
Mongo or Neo4j is **skipped with a warning** (to stderr) and the run completes
with the remaining sources.

Stable per-source ids make re-runs idempotent (upsert by id, never duplicate):
`qq-file:<path>#<section-slug>`, `qq-mongo:<note_id>`, `qq-neo4j:<entity_id>`.
Provenance + a date signature ride along in `metadata` and `created`
(file mtime / Mongo `last_accessed` / Neo4j `last_seen`, falling back to the
decay-neutral date-unknown sentinel).

### No-leak default

QQ files/`core.md` hold PERSONAL data, so migration writes into a **private**
scope by default (`--scope qq --visibility private`). Migrated personal
knowledge therefore never surfaces in a public recall. Both flags are
configurable.

## Flags

- `--file PATH` — QQ markdown source to read (repeatable; defaults to the known
  `core.md`/`notes.md` paths).
- `--backend` — destination backend: `files`, `neo4j`, `mongo` (default:
  `files`).
- `--scope` — destination scope name (default: `qq`).
- `--visibility` — destination scope visibility: `public` or `private`
  (default: `private`).
- `--json` — emit the per-source migration report as JSON.

## Exit codes

- `0` success
- `2` environment / setup error (backend unavailable)

## Behavior

Reports counts of `shadowed` and `archived` transitions plus any advisory
conflict suggestions. With `--dry-run`, the same report is produced but nothing
is persisted. No code path deletes a record.
- `1` user-input error (missing migration target)

## Usage

    eidetic-cli migrate qq
    eidetic-cli migrate qq --file core.md --file notes.md --json
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
    ("sweep",): _SWEEP,
    # t6: one-shot QQ memory importer
    ("migrate",): _MIGRATE,
    ("migrate", "qq"): _MIGRATE,
}
