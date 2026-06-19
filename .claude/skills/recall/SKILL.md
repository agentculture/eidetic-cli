---
name: recall
type: command
description: >
  Search the shared eidetic memory store and get back ranked, provenanced
  records. Drives `eidetic recall` with four search modes — exact (verbatim
  substring), approximate (vector/semantic), keyword (BM25 lexical), and hybrid
  (a weighted blend of vector+keyword, the default) — each hit carrying its text,
  full metadata, and a relevance score. The store lives at ~/.eidetic/memory (a
  home-dir path outside any git worktree), so Claude and the colleague backend
  recall each other's memories from one shared store. Use when the user says
  "recall", "what do we know about X", "search memory", "have we seen X before",
  "look it up in memory", "eidetic recall", or before answering from scratch when
  prior context may already be stored. Pairs with the sibling /remember skill.
---

# recall — search the shared eidetic memory

`recall` drives **`eidetic recall`**: given a query, it returns the top-k stored
records ranked by relevance, each with its `text`, full `metadata` (provenance),
and a numeric `score`. It is the read half of the memory surface; the write half
is the sibling **/remember** skill.

The point of a *shared* store is that memory is a **team faculty**, not a
per-agent silo: a record Claude wrote is recallable by the colleague backend
(and vice versa), because both resolve the same `~/.eidetic/memory` path.

## How to run

```bash
bash .claude/skills/recall/scripts/recall.sh "<query>" [flags...]
```

The wrapper resolves the CLI portably (installed `eidetic` on `PATH`, else
`uv run eidetic` from the checkout) and forwards every flag verbatim, so it is
exactly `eidetic recall …`. Run it from anywhere; the store is the same.

## Search modes (`--mode`, default `hybrid`)

| Mode | What it matches | Needs embed server? |
|------|-----------------|---------------------|
| `exact` | case-insensitive verbatim substring (`--case-sensitive` to tighten) | no — offline-safe |
| `approximate` | vector cosine / semantic similarity | yes (falls back offline) |
| `keyword` | BM25 lexical; only records sharing a query term | no — offline-safe |
| `hybrid` | `alpha*approximate + (1-alpha)*keyword` (`--alpha`, default 0.5) | uses it when up |

`hybrid` is the default because the two signals cover each other's blind spots:
vector catches paraphrases, keyword catches exact ids/quotes. When the embed
server is unreachable, `hybrid` collapses to keyword-only (it never fuses
meaningless offline-fallback cosine).

## Common flags (forwarded to `eidetic recall`)

- `--mode exact|approximate|keyword|hybrid` — default `hybrid`.
- `--top-k N` — max results (default 5).
- `--alpha F` — hybrid blend weight in `[0,1]` (default 0.5).
- `--case-sensitive` — for `--mode exact`.
- `--filter KEY=VALUE` — metadata facet filter (repeatable): e.g. `--filter source=docs`.
- `--scope NAME` / `--visibility public|private` — scope isolation (no private leak).
- `--backend files|mongo|neo4j` — default `files` (the shared home-dir store).
- `--json` — structured list to stdout (use this when an agent parses the result).

## Examples

```bash
# Default hybrid recall, JSON for an agent to parse:
bash .claude/skills/recall/scripts/recall.sh "jetson nano power draw" --json

# Find the exact message that mentions a phrase:
bash .claude/skills/recall/scripts/recall.sh "Orin Nano" --mode exact

# Keyword search, offline-safe, narrowed to a source:
bash .claude/skills/recall/scripts/recall.sh "thermal throttle" --mode keyword \
    --filter source=discord --top-k 10
```

## Notes

- **Provenance is mandatory** on every hit — recall is for *cited* answers.
- The embed endpoint defaults to the local model-gear embed gear
  (`http://localhost:8002/v1`, model `Qwen/Qwen3-Embedding-0.6B`); override with
  `EIDETIC_EMBED_URL` / `EIDETIC_EMBED_MODEL`. `exact`/`keyword` ignore it.
- `eidetic explain recall` is the authoritative flag/behaviour reference.

## Provenance

First-party to **eidetic-cli** — eidetic owns its memory surface. Cite, don't
import: downstream repos copy this skill, they don't symlink it. See
[`docs/skill-sources.md`](../../../docs/skill-sources.md).
