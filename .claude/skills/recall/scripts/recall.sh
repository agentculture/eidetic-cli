#!/usr/bin/env bash
# recall.sh — search the shared eidetic memory store (the /recall skill).
#
# Thin, portable wrapper around `eidetic recall`. It resolves the CLI, points
# the embedding modes at the local model-gear embed gear (overridable), and
# forwards every flag verbatim — so `recall.sh "<query>" --mode hybrid --json`
# is exactly `eidetic recall "<query>" --mode hybrid --json`.
#
# The store is the files backend. Default location resolves per-operation:
# PUBLIC records inside a git repo → <repo-root>/.eidetic/memory (committed,
# team-shared); PRIVATE records, or any record outside a git repo →
# $HOME/.eidetic/memory (never committed). Recall reads both stores and merges.
# An explicit EIDETIC_DATA_DIR wins and short-circuits to that single dir.

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
    # In a vendored copy there is no eidetic-cli checkout to fall back to, so the
    # only honest remedy is to install the CLI. One `error:` + one `hint:` line.
    printf 'error: eidetic CLI not found.\n' >&2
    printf 'hint: install it with: uv tool install eidetic-cli (or pipx install eidetic-cli); the console script is eidetic.\n' >&2
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
    -h | --help)
        usage
        exit 0
        ;;
    "")
        # A missing query is a usage error, not success. The bareword `help` is
        # a legitimate search term, so it is intentionally NOT a usage alias.
        printf 'error: no query given.\n' >&2
        printf 'hint: recall.sh "<query>" [--mode ...] [--json]; run recall.sh --help for usage.\n' >&2
        exit 1
        ;;
esac

resolve_eidetic || exit 2

# ── default to this agent's PERSONAL, PUBLIC scope (culture.yaml `suffix`) ───
# Query this agent's OWN personal scope by default, matching where /remember
# writes, instead of the global `default` scope shared by every project on this
# host. We read the `suffix` from the nearest culture.yaml (walking up from this
# script), so the scope follows the repo identity rather than being hard-coded —
# a downstream cite-don't-import copy adapts to its own suffix, and the colleague
# backend (running in a worktree of this same repo) resolves the same suffix,
# keeping the Claude↔colleague shared-memory story intact.
#
# The personal scope is PUBLIC by default to match /remember — the memory
# scope+visibility convention (v1, docs/contract.md): a public record is
# visible to ANY query scope regardless of name (`can_serve`,
# eidetic/memory/scope.py), so a no-flag recall here returns the full public
# pool, matching both the plain `eidetic recall` CLI's own --visibility
# default and the colleague backend's runtime (colleague/memory.py hardcodes
# --visibility public). Scope and visibility are paired — the public default
# applies only when we inject the resolved scope, and only if the caller
# didn't pass --visibility (so an explicit `--visibility private` still
# wins). Passing --visibility private restores exactly what used to be the
# implicit default: this agent's own private notes (scope=<suffix>, matched
# exactly — `can_serve` only serves a private record to a query in the SAME
# scope) PLUS the full public pool (a private query still sees every public
# record too — that "private + public" merge is `can_serve`'s behavior
# whenever the QUERY itself is private, unrelated to this default). What
# changed is only the no-flag case: a plain recall now returns public-only,
# since a public-visibility query structurally excludes every private
# record regardless of scope name. An explicit --scope on the command line
# takes over steering entirely; a wheel install with no culture.yaml falls
# back to the plain CLI default (`default`/`public`).
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
    # --scope/--visibility unset entirely, so the plain `eidetic recall`
    # defaults apply (scope=default, visibility=public) — identical
    # visibility to the suffix-resolved case above, just grouped under the
    # `default` scope name instead of this agent's personal one. Nothing to
    # warn about: unlike the old private-by-default behavior, there is no
    # unexpected-leak or unexpected-empty-result surprise here either way.
fi

# Default the embedding endpoint to the local model-gear embed gear. eidetic
# falls back to a deterministic offline embedding if it's unreachable, so this
# is safe even when the gear is down. Override by exporting these yourself.
: "${EIDETIC_EMBED_URL:=http://localhost:8002/v1}"
: "${EIDETIC_EMBED_MODEL:=Qwen/Qwen3-Embedding-0.6B}"
export EIDETIC_EMBED_URL EIDETIC_EMBED_MODEL

exec "${EIDETIC[@]}" recall "${SCOPE_ARGS[@]}" "$@"
