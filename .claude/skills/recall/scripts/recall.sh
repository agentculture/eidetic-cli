#!/usr/bin/env bash
# recall.sh — search the shared eidetic memory store (the /recall skill).
#
# Thin, portable wrapper around `eidetic recall`. It resolves the CLI, points
# the embedding modes at the local model-gear embed gear (overridable), and
# forwards every flag verbatim — so `recall.sh "<query>" --mode hybrid --json`
# is exactly `eidetic recall "<query>" --mode hybrid --json`.
#
# The store is the files backend at ~/.eidetic/memory by default — a home-dir
# path OUTSIDE any git worktree, so Claude and the colleague backend (which runs
# in throwaway worktrees) read the SAME memories. Set EIDETIC_DATA_DIR to opt out
# of sharing; set EIDETIC_MONGO_URI / NEO4J_URI + --backend for a server store.

set -euo pipefail

# ── resolve the eidetic CLI (installed tool first, then dev checkout) ────────
EIDETIC=()
resolve_eidetic() {
    if command -v eidetic >/dev/null 2>&1; then
        EIDETIC=(eidetic)            # installed console script — the normal case
        return 0
    fi
    # Dev fallback: inside the eidetic-cli checkout, run via uv.
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
recall.sh — search the shared eidetic memory store (the /recall skill).

Usage:
  recall.sh "<query>" [--mode exact|approximate|keyword|hybrid] [--top-k N] \
            [--alpha F] [--case-sensitive] [--filter KEY=VALUE]... \
            [--backend files|mongo|neo4j] [--scope NAME] [--visibility public|private] \
            [--json]

Modes (default: hybrid):
  exact        case-insensitive verbatim substring (--case-sensitive to tighten); offline-safe
  approximate  vector cosine / semantic similarity (uses the embed server)
  keyword      BM25 lexical; only records sharing a query term; offline-safe
  hybrid       alpha*approximate + (1-alpha)*keyword (--alpha, default 0.5);
               degrades to keyword-only when the embed server is offline

Every flag is forwarded verbatim to `eidetic recall`. See `eidetic explain recall`.
EOF
}

case "${1:-}" in
    -h | --help | help | "")
        usage
        exit 0
        ;;
esac

resolve_eidetic || exit 2

# Default the embedding endpoint to the local model-gear embed gear. eidetic
# falls back to a deterministic offline embedding if it's unreachable, so this
# is safe even when the gear is down. Override by exporting these yourself.
: "${EIDETIC_EMBED_URL:=http://localhost:8002/v1}"
: "${EIDETIC_EMBED_MODEL:=Qwen/Qwen3-Embedding-0.6B}"
export EIDETIC_EMBED_URL EIDETIC_EMBED_MODEL

exec "${EIDETIC[@]}" recall "$@"
