# Build Plan — eidetic-cli now remembers like a mind: a one-shot migration absorbs the entire old ~/.claude QQ memory (markdown notes, MongoDB RAG, Neo4j graph) into eidetic; every fact carries a dated, decaying signal that weakens with age but strengthens each time it is recalled-and-validated or corroborated by a newly remembered connected fact; and nothing is ever hard-deleted — a stronger conflicting fact shadows a weaker older one, and memories past a year archive themselves, everything still recoverable.

slug: `eidetic-cli-now-remembers-like-a-mind-a-one-shot-m` · status: `exported` · from frame: `eidetic-cli-now-remembers-like-a-mind-a-one-shot-m`

> eidetic-cli now remembers like a mind: a one-shot migration absorbs the entire old ~/.claude QQ memory (markdown notes, MongoDB RAG, Neo4j graph) into eidetic; every fact carries a dated, decaying signal that weakens with age but strengthens each time it is recalled, or corroborated by a newly remembered connected fact; and nothing is ever hard-deleted — a stronger conflicting fact shadows a weaker older one, and memories past a year archive themselves, everything still recoverable.

## Tasks

### t1 — L1 schema foundation (record.py): extend Record with created-date, last_recall + recall_count, related-memory links, a computed signal field, lifecycle status (active/shadowed/archived), and a supersedes link; serialisation round-trips and legacy records without the fields load with safe defaults.

- covers: c4, h7, h1
- acceptance:
  - Record.to_dict/from_dict round-trip all new fields
  - a legacy record lacking the fields loads with defaults: date-unknown sentinel, neutral signal, empty links, lifecycle=active

### t2 — L2 signal function (scoring.py): deterministic, query-time-recomputable signal strength = near-linear (base - staleness + access_bonus) * age_factor from stored fields, with tunable decay_rate and passive recall reinforcement; blend signal into all four ranking modes.

- depends on: t1
- covers: c9, h3
- acceptance:
  - signal(record, now) is pure and deterministic from created/last_recall/recall_count/links
  - an older record scores below an identical fresher one; decay_rate is a parameter; no mutable cached score is persisted

### t3 — Recall surface (recall.py): add --include-shadowed and --include-archived (default excludes both), expose signal/freshness in recall output, and trigger passive reinforcement (bump last_recall + recall_count) on each hit.

- depends on: t2
- covers: c3, h6, c7, h10, h4
- acceptance:
  - default recall omits shadowed and archived records; the two flags return them
  - recall JSON exposes signal alongside score; a fresh fact outranks an equal-lexical-match year-old one; recalling a record increments recall_count

### t4 — Remember/supersedes (remember.py): stamp a creation date on ingest, accept an explicit supersedes link and related-memory links, and carry them into the stored record.

- depends on: t1
- covers: c8
- acceptance:
  - ingesting stamps created date when absent and persists supersedes + links; re-ingest by id stays idempotent

### t5 — L3 lifecycle engine (new memory/lifecycle.py + sweep command): within-scope hybrid conflict shadowing (explicit supersedes authoritative, assisted suggestions never auto-shadow), archival when age>1yr OR signal<threshold, core/protected exempt, never a hard delete.

- depends on: t2
- covers: c10, h4
- acceptance:
  - an explicit supersedes link shadows the older same-scope record; cross-scope never interacts
  - a record older than 1yr OR below signal threshold is marked archived not deleted; core/protected exempt; no destructive-delete path exists

### t6 — L1 migration importer (new migrate noun group + memory/migrate_qq.py): ingest core.md/notes.md, Mongo claude_notes, and Neo4j claude-context graph as idempotent eidetic upserts with provenance + date signature (cascade last_accessed/last_seen -> mtime -> date-unknown), relationships as record links; a down backend is skipped with a warning.

- depends on: t1, t4
- covers: c8, h2, c5, h8
- acceptance:
  - re-running the migration updates in place and never duplicates (idempotent by id/hash)
  - with Mongo or Neo4j down, that layer is skipped with a warning and the run completes; migrated records carry provenance + date signature

### t7 — Boundary/contract guard tests (tests/test_boundaries.py): assert no destructive delete exists, the subprocess/JSON contract and public-only rule are unchanged, and no autonomous extraction/summarisation or UI is added.

- depends on: t3, t5
- covers: c6, h9
- acceptance:
  - a test fails if any hard-delete path exists in the tree
  - tests assert the recall/remember JSON contract and public-only scope rule are unchanged

### t8 — Shared-store/audience integration test (tests/test_shared_store.py): prove one shared ~/.eidetic/memory store is served over the subprocess/JSON boundary to all agents with no per-agent fork.

- depends on: t6
- covers: c2, h5
- acceptance:
  - a record remembered via one CLI invocation is recalled by a separate invocation from the same shared store; no per-agent store path is created

### t9 — End-to-end coherence + success-signal suite (tests/test_e2e_memory.py): runnable checks that the three layers form one coherent surface — migrated records rank immediately, L2 signal is exactly what L3 thresholds on — and every spec success signal passes.

- depends on: t3, t5, t6
- covers: c1, h1, c7, h10
- acceptance:
  - a migrated-topic recall returns old facts with provenance
  - a higher-signal contradiction hides the old one from default recall but --include-shadowed returns it; a >1yr record drops from default but --include-archived returns it

### t10 — Docs + skills surface update: refresh README.md and CLAUDE.md to describe the freshness signal, the no-delete/shadow/archive lifecycle, and the QQ migration verb (CLAUDE.md must stop saying the memory surface is unbuilt); update the first-party remember + recall skills to document --include-shadowed/--include-archived, supersedes/links on ingest, and signal in recall output; ensure the in-CLI learn/overview/explain catalogs list the new migrate and lifecycle/sweep verbs.

- depends on: t3, t5, t6
- covers: c6, h9
- acceptance:
  - README.md and CLAUDE.md describe freshness + lifecycle + migration; CLAUDE.md no longer states the memory surface is not yet built
  - remember and recall skills document the new flags, supersedes/links, and signal-in-output
  - eidetic learn/overview/explain list the migrate and sweep verbs and teken cli doctor . --strict stays green

## Risks

- [unknown_nonblocking] Link-corroboration boost: how much a newly remembered connected fact raises an older linked fact's signal, and whether it propagates across links, is undecided (frame v7). Ship signal with a tunable/placeholder corroboration term initially. (task t2)
- [unknown_nonblocking] How assisted conflict suggestions surface to the operator (output channel, confirmation UX) is undecided. (task t5)
- [unknown_nonblocking] Whether the old Mongo/Neo4j backends are reachable at migration time is environment-dependent; handled by skip-with-warning but may yield a partial import. (task t6)
