# eidetic-cli's default memory store becomes project-local: a PUBLIC record written inside a git repo lands in that repo's committed store (<repo-root>/.eidetic/memory), while PRIVATE records and any record written outside a git repo fall back to $HOME/.eidetic/memory.

> eidetic-cli's default memory store becomes project-local: a PUBLIC record written inside a git repo lands in that repo's committed store (<repo-root>/.eidetic/memory), while PRIVATE records and any record written outside a git repo fall back to $HOME/.eidetic/memory.

## Audience

- AgentCulture mesh agents (Claude in the main checkout + the colleague backend in throwaway worktrees) and direct CLI consumers that call 'eidetic remember/recall' over a subprocess (#3 jetson-ai-lab-cli, #1 research pipeline). All of them invoke the same CLI.

## Before → After

- Before: Today backend.py _bridge_env sets DR_DATA_DIR = EIDETIC_DATA_DIR or Path.home()/.eidetic/memory unconditionally — no git awareness, no visibility awareness. One global cross-project store; project isolation only via --scope, never by path.
- After: Store path is resolved per-operation from CWD AND visibility: PUBLIC + inside a git repo -> <git-toplevel>/.eidetic/memory (committed, team-shared); PRIVATE, or any record outside a git repo -> $HOME/.eidetic/memory (never committed). The default becomes project-local by path.

## Why it matters

- Project work should recall that project's memory by default instead of a cross-project soup, AND team-relevant (public) memory should travel with the repo via git — without breaking the existing Claude<->colleague shared-memory story.

## Honesty conditions

- The repo-root default is additive and overridable: explicit --data-dir and EIDETIC_DATA_DIR always win, so any existing caller that sets them sees zero behavior change.
- All three audiences hit the same single decision point in backend.py, so direct consumers #1/#3 and the shell wrappers resolve the store identically — there is no wrapper-only divergence to reason about.
- The before-state is verified against the code: the exact line backend.py:112-114 (DR_DATA_DIR = EIDETIC_DATA_DIR or Path.home()/.eidetic/memory) is what changes; nothing else resolves the files-backend path.
- Routing is exactly: explicit override > (visibility==public AND in git repo -> git-toplevel store) > $HOME. A private record in a repo, and any record outside a repo, both resolve to $HOME. [user-confirmed via AskUserQuestion]
- Claude<->colleague sharing survives on BOTH paths: private via the shared same-machine $HOME store (today's mechanism), public via git checkout-copy of the committed store into the colleague's worktree. Neither path strands the other agent.
- The override path (EIDETIC_DATA_DIR set) is regression-tested to be byte-identical to today; the new public-in-repo and private/outside-repo branches are each covered by their own tests.
- The clean break holds: the populated $HOME/.eidetic/memory is left untouched and stays reachable (private recall, outside-repo, or explicit override); no migration or copy is performed by this change.

## Success signals

- Tests cover all branches: (a) public-in-repo writes/recalls under <git-toplevel>/.eidetic/memory, (b) private and outside-repo use $HOME, (c) explicit EIDETIC_DATA_DIR/--data-dir still win unchanged. This repo's /remember & /recall wrapper docs + SKILL.md descriptions are updated to the new routing. teken rubric stays green; version bumped.

## Scope / boundaries

- Scope is the FILES-backend default directory + leaving the existing $HOME store intact (clean break). NOT changing: record schema, ranking modes, scoring/freshness, the can_serve no-leak semantics, or the mongo/neo4j network backends. Explicit --data-dir / EIDETIC_DATA_DIR overrides are unchanged.

## Decisions

- Resolution lives at the single decision point in eidetic/memory/backend.py (Python), made CWD-based-git-aware AND visibility-aware, so every CLI consumer (#1/#3 + the shell wrappers) shares one behavior. find_culture_yaml's module-walk is NOT reused.
- 'repo root' is the literal git toplevel of the CLI's CWD (git rev-parse --show-toplevel). No common-dir trick: cross-worktree sharing comes from committing the public store (copied into the colleague's worktree at 'git worktree add') and from private records using the shared $HOME store.
- Visibility gains a physical-routing role (public->committed repo store, private->$HOME) IN ADDITION to its logical no-leak role; can_serve/no-leak semantics themselves are unchanged. The public repo store is committed (team-shared); it carries no private records, so committing it leaks nothing.

## Open / follow-up

- The /remember & /recall SKILL.md descriptions + wrapper header comments ('$HOME/.eidetic/memory ... OUTSIDE any git worktree') are fanned out byte-verbatim across ~57 org repos; updating them everywhere is a separate rollout-cli re-sync, tracked as follow-up beyond this repo's own docs.
- Committing .eidetic/memory means a git commit/diff per public remember and possible merge conflicts across teammates; the data_refinery files on-disk format's merge-friendliness should be validated.
- Per-record visibility WITHIN a single batch NDJSON ingest (mixed public/private lines needing two stores in one op) is out of scope; visibility is a per-invocation flag today.
