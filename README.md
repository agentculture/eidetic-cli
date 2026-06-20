# eidetic-cli

Agent/CLI providing eidetic perfect-recall memory

## What you get

- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`) — the runtime declares `neo4j` and `pymongo` for its Neo4j and Mongo
  memory backends; consumers stay dependency-free because they call `eidetic`
  over a subprocess boundary.
- **A mesh identity** — `culture.yaml` (`suffix` + `backend`) and the matching
  prompt file (`CLAUDE.md` for `backend: claude`).
- **The canonical guildmaster skill kit** under `.claude/skills/`, vendored
  cite-don't-import, plus eidetic's own first-party `remember` / `recall` skills
  (a shared `~/.eidetic/memory` store both Claude and the colleague backend can
  drive). See [`docs/skill-sources.md`](docs/skill-sources.md).
- **A build + deploy baseline** — pytest, lint, the agent-first rubric gate, and
  PyPI Trusted Publishing wired into GitHub Actions.

## Quickstart

```bash
uv sync
uv run pytest -n auto               # run the test suite
uv run eidetic whoami              # identity from culture.yaml (console script is `eidetic`, not `eidetic-cli`)
uv run eidetic learn               # self-teaching prompt (add --json)
uv run teken cli doctor . --strict  # the agent-first rubric gate CI runs
```

## CLI

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` | Read-only descriptive snapshot of the agent. `--store` appends live store stats — per-backend record counts (files/mongo/graph), per-scope + lifecycle breakdown, and link-connections; narrow with `--backend`/`--scope` (a down backend degrades to `unavailable`, never crashes). |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `remember` | Ingest memory records — one JSON object or NDJSON on stdin; idempotent upsert by id; stamps `created` date; accepts `supersedes`/`links`; `--backend`/`--scope`/`--visibility`. |
| `recall <query>` | Search the store — top-k hits with text + full metadata + `score` + `signal`; scope-aware (no private→public leak). Four `--mode`s: `exact` (substring), `approximate` (vector), `keyword` (BM25), `hybrid` (blend, default; `--alpha`). Lifecycle flags: `--include-shadowed`, `--include-archived` (both excluded by default). Plus `--top-k`/`--filter`/`--backend`/`--case-sensitive`. |
| `sweep` | Apply lifecycle transitions (shadow/archive) across the whole store — never deletes, only flips `lifecycle`. Supports `--dry-run`. |
| `migrate qq` | One-shot idempotent import of legacy QQ memory (core.md/notes.md, MongoDB, Neo4j) into a private scope. |
| `cli overview` | Describe the CLI surface itself. |

Every command supports `--json`. Results go to stdout, errors/diagnostics to
stderr (never mixed). Exit codes: `0` success, `1` user error, `2` environment
error, `3+` reserved.

## Memory stores

The `files` backend (default) is self-contained — JSONL on disk, no services. For
the `neo4j` / `mongo` backends, eidetic owns its own stores via `docker-compose.yml`:

```bash
docker compose up -d                # eidetic-neo4j (bolt :7687) + eidetic-mongo (:27018)
eidetic remember --backend mongo …  # the CLI's default connection settings already match
```

eidetic is the memory layer itself — these are eidetic's own stores (their store /
Cypher / embedding logic is *adapted* from `data-refinery`, not a dependency on its
running stack). Embeddings + rerank come from a separate model-gear HTTP endpoint
(`EIDETIC_EMBED_URL`, `EIDETIC_EMBED_MODEL` — default
`http://localhost:8002/v1` + `Qwen/Qwen3-Embedding-0.6B`), with a deterministic
local lexical fallback when it is absent. Only `approximate`/`hybrid` recall use
it; `exact`/`keyword` are pure lexical and work fully offline.

## Freshness signal

Every record carries temporal state used to compute a *freshness signal* — a
float in `[0, 1]` that blends into recall ranking so recently-created and
frequently-recalled records surface ahead of stale ones:

- `created` — ISO-8601 date stamped at `remember` time; drives the age-decay
  factor (`1/(1 + days_old * DECAY_RATE)`).
- `last_recall` + `recall_count` — updated passively on every `recall` hit;
  drive an access bonus (capped at +0.5) and a staleness penalty
  (`days_since_recall * DECAY_RATE`).
