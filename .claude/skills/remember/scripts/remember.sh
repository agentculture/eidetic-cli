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
# The store is the files backend. Default location resolves per-operation:
# PUBLIC records inside a git repo → <repo-root>/.eidetic/memory (committed,
# team-shared); PRIVATE records, or any record outside a git repo →
# $HOME/.eidetic/memory (never committed). An explicit EIDETIC_DATA_DIR still
# wins and short-circuits to that single dir. Use --backend mongo|neo4j (with
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
    # In a vendored copy there is no eidetic-cli checkout to fall back to, so the
    # only honest remedy is to install the CLI. One `error:` + one `hint:` line.
    printf 'error: eidetic CLI not found.\n' >&2
    printf 'hint: install it with: uv tool install eidetic-cli (or pipx install eidetic-cli); the console script is eidetic.\n' >&2
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
Records default to this agent's PERSONAL, PUBLIC scope (--scope from the
culture.yaml suffix, --visibility public); pass --visibility private to keep
a record out of the shared/committed store. Every flag is forwarded verbatim
to `eidetic remember`. See `eidetic explain remember`.
EOF
}

case "${1:-}" in
    -h | --help | help)
        usage
        exit 0
        ;;
esac

# No record argument AND stdin is an interactive terminal → `eidetic remember`
# would block forever waiting for NDJSON. Show usage instead of hanging. A piped
# or redirected stdin (`cat records.ndjson | remember.sh`) is not a TTY and
# proceeds to the batch path normally.
if [ "$#" -eq 0 ] && [ -t 0 ]; then
    usage >&2
    printf 'hint: pass a JSON record as an argument, or pipe NDJSON on stdin.\n' >&2
    exit 1
fi

resolve_eidetic || exit 2

# ── default to this agent's PERSONAL, PUBLIC scope (culture.yaml `suffix`) ───
# A record this agent remembers should land in its OWN personal scope, not the
# global `default` scope shared by every project on this host. We read the
# `suffix` from the nearest culture.yaml (walking up from this script), so the
# scope follows the repo identity rather than being hard-coded — a downstream
# cite-don't-import copy adapts to its own suffix, and the colleague backend
# (running in a worktree of this same repo) resolves the same suffix, keeping
# the Claude↔colleague shared-memory story intact.
#
# The personal scope is PUBLIC by default — the memory scope+visibility
# convention (v1, docs/contract.md): an in-repo record is team-shared by
# default (routing already sends a public write inside a git repo to
# <repo-root>/.eidetic/memory, committed alongside the code), matching both
# the plain `eidetic remember` CLI's own --visibility default and the
# colleague backend's runtime (colleague/memory.py hardcodes --visibility
# public) — so a no-flag remember here and a no-flag `eidetic remember`
# elsewhere land mutually-visible records instead of silently disagreeing.
# Scope and visibility are paired — the public default applies only when we
# inject the resolved scope, and only if the caller didn't pass --visibility
# (so an explicit `--visibility private` still wins, keeping genuinely
# personal/non-shareable data out of the committed store). An explicit
# --scope on the command line takes over steering entirely; a wheel install
# with no culture.yaml falls back to the plain CLI default (`default`/`public`).
resolve_scope() {
    local dir suffix=""
    dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/culture.yaml" ]; then
            # Capture only the first non-space token after `suffix:` (so an
            # inline `# comment` or trailing space can't bleed into the scope),
            # then strip surrounding quotes only — matching the canonical parser
            # in .claude/skills/cicd/scripts/_resolve-nick.sh.
            # `|| true`: under `set -o pipefail`, `head -n1` closing the pipe
            # early can SIGPIPE `sed`, making the substitution non-zero and
            # aborting the script. An empty parse must yield "" here, not exit.
            suffix=$(sed -n \
                's/^[[:space:]]*-\{0,1\}[[:space:]]*suffix:[[:space:]]*\([^[:space:]]*\).*/\1/p' \
                "$dir/culture.yaml" | head -n1 | tr -d "\"'" || true)
            break
        fi
        dir=$(dirname "$dir")
    done
    printf '%s' "$suffix"
}

has_flag() {
    local needle=$1
    shift
    local a
    for a in "$@"; do
        case "$a" in
            "$needle" | "$needle"=*) return 0 ;;
        esac
    done
    return 1
}

SCOPE_ARGS=()
if ! has_flag --scope "$@"; then
    EIDETIC_SCOPE=$(resolve_scope)
    if [ -n "$EIDETIC_SCOPE" ]; then
        SCOPE_ARGS+=(--scope "$EIDETIC_SCOPE")
        has_flag --visibility "$@" || SCOPE_ARGS+=(--visibility public)
    fi
    # No suffix resolved (e.g. a wheel install with no culture.yaml): leave
    # --scope/--visibility unset entirely, so the plain `eidetic remember`
    # defaults apply (scope=default, visibility=public) — identical
    # visibility to the suffix-resolved case above, just grouped under the
    # `default` scope name instead of this agent's personal one. Nothing to
    # warn about: unlike the old private-by-default behavior, there is no
    # privacy downgrade here either way.
fi

: "${EIDETIC_EMBED_URL:=http://localhost:8002/v1}"
: "${EIDETIC_EMBED_MODEL:=Qwen/Qwen3-Embedding-0.6B}"
export EIDETIC_EMBED_URL EIDETIC_EMBED_MODEL

exec "${EIDETIC[@]}" remember "${SCOPE_ARGS[@]}" "$@"
