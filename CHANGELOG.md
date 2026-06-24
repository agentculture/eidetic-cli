# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.2] - 2026-06-24

### Added

- Design spec for the fully repo-contained memory store: a single `<repo-root>/.eidetic/memory` holds both public and private records (collapsing the 0.10.x two-store model), with private kept out of git by a fail-closed `.gitignore` that data-refinery writes on store-dir materialization. NO behavior change yet — this ships the converged `/think` spec only (`docs/specs/2026-06-24-eidetic-s-memory-store-is-now-fully-repo-contained.md`); implementation is tracked in #25 and blocked on data-refinery-cli#12.

## [0.10.1] - 2026-06-24

### Fixed

- StoreBackend.search() now applies can_serve BEFORE cross-store id-dedup, so a non-serveable private copy can no longer occupy an id slot and hide a serveable public copy of the same id (Qodo PR #23 finding 1; defense-in-depth — not exploitable through data_refinery.store.list(), which pre-filters).
- _git_toplevel() truly fails closed: os.getcwd() is now inside the try and the subprocess guard catches OSError (not just FileNotFoundError), honoring its "Never raises" contract before every files-backend op (Qodo PR #23 finding 2).

## [0.10.0] - 2026-06-24

### Added

- Project-local default for the files-backend memory store: a PUBLIC record written inside a git repo is stored in (and committed to) `<repo-root>/.eidetic/memory` (team-shared), instead of always using a single global $HOME store. The public/private no-leak invariant is preserved.

### Changed

- Files-backend store path now resolves per-operation by visibility and cwd: PUBLIC + inside a git repo -> `<repo-root>/.eidetic/memory`; PRIVATE, or any record outside a git repo -> $HOME/.eidetic/memory (never committed). recall and sweep read and merge across both stores. Precedence: explicit --data-dir / EIDETIC_DATA_DIR > public-in-repo > $HOME; setting EIDETIC_DATA_DIR keeps the prior single-directory behavior byte-for-byte. mongo/neo4j backends are unaffected.

## [0.9.3] - 2026-06-23

### Changed

- Scope resolution no longer silently downgrades an expected-private record to the public default scope: when no culture.yaml suffix resolves *and* the caller passed neither --scope nor --visibility, a single accurate warning is written to stderr (stdout stays clean for --json); passing either flag is a deliberate choice, honored verbatim, and silences the warning (FIX-5).
- recall.sh: the bareword query `help` is now a real search term (only -h/--help print usage), and a missing query is a hint:+non-zero error instead of exiting 0 (FIX-6).

### Fixed

- remember/recall wrappers: resolve_eidetic no longer implies an unreachable uv-checkout fallback in vendored copies — the not-found path prints one honest, single-line `hint:` to install the CLI (FIX-1, FIX-7).
- remember.sh no longer blocks forever on an interactive no-arg invocation: with no args and a TTY stdin it prints usage and exits non-zero; the piped NDJSON batch path is unchanged (FIX-2).
- SKILL.md (both skills) + skill descriptions: store path written as $HOME/.eidetic/memory instead of ~/.eidetic/memory so downstream cicd portability-lint no longer fails (FIX-3).
- remember.sh --help no longer claims "Public data only." — it now states the private-by-default scope and how --visibility public overrides it (FIX-4).
- resolve_scope suffix parse hardened against set -o pipefail (head closing the pipe could SIGPIPE sed and abort the script) (FIX-8).

## [0.9.2] - 2026-06-23

### Changed

- remember/recall skill wrappers now default --scope to this agent's mesh identity (culture.yaml suffix, e.g. eidetic-cli) paired with --visibility private, so a record this agent remembers lands in its own isolated personal scope instead of the global default/public scope. An explicit --scope steers elsewhere; --visibility public keeps the personal scope but shares it; a wheel install with no culture.yaml falls back to the CLI default. CLI contract is unchanged (skill-wrapper defaults only).

## [0.9.1] - 2026-06-23

### Fixed

- migrate qq: duplicate ## headings in one file (e.g. two "## Ongoing Threads") no longer slug-collide on the same id and silently overwrite each other. The first occurrence keeps the bare slug; later ones get a deterministic -2/-3 suffix, so every section is preserved as a distinct record. Recovered a 128 KB section that was previously dropped on import.

## [0.9.0] - 2026-06-22

### Added

- `--backend` token is now uniform across every verb (`remember`/`recall`/`sweep`/`migrate`/`overview`): `files`, `mongo`, `neo4j`, or `graph`, where `graph` is an alias for `neo4j` (issue #12). A single backend token works everywhere.

### Changed

- `migrate store` now delegates the on-disk format upgrade to data-refinery's `store.migrate` endpoint (data-refinery-cli 0.6.x) — eidetic constructs no filesystem write path and carries no `pythonsecurity:S2083` sink (issue #8). Its `--json` report adopts data-refinery's file-granularity summary `{backend, files, migrated, migrated_files, skipped, dry_run}`.
- Bumped the storage dependency pin to `data-refinery-cli[store]>=0.6,<0.7`.

### Removed

- Deleted `eidetic/memory/migrate_store.py` and its path-construction logic; the store rewrite now lives behind data-refinery's boundary.

## [0.8.0] - 2026-06-21

### Added

- `migrate store` — one-shot, idempotent in-place upgrade of an existing store from legacy Record JSONL to data-refinery Envelope JSONL (#13).

### Changed

- Storage is now owned by the sibling data-refinery-cli (contract v2): eidetic depends on `data-refinery-cli[store]` and imports `data_refinery.store` / `data_refinery.quality` for the opaque KV + data-quality layer instead of shipping its own files/mongo/neo4j backends. Memory semantics — record schema, the four recall modes, scoring, freshness signal, lifecycle — stay in eidetic (#13).
- Runtime dependencies dropped direct `neo4j` + `pymongo`; now declare `data-refinery-cli[store]>=0.5.2,<0.6` (the extra provides those drivers transitively).
- The mongo/neo4j stack is no longer owned here: bring it up with `data-refinery stack up` / the `ghcr.io/agentculture/data-refinery-stack` image. Removed eidetic`'`s own `docker-compose.yml`.

### Security

- `migrate store` now canonicalises the resolved store directory and asserts each per-file temp path stays within it before writing, making the trust boundary explicit on the JSONL-rewrite path (clears the SonarCloud path-construction finding on #13).

## [0.7.1] - 2026-06-20

### Fixed

- `overview` no longer crashes when a record carries a non-string `metadata.author`: `compute_stats` now only unions non-empty strings into the per-scope contributor set, so `sorted()` cannot raise `TypeError` on mixed types (found by colleague review on #10; preserves the always-on overview never-fail invariant)

## [0.7.0] - 2026-06-20

### Added

- First-class `added_by` attribution field on every record envelope, so two agents writing into the same shared scope are distinguishable (issue #8)
- `remember --added-by <nick>` flag; `added_by` is auto-stamped at ingest when absent (resolution: record JSON / `--added-by` > agent mesh nick from culture.yaml > None), and explicit values are preserved verbatim
- `overview --store` now reports distinct contributors per scope (union of each record `added_by` and legacy `metadata.author`)
- Neo4j backend round-trips `added_by`; CI pipeline now runs a live neo4j service so the round-trip is proven, not skipped

### Changed

- `learn` and `explain remember`/`explain overview` now teach the `added_by` field, the `--added-by` flag, and the overview contributors line

## [0.6.0] - 2026-06-20

### Added

- `overview` now reports a live **Store** section on every call, covering all stores: per-backend record counts + live/unavailable status (files/mongo/graph), per-scope name+visibility+lifecycle breakdown, and link-connections (counted link/supersedes references, not graph edges). Narrow with `--backend {files,mongo,graph}` or `--scope NAME`. A down backend degrades to an `unavailable` line via a fast status probe (tunable via `EIDETIC_STORE_PROBE_TIMEOUT_MS`) and overview still exits 0. New pure aggregator `eidetic.memory.stats`; backends gained an optional `timeout_ms` for the probe path.

### Fixed

- `remember`: a record carrying an inline `scope` but no `hash`/`metadata` no longer raises `KeyError` — the inline-scope path now applies the same optional-field defaults as the flag-scope path (hash derived from text, metadata defaults to `{}`), restoring the documented optional-`hash`/`metadata` contract and unblocking the batch NDJSON ingest path.

## [0.5.0] - 2026-06-20

### Added

- migrate qq — one-shot, idempotent import of the legacy ~/.claude QQ memory (core.md/notes.md markdown, Mongo claude_notes, Neo4j claude-context graph) into eidetic as upserts carrying provenance + a date signature; a down Mongo/Neo4j is skipped with a warning, not fatal; defaults to a PRIVATE qq scope so personal data never leaks into public recall (L1)
- Freshness signal — every record now carries created, last_recall, recall_count, related-memory links, and a deterministic signal strength (near-linear age-decay + passive recall reinforcement: every recall counts as validation, no cooldown); signal is query-time-recomputable and blends into all four recall ranking modes (L2)
- sweep command + eidetic/memory/lifecycle.py — applies never-delete lifecycle transitions across the store: within-scope-only supersedes shadowing (explicit link authoritative; cross-scope never interacts, preserving the no-leak invariant) and archival when a record is older than ~1 year OR its signal falls below threshold; core/protected records are exempt; conflict suggestions are returned for confirmation, never auto-applied (L3)
- recall --include-shadowed / --include-archived — default recall excludes shadowed and archived records; the flags return them (nothing is ever hard-deleted, everything stays recoverable)
- Record schema extended (created, last_recall, recall_count, links, supersedes, lifecycle, signal) with back-compatible from_dict defaults so legacy records load unchanged
- Backend.all() enumeration on the files/mongo/neo4j backends, powering the lifecycle sweep
- Boundary/contract, shared-store, and three-layer-coherence e2e test suites covering the spec success-signals

### Changed

- remember now stamps a created date on ingest and accepts supersedes + related-memory links on each record
- recall output now exposes the freshness signal alongside the lexical score, and recalling a record passively reinforces it (bumps last_recall + recall_count)
- README.md and CLAUDE.md document the built memory surface (freshness signal, no-delete shadow/archive lifecycle, QQ migration); CLAUDE.md no longer states the memory surface is unbuilt

### Fixed

- Recall reinforcement no longer persists query-time fields: the bumped copy now clears score and signal before upsert, so the recall-output-only score never leaks into the store and mongo/neo4j skip redundant per-hit re-embedding (qodo review)
- Neo4j backend now persists and reloads the temporal/lifecycle fields (created, last_recall, recall_count, links, supersedes, lifecycle); previously they were dropped on --backend neo4j so recall reinforcement and sweep could not round-trip there (qodo review)
- Record.links is normalised to a list at construction, so JSON carrying "links": null (or a non-list) no longer crashes signal scoring's len(record.links) during recall/sweep (qodo review)

## [0.4.0] - 2026-06-19

### Added

- recall --mode {exact,approximate,keyword,hybrid} — four search modes (default hybrid); exact=case-insensitive substring (--case-sensitive), approximate=vector cosine, keyword=BM25, hybrid=weighted alpha blend (--alpha, default 0.5) that degrades to keyword-only when embeddings are offline
- eidetic/memory/scoring.py — one shared per-mode ranker every backend calls, so all four modes behave identically across the files/mongo/neo4j backends
- First-party remember / recall skills (.claude/skills/) wrapping the CLI over a shared ~/.eidetic/memory store, usable by both Claude and the colleague backend
- EmbedClient.embed_detect() reports remote vs offline-fallback; EIDETIC_EMBED_MODEL env selects the embedding model

### Changed

- recall ranking now recomputes embeddings at query time (uniform across backends); mongo no longer silently drops records without a stored embedding
- EmbedClient rerank reads relevance_score (vLLM/Jina/Cohere) with score fallback

### Fixed

- `remember` now requires `type` on every record (alongside `id` and `text`), matching the documented ingest contract; the skill docs/wrapper and `explain remember` no longer call `type` optional (qodo review)
- `eidetic/memory/scoring.py` — extracted `_bm25_doc_score` from `_bm25_scores` to drop the per-document loop's cognitive complexity below threshold; ranking output is unchanged (SonarCloud S3776)

## [0.3.0] - 2026-06-19

### Added

- Memory surface: `remember` (idempotent upsert by id; one JSON object or NDJSON-on-stdin) and `recall` (top-k hits, each with text + full metadata + score; scope-aware) verbs
- Pluggable storage backends selectable via --backend: files (zero-dep default, JSONL), Neo4j, and Mongo
- Per-scope public/private visibility with a load-bearing no-private-to-public-leak invariant
- model-gear / OpenAI-compatible embeddings + rerank client over stdlib HTTP with a deterministic offline lexical fallback
- `docker-compose.yml` — eidetic owns its own Neo4j + Mongo stores (distinct ports; the memory layer itself, not reliant on any other project's running stack)

### Changed

- Neo4j and Mongo are now required runtime dependencies (neo4j, pymongo); the runtime is no longer zero-dependency — consumers stay dependency-free via the subprocess boundary

### Fixed

- `recall --backend mongo` no longer raises on a record stored without an embedding (it is skipped)
- `remember` rejects a malformed `scope` field with a clean `CliError` instead of an unexpected error

## [0.2.2] - 2026-06-19

### Changed

- CLAUDE.md — added a **Planned domain: the memory surface** section that orients a future instance toward the not-yet-built `remember`/`recall` memory layer, sourced from the two open contracts: issue #3 (the `jetson-ai-lab-cli` consumer — CLI subprocess, JSON in/out, NDJSON-stdin batch ingest, mandatory provenance on recall, idempotent upsert) and issue #1 (eidetic as the durable end of the `arxivist → tensor-cli → reduce-cli → prove-cli → eidetic-cli` research flow). Flags the zero-dep property's first real decision point: model-gear `/v1/embeddings`+reranker over HTTP vs. a lazy-imported vector store, with `data-refinery` (neo4j + mongo) as the candidate backing store.
- CLAUDE.md — added a **Contract shapes** subsection distilling #1's three deliverables (object schema, ingest contract, retrieval contract) into a written shape: the `MemoryRecord` envelope (`id`/`text`/`type`/`hash`/`metadata`/`score` with required-vs-recommended notes), the consumer (`discord`/`docs`) and research (`PaperMemory`/`ClaimMemory`/`LemmaMemory`/`ProofMemory`/…) object types and their claim-graph relationships, the `eidetic remember` ingest contract (single-JSON-or-NDStdin, required fields, idempotent `id`/`hash` upsert, public-only), and the `eidetic recall` retrieval contract (query + facet filters spanning `source`/`channel`/time and `paper`/`topic`/`claim`/`lemma`/`method`/`author`/`task`, mandatory provenance in results). Addresses qodo review comments on PR #4 (object schemas / ingest contract / retrieval facets). Kept explicitly "proposed, not frozen" — the issues stay authoritative.

### Fixed

- README.md quickstart commands — `uv run eidetic-cli whoami`/`learn` did not resolve to a script (the console script is `eidetic`, not `eidetic-cli`); corrected to `uv run eidetic …`, matching the gotcha already documented in CLAUDE.md.
- README.md rename count — `~100 places` corrected to `~30 files`, consistent with CLAUDE.md's `git grep -lI` figure (the authoritative rename procedure the README points to).

## [0.2.1] - 2026-06-13

### Changed

- Replaced the CLAUDE.md seed placeholder with a real runtime guide for eidetic-cli: commands (test/lint/rubric gate), the agent-first CLI architecture (register-per-command, the CliError/exit-code contract, stdout/stderr split, the explain catalog), the mesh-identity pairing, the version-bump-every-PR and rubric-gate rules, cite-don't-import skill provenance, the cicd PR lane, and the template rename procedure. Documents the eidetic vs eidetic-cli console-script naming gotcha.

### Fixed

- `explain eidetic` now resolves (added an `("eidetic",)` alias to the explain catalog pointing at the root entry). The agent-first rubric (`teken cli doctor . --strict`) probes the tool by its console-script/package name `eidetic`, but the catalog only carried the `eidetic-cli` self-key, so `explain_self` failed and the lint job went red. Surfaced by CI on this PR; the gap pre-dated it.

## [0.2.0] - 2026-06-06

### Added

- **`ask-colleague` skill** (`.claude/skills/ask-colleague/`) — the first-party front door to the `colleague` CLI (the renamed `convertible`). On top of `explore` / `review` / `write` it adds a `feedback` verb (grade a finished work item — the ROI loop), and `write` now **previews by default** in a throwaway worktree (no side effects) unless `--apply` / `--pr` is given. Reach for it reflexively — `review` for a diverse second opinion on a committed diff before opening a PR, `explore` for a fresh read of an unfamiliar area.

### Changed

- **Replaced the `outsource` skill with `ask-colleague`.** `outsource` was renamed to `ask-colleague` upstream ([colleague#148](https://github.com/agentculture/colleague/pull/148)). Because guildmaster has not re-broadcast the rename yet (its kit still ships the old `outsource`), `ask-colleague` is vendored **directly from the sibling `colleague` checkout** rather than from guildmaster — a tracked local divergence recorded in `docs/skill-sources.md`, parallel to the `agex` → `devex` one. Vendored verbatim except one consumer-identifying clause in the Provenance paragraph.
- **Ledger + CLAUDE.md + `.gitignore`:** point `docs/skill-sources.md` and the CLAUDE.md Skills section at `colleague` / `ask-colleague`, swap the *optional* runtime prerequisite `convertible` → `colleague` (env prefix `CONVERTIBLE_*` → `COLLEAGUE_*`, with the legacy names kept as a deprecated fallback), and gitignore the `.colleague/` run-artifact dir the skill writes (plus the stale `.agex/`).

## [0.1.4] - 2026-05-31

### Added

- **Vendor the `outsource` skill** (`.claude/skills/outsource/`) from
  guildmaster's canonical copy (origin
  [`agentculture/convertible`](https://github.com/agentculture/convertible),
  re-broadcast via guildmaster — guildmaster
  [#51](https://github.com/agentculture/guildmaster/pull/51)). Every agent
  cloned from this template now inherits the ability to hand a scoped task to a
  *different* engine/mind: `explore` (read-only investigation), `review` (a
  diverse second opinion on the committed diff), and `write` (delegate a small
  implementation). `explore`/`review` run isolated in a throwaway `git worktree`;
  `write` refuses a dirty tree. Fulfils
  [#8](https://github.com/agentculture/eidetic-cli/issues/8).
- **Ledger + CLAUDE.md:** record `outsource` in `docs/skill-sources.md`
  (origin = convertible, re-broadcast via guildmaster; vendored verbatim — it
  already carries `type: command`) and document its *optional* runtime
  dependency on the `convertible` CLI (the skill exits with an install hint if
  absent, so a clone that never uses it is unaffected).

### Changed

### Fixed

## [0.1.3] - 2026-05-31

### Changed

- Expanded the clone-and-rename instructions in `CLAUDE.md`: added `README.md` to
  the rename targets and a portable `git grep` discovery command so a cloner can
  find every occurrence of the template name (hard-coded in ~100 places across the
  package, including the CLI command files and `_ISSUES_URL` in
  `eidetic/cli/__init__.py`) rather than renaming by hand.
- Synced `README.md`'s "Make it your own" checklist with `CLAUDE.md`: it now lists
  `README.md` itself as a rename target and points to `CLAUDE.md`'s discovery
  command as the authoritative procedure, so the two onboarding checklists no
  longer drift.

## [0.1.2] - 2026-05-30

### Changed

- Renamed the PR-lifecycle CLI references `agex` / `agex-cli` to `devex` (same
  tool, new name) across `CLAUDE.md`, `docs/skill-sources.md`, `.gitignore`, and
  the vendored `cicd`, `assign-to-workforce`, and `communicate` skills — the
  `cicd` scripts now invoke `devex pr`.
- Logged the vendored-skill in-place patch as a local divergence in
  `docs/skill-sources.md`; the matching canonical rename is tracked upstream for
  guildmaster in
  [agentculture/guildmaster#48](https://github.com/agentculture/guildmaster/issues/48)
  so a future re-sync reconciles cleanly.
- Aligned the documented `devex` version floor to `>=0.21` across the vendored
  `cicd` `SKILL.md` and `workflow.sh` install hint (were `>=0.1`), matching
  `docs/skill-sources.md` and the `await`-era feature set; flagged upstream on
  guildmaster#48.

### Fixed

- SonarCloud now reports code coverage — added `relative_files = true` to
  `[tool.coverage.run]` so `coverage.xml` emits repo-relative paths that map to
  `sonar.sources=eidetic` (absolute / `.venv` paths were dropped
  as unmappable). Mirrors the sibling `convertible` setup.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/eidetic-cli/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/eidetic-cli/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: eidetic-cli`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