- `links` — related-memory references; reserved for a future corroboration term
  (weight is currently 0.0, the hook is wired).

The signal is computed at recall time and exposed as `signal` in every hit
alongside `score`. Records with no temporal data (undated legacy records) pass
through unmodified — the blend is an exact identity for them.

The blend is multiplicative around the neutral midpoint (`SIGNAL_BLEND_BETA = 0.25`),
so a fully neutral signal is a no-op and only records carrying real temporal data
move in rank. The formula:

```text
access_bonus = min(0.5, recall_count * 0.05)
age_factor   = 1 / (1 + days_old * 0.01)
staleness    = days_since_last_recall * 0.01
signal       = clamp((0.5 - staleness + access_bonus) * age_factor, 0, 1)
blended_score = score * (1 + 0.25 * (signal - 0.5))
```

## Lifecycle (no hard-delete)

eidetic never deletes a record. Records move through a `lifecycle` state machine:

| State | Meaning |
|-------|---------|
| `active` | Default; visible in recall results. |
| `shadowed` | Superseded within the same scope by a newer record that declares `supersedes`. Retrieved only with `--include-shadowed`. |
| `archived` | Older than ~1 year (`created`) or signal below threshold (0.25). Retrieved only with `--include-archived`. |

Transitions are applied by `eidetic sweep` (the only command that writes lifecycle
changes). `--dry-run` reports without writing. Records with `metadata.protected`
set to a truthy value are exempt from all transitions.

**Within-scope shadowing only.** A `supersedes` link only shadows its target when
both records share the same scope (name *and* visibility). Cross-scope links are
ignored, preserving the public/private no-leak invariant.

**Ingest with supersedes and links:**

```bash
# New version of a record shadows the old one (same scope required):
eidetic remember '{"id":"r2","text":"...","type":"note","supersedes":"r1","links":["r3","r4"]}'
```

Then run `eidetic sweep` to apply the transition: `r1` gets `lifecycle=shadowed`,
`r2` stays `active`.

## Migrate QQ memory

`eidetic migrate qq` performs a one-shot idempotent import of the legacy QQ
(Claude's personal) memory stack into a private eidetic scope:

- **Sources read:** `~/.claude/skills/memory/references/core.md` and `notes.md`
  (one record per `##` section), MongoDB `claude_notes` collection, Neo4j entities
  tagged `knowledge_context="claude"`.
- **Destination:** `--scope qq --visibility private` by default — personal data
  never leaks into a public recall.
- **Idempotent:** stable per-source ids (`qq-file:<path>#<section-slug>`,
  `qq-mongo:<id>`, `qq-neo4j:<id>`) make re-runs safe.
- **Resilient:** a down Mongo or Neo4j is skipped with a warning, not fatal.

```bash
eidetic migrate qq --json           # migrate from all sources, JSON report
eidetic migrate qq --backend mongo  # store into eidetic's mongo backend
eidetic migrate qq --file ~/my.md   # restrict to a specific markdown file
```

**Known limitations** (tracked follow-ups): `--filter` is exact string-equality on
metadata (time-range filtering is future work); the files backend re-embeds
candidates per search (no embedding cache yet); the Neo4j backend fetches nodes and
ranks in Python (vector-index pushdown is future work).

## Make it your own

1. Rename the package `eidetic/` and the `eidetic-cli`
   CLI/dist name throughout `pyproject.toml`, the package, `tests/`,
   `sonar-project.properties`, and this `README.md`. The name is hard-coded in
   ~30 files, so list every occurrence first — see the `git grep` discovery
   command in [`CLAUDE.md`](CLAUDE.md), the authoritative rename procedure.
2. Edit `culture.yaml` with your `suffix` and `backend`.
3. Rewrite `CLAUDE.md` for your agent and run `/init`.
4. Re-vendor only the skills you need from guildmaster (see
   [`docs/skill-sources.md`](docs/skill-sources.md)).

See [`CLAUDE.md`](CLAUDE.md) for the full conventions (version-bump-every-PR,
the `cicd` PR lane, deploy setup).

## License

MIT — see [`LICENSE`](LICENSE).
