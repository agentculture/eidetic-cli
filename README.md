# eidetic-cli

Agent/CLI providing eidetic perfect-recall memory

## What you get

- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`) — the runtime declares `neo4j` and `pymongo` for its Neo4j and Mongo
  memory backends; consumers stay dependency-free because they call `eidetic`
  over a subprocess boundary.
- **A mesh identity** — `culture.yaml` (`suffix` + `backend`) and the matching
  prompt file (`CLAUDE.md` for `backend: claude`).
- **The canonical guildmaster skill kit** (11 skills) under `.claude/skills/`,
  vendored cite-don't-import. See [`docs/skill-sources.md`](docs/skill-sources.md).
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
| `overview` | Read-only descriptive snapshot of the agent. |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `remember` | Ingest memory records — one JSON object or NDJSON on stdin; idempotent upsert by id; `--backend`/`--scope`/`--visibility`. |
| `recall <query>` | Search the store — top-k hits with text + full metadata + score; scope-aware (no private→public leak); `--top-k`/`--filter`/`--backend`. |
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
running stack). Embeddings + rerank come from a separate model-gear HTTP endpoint,
with a deterministic local lexical fallback when it is absent.

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
