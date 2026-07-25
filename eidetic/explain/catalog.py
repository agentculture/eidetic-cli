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

## Store stats (always shown)

Every `overview` also reports a live Store section covering **all** stores: total
records, a per-backend breakdown (files / mongo / graph), per-scope counts (name +
visibility + lifecycle), each backend's live/unavailable **status**, and
`link-connections` — the count of link/`supersedes` *references* summed across
records, **not** graph edges (neo4j stores these as node properties). Narrow with
`--backend {files,mongo,neo4j,graph}` (one store; `graph` and `neo4j` both select
the neo4j store) or `--scope NAME` (one scope). A backend that is down degrades to
an `unavailable` line via a fast status probe — `overview` still exits 0.
(`cli overview` describes the CLI surface and does not touch the store.)

### Contributors per scope

Each per-scope entry in the Store section includes a `contributors` list — the
sorted union of all distinct `added_by` values and `metadata.author` values found
in that scope's records. `added_by` is auto-stamped by `remember` from the
ingesting agent's mesh nick (see `eidetic-cli explain remember`). This lets you
see at a glance which agents or authors have contributed to each scope.

## Usage

    eidetic-cli overview
    eidetic-cli overview --json
    eidetic-cli overview --backend mongo
    eidetic-cli overview --scope qq --json
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

- `--backend` — memory backend to use: `files`, `mongo`, `neo4j`, or `graph`
  (`graph` is an alias for `neo4j`; default: `files`).
- `--scope` — scope name for the record(s) (default: `default`).
- `--visibility` — record visibility: `public` or `private` (default: `public`).
- `--added-by` — override the agent identity stamped on ingested records.
  Defaults to the agent's mesh nick (resolved from `culture.yaml`); falls back
  to `None` when no `culture.yaml` is present.
- `--json` — emit structured JSON output.

## Exit codes

- `0` success
- `1` user-input error (invalid JSON, missing required keys)

## Behavior

Each record must contain `id`, `text`, and `type` keys. When a positional JSON
argument is given, it is parsed as a single record. When omitted, stdin is read
as NDJSON (one JSON object per line). Upsert is idempotent: re-submitting a
record with the same `id` overwrites the previous value.

### added_by stamping

`remember` auto-stamps `added_by` on every ingested record unless the field is
already present in the record JSON. Resolution order: (1) the `--added-by` flag
value, (2) the agent's mesh nick from `culture.yaml`, (3) `None`. An explicit
value in the record JSON is always preserved verbatim — `--added-by` does not
overwrite it. The field is returned by `recall` and is used by `overview` to
compute the contributor list for each scope (union of `added_by` and
`metadata.author`).
"""

_RECALL = """\
# eidetic-cli recall

Search the memory store and return a composite bundle: the search hits
themselves, plus a bounded neighbourhood walk of `links`/`supersedes` out from
those hits. **`recall`'s default output is the bundle object, not a bare
list** — this is a breaking shape change from pre-0.13.0 recall, which emitted
a flat JSON list of hits.

## Bundle shape (`--json`)

    {
      "query": "<the query string>",
      "mode": "hybrid",
      "truncated": false,
      "items": [
        {"...every record field (id, text, metadata, score, signal, ...)...",
         "tier": "primary", "depth": 0},
        {"...": "...", "tier": "traversal", "depth": 1}
      ]
    }

Every item is the **full record shape** — id, verbatim `text`, complete
`metadata`, `score`, `signal` — plus two bundle-only fields:

