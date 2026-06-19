---
name: remember
type: command
description: >
  Ingest records into the shared eidetic memory store so they can be recalled
  later. Drives `eidetic remember`: accepts one record as a JSON object, or a
  batch as NDJSON on stdin for bulk ingest. Upsert is idempotent by id (and
  dedups by content hash) — re-remembering updates in place, never duplicates.
  The store lives at ~/.eidetic/memory (a home-dir path outside any git
  worktree), so what Claude remembers, the colleague backend can recall (and vice
  versa). Use when the user says "remember this", "store this", "save to memory",
  "index these", "eidetic remember", or when something learned this session
  should outlive it. Public data only. Pairs with the sibling /recall skill.
---

# remember — write to the shared eidetic memory

`remember` drives **`eidetic remember`**, the write half of the memory surface
(the read half is the sibling **/recall** skill). Records you store here are
recallable later by *any* agent on this machine — Claude or the colleague
backend — because the default store is one shared `~/.eidetic/memory` path.

## How to run

```bash
# One record (JSON object as the argument):
bash .claude/skills/remember/scripts/remember.sh \
  '{"id":"d1","text":"Orin Nano draws 7-15W","type":"docs","metadata":{"source":"docs","permalink":"https://..."}}' --json

# Batch (NDJSON on stdin, one record per line) — for bulk re-index:
cat records.ndjson | bash .claude/skills/remember/scripts/remember.sh --json
```

The wrapper resolves the CLI portably (installed `eidetic` on `PATH`, else
`uv run eidetic` from the checkout) and forwards every flag verbatim.

## Record shape

| Field | Required? | Notes |
|-------|-----------|-------|
| `id` | yes | stable identity; the upsert key |
| `text` | yes | the chunk being remembered |
| `type` | recommended | e.g. `note`, `docs`, `discord`, a research object type |
| `hash` | optional | content hash for dedup; derived from `text` when omitted |
| `metadata` | recommended | provenance + facets; **round-trips verbatim** on recall |

`score` is recall-only and is ignored on ingest. **Public data only** — never
remember private/role-gated content into the shared store.

## Idempotency

Re-submitting a record with the same `id` overwrites the previous value; a record
with a matching content `hash` is de-duplicated. So re-running an ingest (e.g. a
periodic re-scan) is safe and will not create duplicates.

## Flags (forwarded to `eidetic remember`)

- `--json` — structured result (`{"upserted": N, "ids": [...]}`) to stdout.
- `--scope NAME` / `--visibility public|private` — record scope (default
  `default`/`public`). Private records are served only to a query in the same scope.
- `--backend files|mongo|neo4j` — default `files` (the shared home-dir store);
  use `mongo`/`neo4j` (with `EIDETIC_MONGO_URI` / `NEO4J_URI`) for a server store.

## Notes

- The embed endpoint defaults to the local model-gear embed gear
  (`http://localhost:8002/v1`); override with `EIDETIC_EMBED_URL` /
  `EIDETIC_EMBED_MODEL`. Ingest still works offline (embeddings are recomputed at
  recall time).
- **Use the wrapper, not a bare `eidetic`.** The console script may not be on
  `PATH` (in a dev checkout it isn't); the wrapper resolves it (`PATH` first, else
  `uv run eidetic`). For the docs, run `eidetic explain remember` if installed,
  otherwise `uv run --project <eidetic-cli checkout> eidetic explain remember`.

## Provenance

First-party to **eidetic-cli** — eidetic owns its memory surface. Cite, don't
import: downstream repos copy this skill, they don't symlink it. See
[`docs/skill-sources.md`](../../../docs/skill-sources.md).
