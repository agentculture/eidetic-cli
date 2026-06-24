# eidetic's memory store is now fully repo-contained: one .eidetic/memory directory per repo holds both public and private records, with private kept out of git by a fail-closed .gitignore — no more split between a repo store and a global $HOME store

> eidetic's memory store is now fully repo-contained: one .eidetic/memory directory per repo holds both public and private records, with private kept out of git by a fail-closed .gitignore — no more split between a repo store and a global $HOME store

## Audience

- eidetic operators + the agents that drive it (Claude, colleague) who run remember/recall inside a project git repo; plus the two consumer contracts (jetson-ai-lab-cli #3, the research flow #1)

## Before → After

- Before: 0.10.x split storage by visibility AND cwd across TWO dirs: public-in-repo went to <repo>/.eidetic/memory, private (or out-of-repo) went to $HOME/.eidetic/memory; recall/sweep had to union+dedup across both stores (the _candidate_read_dirs/seen machinery), which is the surface Qodo finding #1 was about
- After: a single store at <repo-root>/.eidetic/memory holds BOTH public and private records; reads hit exactly one directory (no union, no cross-store dedup); private shards are gitignored so they never get committed; $HOME is used ONLY as a fallback outside any git repo; EIDETIC_DATA_DIR/--data-dir override still wins and short-circuits to that one dir

## Why it matters

- less complexity and more containment: a project's memory (public AND private) lives with the project instead of a global cross-project $HOME soup; the two-store merge/dedup code (and its whole class of ordering bugs) is deleted; the no-leak reasoning simplifies to one store still gated by can_serve

## Requirements

- store resolution collapses to a single dir: _resolve_write_dir(visibility) returns the repo store for BOTH visibilities when inside a git repo (else $HOME); _candidate_read_dirs() returns exactly that one dir; search()/all() drop the union-by-id/seen-dedup loops and read one store
  - honesty: search()/all() for the files backend read exactly one resolved dir; the test suite's former two-store tests are rewritten to the single-store model and pass
- the EIDETIC_DATA_DIR/--data-dir override path stays byte-identical to today (single dir, no gitignore written there since the operator chose that location explicitly)
  - honesty: with EIDETIC_DATA_DIR set, behavior is unchanged vs 0.10.x (single dir, no gitignore written)
- no-leak invariant still holds with one store: can_serve gates every read (a public-scope recall never returns a private record), now trivially since there is a single candidate set; defensive can_serve is retained
  - honesty: a public-scope recall over a single store that contains a private record returns nothing private; eidetic's defensive can_serve is retained on the candidate set
- existing $HOME private records are a clean break: documented in CHANGELOG/README, not auto-migrated; out-of-repo behavior is unchanged ($HOME), so nothing breaks for wheel/no-repo installs
  - honesty: CHANGELOG+README state the clean break for existing $HOME private records; out-of-repo (no git) behavior still resolves to $HOME so wheel/no-repo installs are unaffected
- the private-in-repo cutover ships ATOMICALLY with the data-refinery dep bump that provides the gitignore-on-materialize behavior — never before — so private shards are git-ignored from their first write (no leak window); if that DR release is not yet available, eidetic's cutover is blocked on it
  - honesty: a test (or the dep floor itself) guarantees the gitignore exists before/when the first private shard is written in a repo, so 'git check-ignore' reports the private shard ignored from the very first private remember

## Honesty conditions

- inside a repo, a private remember produces a git-ignored <scope>__private.jsonl (git status stays clean) while a public remember produces a tracked <scope>__public.jsonl
- the consumer contracts (#3 public-only; #1 research flow) are unaffected because they operate on public records, which still travel with the repo
- the 0.10.x two-store union/dedup code (_candidate_read_dirs returning 2 dirs, seen-dedup in search()/all()) is the exact code being removed
- with EIDETIC_DATA_DIR unset and cwd inside a git repo, both _resolve_write_dir('public') and _resolve_write_dir('private') return <repo>/.eidetic/memory and _candidate_read_dirs() returns exactly [that one dir]
- after the change a grep finds no surviving cross-store merge/dedup loop, and the no-leak tests still pass with a single store
- the override (EIDETIC_DATA_DIR/--data-dir) path yields byte-identical store layout+contents to 0.10.x, and no .gitignore is written into an explicit override dir
- a hermetic regression test (HOME+cwd isolated) proves: private-in-repo -> gitignored file + clean git status; public-in-repo -> tracked file; a fresh worktree sees public but not private
- a test asserts the written .gitignore contents are exactly the whitelist (ignore '*', un-ignore '.gitignore' and '*__public.jsonl') and that a private shard created afterward is reported ignored by 'git check-ignore'
- after the change, eidetic contains no open()/write to disk for the store (grep confirms); the .gitignore appears in a repo store dir because the data-refinery version eidetic depends on writes it on materialization

## Success signals

- in a git repo, a private remember writes only to <repo>/.eidetic/memory/<scope>__private.jsonl and that file is git-ignored (git status clean); a public remember writes <scope>__public.jsonl which IS tracked; recall/sweep read one dir; a fresh clone/worktree sees public records but not private; full suite + lint + teken rubric green; bandit/Sonar clean (or a justified, documented exception) for the gitignore write

## Scope / boundaries

- NOT changing: the record schema, ranking modes, scoring/freshness, can_serve no-leak semantics, the mongo/neo4j network backends, or the EIDETIC_DATA_DIR/--data-dir override path. NOT building a migrator for existing $HOME private records (clean break, documented). NOT keeping any private data in $HOME when inside a repo.

## Non-goals

- not preserving Claude<->colleague PRIVATE-record sharing — that is consciously dropped; colleague (throwaway worktree) sees committed public records only

## Decisions

- the .gitignore is fail-closed (whitelist): contents ignore '*' then un-ignore '.gitignore' and '*__public.jsonl', so any future private/sidecar filename DR introduces is excluded by default rather than leaked
- data-refinery writes the fail-closed .gitignore when it materializes a repo store dir (it owns the on-disk layout); eidetic constructs NO filesystem write path and carries no S2083 sink — consistent with the #8/#15 boundary. eidetic consumes it by raising its data-refinery-cli version floor; a brief is filed to data-refinery-cli.

## Hard questions

- risk: this depends on a not-yet-released data-refinery feature; until DR ships gitignore-on-materialize, eidetic cannot safely flip private-in-repo (leak risk) — sequence: file DR brief -> DR releases -> eidetic bumps floor + cuts over. (The earlier "eidetic writes the gitignore" S2083 risk was dropped when decision c9 was rejected in favor of data-refinery owning the write — eidetic keeps no filesystem write sink.)

## Open / follow-up

- recall's passive reinforcement of a PUBLIC record still writes to the committed store (dirties the tree) — unchanged by this work; private reinforcement is now gitignored so no longer dirties. Stays tracked in issue #24.
- a future 'eidetic migrate' helper to lift existing $HOME private records into the per-repo store — deliberately out of scope (clean break chosen), revisit if users ask.
