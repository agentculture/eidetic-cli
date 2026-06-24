# Build Plan — eidetic-cli's default memory store becomes project-local: a PUBLIC record written inside a git repo lands in that repo's committed store (<repo-root>/.eidetic/memory), while PRIVATE records and any record written outside a git repo fall back to $HOME/.eidetic/memory.

slug: `eidetic-cli-s-default-memory-store-becomes-project` · status: `exported` · from frame: `eidetic-cli-s-default-memory-store-becomes-project`

> eidetic-cli's default memory store becomes project-local: a PUBLIC record written inside a git repo lands in that repo's committed store (<repo-root>/.eidetic/memory), while PRIVATE records and any record written outside a git repo fall back to $HOME/.eidetic/memory.

## Tasks

### t1 — Add visibility-aware store-path resolver helpers to eidetic/memory/backend.py

- covers: c1, c4, h1, h4
- acceptance:
  - New _git_toplevel() returns 'git rev-parse --show-toplevel' when CWD is inside a git repo, and None when outside a repo or git is unavailable (no traceback, no CliError leak)
  - _resolve_write_dir('public') returns <git-toplevel>/.eidetic/memory inside a repo; private (any visibility) and any visibility outside a repo return $HOME/.eidetic/memory
  - When EIDETIC_DATA_DIR is set it wins: _resolve_write_dir(any visibility) and the read-dir resolver both return exactly that dir (single dir, byte-identical to today)
  - _candidate_read_dirs() returns a deduped list = [$HOME] union ([git-toplevel] if in a repo), or [override] when EIDETIC_DATA_DIR set; no duplicate when toplevel==home
  - Resolution is CWD-based (process working dir), NOT module-based; find_culture_yaml is not called; git-toplevel is resolved at most once per process (cached) so a batch ingest of N records spawns no more than one git subprocess
  - tests/test_store_resolver.py unit-covers: in-repo, subdir-of-repo, outside-repo, git-absent, EIDETIC_DATA_DIR-set

### t2 — Wire upsert/search/all in backend.py onto the resolver: write-routing, multi-store read, sweep enumeration (files-only)

- depends on: t1
- covers: c2, c3, c5, c7, h2, h3, h5
- acceptance:
  - files-backend upsert writes a public record into _resolve_write_dir('public') and a private record into _resolve_write_dir('private'), verified by which <scope>__<vis>.jsonl appears in which dir
  - files-backend search gathers candidates from every dir in _candidate_read_dirs(), unions by id (no duplicate records), then applies the EXISTING can_serve + filters + rank unchanged; a private-scope query returns its own private records (from $HOME) PLUS public records (from the repo dir)
  - a public-scope query never returns private records even though the home dir is read (can_serve fails closed); the public/private no-leak invariant still holds
  - files-backend all() enumerates across all _candidate_read_dirs(); sweep's re-upsert of a changed record lands it back in the dir matching that record's own visibility
  - all new dir logic is gated behind self._name=='files'; mongo and neo4j _bridge_env branches and single-store reads are untouched; the old hardcoded Path.home()/.eidetic/memory (backend.py:112-114) is the only path logic replaced

### t3 — Add end-to-end + regression tests for the two-store routing across the CLI

- depends on: t2
- covers: c6, h6, h7
- acceptance:
  - e2e: 'remember --visibility public' with CWD inside a temp git repo writes under <repo>/.eidetic/memory and a same-repo recall finds it
  - 'remember --visibility private' writes under $HOME/.eidetic/memory (never the repo dir); a default (private) recall in the repo returns both the private record and the repo's public records
  - with EIDETIC_DATA_DIR set, remember+recall touch only that dir and the on-disk result is byte-identical to pre-change behavior (override regression locked)
  - run outside any git repo, remember/recall use $HOME/.eidetic/memory; and a pre-populated $HOME store is neither moved nor modified by running in a repo (clean break)
  - sweep over a store split across the repo dir and $HOME transitions records in both and writes each back to its origin dir; tests assert mongo/neo4j paths are unaffected

### t4 — Update this repo's docs to the visibility-aware routing (wrapper headers, SKILL.md, CLAUDE.md, catalog/migrate help)

- depends on: t2
- covers: c5, c6
- acceptance:
  - remember.sh & recall.sh header comments no longer claim the store is unconditionally '$HOME/.eidetic/memory ... OUTSIDE any git worktree'; they describe public->repo / private->$HOME / override-wins routing (no functional wrapper change)
  - the /remember and /recall SKILL.md description fields are updated to the new routing and markdownlint passes
  - CLAUDE.md storage section + eidetic/explain/catalog.py + eidetic/cli/_commands/migrate.py help text describe the new default resolution and that explicit overrides still win
  - 'teken cli doctor . --strict' stays green (rubric: learn/overview/explain still satisfied)

### t5 — Bump version and changelog; verify CI gates

- depends on: t3, t4
- covers: c6
- acceptance:
  - pyproject.toml version is bumped (minor) above main and CHANGELOG.md gets a Keep-a-Changelog entry for the project-local store default
  - version-check passes (version != main); full lint suite (black/isort/flake8/bandit/markdownlint) and 'uv run pytest -n auto' are green

## Risks

- [follow_up] 'migrate store' with no --data-dir migrates only $HOME; auto-iterating both the repo dir and $HOME in one run is deferred — pass --data-dir to upgrade the repo store explicitly. (task t4)
- [follow_up] Cross-org byte-verbatim re-sync of the updated remember/recall wrapper docs across ~57 repos is a separate rollout-cli job, not part of this PR.
- [unknown_nonblocking] Service-style consumers (#3 jetson-ai-lab-cli) that run under varying CWDs and want one stable store must set EIDETIC_DATA_DIR; the default now follows CWD. Covered by the override (h1) but worth a doc note. (task t4)
- [follow_up] Committing *__public.jsonl produces a git diff per public remember and can merge-conflict across teammates; data-refinery's atomic-per-file append format should be validated for merge-friendliness.
