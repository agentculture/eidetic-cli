#!/usr/bin/env bash
# remember.sh — ingest records into the shared eidetic memory store (the /remember skill).
#
# Thin, portable wrapper around `eidetic remember`. It resolves the CLI, points
# the embedding endpoint at the local model-gear embed gear (overridable), and
# forwards every argument verbatim. Accepts ONE record as a JSON object argument,
# or a BATCH as NDJSON on stdin (one JSON object per line) for bulk ingest.
#
#   remember.sh '{"id":"d1","text":"...","type":"docs","metadata":{...}}' --json
#   cat records.ndjson | remember.sh --json
#
# Upsert is idempotent by id (and dedups by content hash): re-remembering the
# same record updates it in place, never duplicates.
#
# The store is the files backend at ~/.eidetic/memory by default — a home-dir
# path OUTSIDE any git worktree, so a record Claude remembers is recallable by
# the colleague backend (which runs in throwaway worktrees), and vice versa.
# Set EIDETIC_DATA_DIR to opt out of sharing; use --backend mongo|neo4j (with
# EIDETIC_MONGO_URI / NEO4J_URI) for a server-backed shared store.

set -euo pipefail

# ── resolve the eidetic CLI (installed tool first, then dev checkout) ────────
EIDETIC=()
resolve_eidetic() {
    if command -v eidetic >/dev/null 2>&1; then
        EIDETIC=(eidetic)
        return 0
    fi
    local dir
    dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/pyproject.toml" ] \
            && grep -q '^name = "eidetic-cli"' "$dir/pyproject.toml" 2>/dev/null; then
            if command -v uv >/dev/null 2>&1; then
                EIDETIC=(uv run --project "$dir" eidetic)
                return 0
            fi
            break
        fi
        dir=$(dirname "$dir")
    done
    cat >&2 <<'EOF'
error: eidetic CLI not found.
hint: install it with `uv tool install eidetic-cli` (or `pipx install eidetic-cli`),
      or run from inside the eidetic-cli checkout with `uv` available.
      The console script is `eidetic` (dist name: eidetic-cli).
EOF
    return 1
}

usage() {
    cat <<'EOF'
remember.sh — ingest records into the shared eidetic memory store (the /remember skill).

Usage:
  remember.sh '<json-object>' [--json] [--backend files|mongo|neo4j] \
              [--scope NAME] [--visibility public|private]
  cat records.ndjson | remember.sh [--json] ...

A record needs `id`, `text`, and `type`; `hash` and `metadata` are recommended
(hash is derived from text when omitted). Upsert is idempotent by id.
Public data only. Every flag is forwarded verbatim to `eidetic remember`.
See `eidetic explain remember`.
EOF
}

case "${1:-}" in
    -h | --help | help)
        usage
        exit 0
        ;;
esac

resolve_eidetic || exit 2

: "${EIDETIC_EMBED_URL:=http://localhost:8002/v1}"
: "${EIDETIC_EMBED_MODEL:=Qwen/Qwen3-Embedding-0.6B}"
export EIDETIC_EMBED_URL EIDETIC_EMBED_MODEL

exec "${EIDETIC[@]}" remember "$@"