- `tier` — `primary` for a search hit (`depth` always `0`), or `traversal` for
  a record discovered by walking `links`/`supersedes` outward from the primary
  hits (`depth` = hop distance, `1` for a direct neighbour, `2` for a
  neighbour's neighbour, ...). A consumer attributes every item to its tier
  without heuristics.
- `depth` — hop distance from the nearest primary hit; `0` for every primary
  item.

`--depth 0` skips the traversal walk entirely and reproduces the old flat,
primary-only shape (still wrapped in the bundle envelope — `items` then holds
only `tier: "primary"` entries).

Text mode (no `--json`) renders each item under a `[<tier> depth=<n>] id: ...`
header, keeping the two tiers visually distinguishable.

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

Search modes select the **primary** tier only. The traversal tier is not
ranked by any of these — it is a graph walk, not a search.

## Flags

- `--mode` — search mode: `exact`, `approximate`, `keyword`, `hybrid` (default:
  `hybrid`).
- `--alpha` — hybrid blend weight in `[0,1]` (default: `0.5`); only used by
  `--mode hybrid`.
- `--case-sensitive` — only used by `--mode exact`; require matching case.
- `--top-k` — maximum number of primary (search-hit) results to return
  (default: `5`). Bounds the primary tier only; the traversal tier is bounded
  separately by `--depth`/`--max-nodes`.
- `--filter KEY=VALUE` — metadata facet filter on the primary search only;
  repeatable.
- `--source SOURCE` — metadata.source facet that constrains **both** tiers
  (unlike `--filter`, which reaches the primary search alone): a traversal
  discovery whose `metadata.source` doesn't match `--source` is a dead end and
  is not walked past either. Conflicts with a different `--filter source=...`
  value raise a user error rather than silently picking one.
- `--depth N` — traversal hop bound from the primary hits (default: `1`). `0`
  disables the walk (flat, primary-only bundle); `truncated` is *not* set by
  `--depth 0` since opting out is not a cut of a requested walk.
- `--max-nodes N` — maximum number of traversal-*discovered* records (default:
  `20`); primary hits never count against this budget.
- `--backend` — storage backend to query: `files`, `mongo`, `neo4j`, or `graph`
  (`graph` is an alias for `neo4j`; default: `files`).
- `--scope` — query scope name (default: `default`).
- `--visibility` — query scope visibility: `public` or `private` (default:
  `public`).
- `--include-shadowed` / `--include-archived` — include lifecycle-excluded
  records (both tiers; excluded by default).
- `--json` — emit the bundle object as JSON to stdout.

## Truncation

Hitting `--depth` or `--max-nodes` before the walk has exhausted everything
genuinely reachable sets `"truncated": true` on the bundle — the walk never
cuts silently. `truncated` is `false` whenever the walk ran to completion
(including the `--depth 0` opt-out case).

## Per-hop no-leak guarantee

Every traversal hop re-applies the same admission policy the primary tier
enforces — scope visibility (`can_serve`), lifecycle filtering, and the
`--source` facet — to each *discovered* record, not only to the seeds. A
private record reachable via `links` from a public hit never enters a public
bundle at any depth, and a rejected record is a dead end: its own
`links`/`supersedes` are never walked further, so a filtered-out neighbour
cannot smuggle a deeper record through as a transit hop.

## Graded reinforcement

Every `recall` call still passively reinforces the records it returns
(`last_recall` + `recall_count`), but the bump is now **graded by tier**:

- A primary hit bumps `recall_count` by the full `1.0` (unchanged from
  pre-bundle recall).
- A traversal discovery at hop depth `d` bumps `recall_count` by
  `DECAY**d` (`DECAY = 0.5` in `eidetic/memory/scoring.py`, alongside the
  freshness-signal constants) — `0.5` at depth 1, `0.25` at depth 2, and so
  on. `recall_count` is `int | float`; a record's count keeps accumulating
  fractional bumps once any traversal has touched it.
- A record excluded by scope or lifecycle filtering is never a bundle item
  and is never bumped.

`last_recall` is stamped on every bumped record, primary or traversal. The
bumps are written from the *emitted* payload's pre-bump snapshot — the JSON
you see in this call always reflects state *before* this call's own bump.

## Exit codes

- `0` success
- `1` user-input error (malformed filter, missing query, bad `--mode`/`--alpha`,
  negative `--depth`/`--max-nodes`, conflicting `--source`/`--filter source=`)

## Before / after (issue #37)

Before: recall was flat top-k search; a consumer wanting a hit's neighbourhood
had to issue N follow-up recalls and still could not reach the graph the
record's own `links`/`supersedes` fields describe. After: one default recall
call returns the hit plus a bounded, tier-labelled, cited neighbourhood in the
same payload.

## v1 degradations (honest, not hidden)

Two aspects of this feature are client-side simulations of what a real store
feature would look like, not the real thing — both are filed upstream as
[data-refinery-cli#20](https://github.com/agentculture/data-refinery-cli/issues/20):

- **"vector lines" is not a real persisted vector index.** Each bundle item's
  `text` is simply the record's own stored text, cited by id — there is no
  sub-record chunking or persisted embedding-index line anywhere in the stack.
- **The traversal is a client-side BFS**, not a graph-store traversal.
  data-refinery's neo4j backend stores `links`/`supersedes` as opaque JSON on
  a node's own properties, with no relationships and no traversal API —
  eidetic walks the fields itself, resolving each hop's ids through
  `StoreBackend.get_many` (`eidetic/memory/backend.py`), one id-lookup batch
  at a time.

`recall` remains fetch-only in every case above: no synthesis, no
summarisation, no LLM/chat call anywhere on this path — the only network call
is the existing embeddings endpoint the search modes already use. Bundle text
fields are byte-equal to stored record text.
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

- `--backend` — memory backend to sweep: `files`, `mongo`, `neo4j`, or `graph`
  (`graph` is an alias for `neo4j`; default: `files`).
- `--dry-run` — report transitions without writing any change.
- `--json` — emit structured JSON output.
"""


_MIGRATE = """\
# eidetic-cli migrate

One-shot maintenance imports/upgrades for the eidetic store. Exposes two
targets: `migrate qq` (import the legacy "QQ" memory layers) and `migrate store`
(upgrade an existing store's on-disk format from Record to Envelope JSONL).

## migrate qq

Reads the QQ markdown files (`core.md` / `notes.md`, one record per `##`
section), the QQ MongoDB `claude_notes` collection, and the QQ Neo4j entities
tagged `knowledge_context="claude"`. Each source is guarded: a down/absent
Mongo or Neo4j is **skipped with a warning** (to stderr) and the run completes
with the remaining sources.

Stable per-source ids make re-runs idempotent (upsert by id, never duplicate):
`qq-file:<path>#<section-slug>`, `qq-mongo:<note_id>`, `qq-neo4j:<entity_id>`.
Within one file, duplicate headings that slug identically are disambiguated —
the first keeps the bare slug, later ones get a `-2`/`-3` suffix — so a repeated
`## ` heading never makes one section silently overwrite another.
Provenance + a date signature ride along in `metadata` and `created`
(file mtime / Mongo `last_accessed` / Neo4j `last_seen`, falling back to the
decay-neutral date-unknown sentinel).

### No-leak default

QQ files/`core.md` hold PERSONAL data, so migration writes into a **private**
scope by default (`--scope qq --visibility private`). Migrated personal
knowledge therefore never surfaces in a public recall. Both flags are
configurable.

## migrate store

A one-shot, **idempotent** in-place upgrade of an existing store from the legacy
Record-format JSONL to data-refinery's Envelope-format JSONL (issues #13, #8).
eidetic no longer owns its storage engine **and constructs no filesystem write
path**: it delegates the rewrite to data-refinery's `store.migrate` endpoint,
supplying only a record->Envelope transform and the store root it already owns.
data-refinery — which owns the store layout — resolves paths, validates the whole
store, and rewrites **atomically per file**. Already-migrated lines pass through
untouched, so re-running converts nothing. Reports data-refinery's file-granularity
summary `{backend, files, migrated, migrated_files, skipped, dry_run}`; `--dry-run`
reports the same counts but writes nothing; `--data-dir` overrides the store
location (default: `EIDETIC_DATA_DIR`, else `~/.eidetic/memory`; the repo store at `<repo-root>/.eidetic/memory` requires an explicit `--data-dir`).

## migrate qq flags

- `--file PATH` — QQ markdown source to read (repeatable; defaults to the known
  `core.md`/`notes.md` paths).
- `--backend` — destination backend: `files`, `mongo`, `neo4j`, or `graph`
  (`graph` is an alias for `neo4j`; default: `files`).
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
    eidetic-cli migrate store --dry-run --json
    eidetic-cli migrate store
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
    ("migrate", "store"): _MIGRATE,
}
