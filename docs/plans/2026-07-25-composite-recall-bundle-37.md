# Build Plan — composite recall bundle (#37)

slug: `composite-recall-bundle-37` · status: `exported` · from frame: `composite-recall-bundle-37`

> eidetic recall now returns a composite bundle by default: matching records, their verbatim text identified by record id, a bounded links/supersedes neighborhood traversal, and the text of traversed neighbors - every item tier-labeled and cited by record id, filtered per hop by can_serve, with graded reinforcement (full bump for primary hits, decaying by hop distance for traversal discoveries)

## Tasks

### t1 — Traversal engine: pure bounded BFS over links/supersedes (new eidetic/memory/traverse.py + tests/test_traverse.py)

- covers: c3, c5, h3, h4
- acceptance:
  - BFS walks links+supersedes ids from seed records with caller-stated depth and max-node bounds; hitting a bound sets a truncated flag on the result - never a silent cut
  - an injected can_serve predicate runs on EVERY hop: a private record reachable via links from a public seed never enters the result at any depth (regression test with a planted private record)
  - pure module: no I/O, no clock, no store import; deterministic discovery order; each discovered node carries (record id, hop depth, via-edge kind)

### t2 — Dual-store id-lookup: StoreBackend.get_many spanning home+repo stores (eidetic/memory/backend.py + tests/test_backend.py)

- covers: c2, h2
- acceptance:
  - get_many(ids, scope) unions BOTH candidate store dirs exactly like search does (data_refinery get is single-store); returns full Record round-trips, never projections
  - can_serve applies per store dir BEFORE dedup so a non-serveable duplicate can never shadow a serveable one (mirrors test_backend_multi_store pattern); home dir wins on id collision
  - unknown ids are skipped silently (dangling links tolerated); mongo/neo4j path degrades to per-id get without error

### t3 — Float-tolerant recall_count groundwork (eidetic/memory/record.py + scoring.py + tests/test_record.py, test_signal_strength.py)

- covers: c15
- acceptance:
  - recall_count accepts and round-trips float values through Record.to_dict/from_dict and the envelope mapping; legacy integer records load unchanged
  - signal_strength access_bonus math produces identical results for integer counts as before (no ranking regression); fractional counts scale continuously
  - no other schema field changes; test_record + test_signal_strength extended, all existing tests green

### t4 — Bundle assembly: recall default becomes the composite bundle + --source/--depth/--max-nodes flags (eidetic/cli/_commands/recall.py + tests/test_recall.py)

- depends on: t1, t2
- covers: c2, c5, c6, h5, c13, c14, h7, c8, h10, c9, h11, h4
- acceptance:
  - default recall --json emits ONE bundle object: {query, mode, truncated, items: [record fields + tier ('primary'|'traversal') + depth]}; the old bare-list shape is gone and test_recall shape assertions are updated in the same change
  - --source <x> filters on metadata.source across BOTH tiers (mixed-source store test proves it); coexists with --filter; --depth (default 1) and --max-nodes (default 20) are documented argparse flags and truncation is reported in the payload
  - every item carries record id, full metadata, tier label, and score where applicable - tier attributable without heuristics; bundle text fields are byte-equal to stored record text
  - no LLM or chat call anywhere on the path (only the existing embeddings endpoint); no new ingest parameter; text mode renders tiers readably
  - traversal seeds are the primary hits; discovery via t1 engine + t2 get_many; per-hop can_serve verified at CLI level with a planted private record

### t5 — Graded reinforcement wiring: full bump for primary, decay**depth for traversal (recall.py reinforcement loop + tests/test_recall.py)

- depends on: t3, t4
- covers: c15, h8, c24
- acceptance:
  - primary hits bump recall_count by 1.0; a depth-d traversal discovery bumps by DECAY**d (DECAY=0.5 module constant next to the scoring constants); last_recall stamped on every bumped record
  - no bump at all for records excluded by scope or lifecycle filters; score/signal still cleared before persisting
  - a persistence test asserts the EXACT stored values after a bundle recall: 1.0-stepped counts for primary, fractional for traversal

### t6 — Docs surface: explain catalog, learn, overview, CLAUDE.md, CHANGELOG + version bump

- depends on: t5
- covers: c21, h14, c22, h15
- acceptance:
  - explain catalog _RECALL documents the bundle shape, tiers, new flags, graded reinforcement, and the before-state contrast (flat hits, N follow-up recalls) plus the DR#20 upstream linkage and v1 degrade (client-side BFS, vector lines = verbatim record text)
  - learn _TEXT and _as_json_payload plus overview _VERBS updated in the same change (the hand-maintained trio drifts silently otherwise); CLAUDE.md memory-surface section updated
  - version bumped (minor) with a Keep-a-Changelog entry naming the breaking default-shape change; teken cli doctor --strict stays green; no compile/synthesize logic anywhere in the diff (reviewable seam)

### t7 — E2E + CI success signals: two-scope linked-graph live run + leak regression + bump assertions (new tests/test_bundle_e2e.py)

- depends on: t5
- covers: c20, h13, c23, h16, c24, h9, c3, h3
- acceptance:
  - e2e test seeds a small linked record graph across public+private scopes in temp stores, runs ONE default recall --json, and asserts all four bundle properties: tier labels + ids on every item, zero private leakage, bounds honored with visible truncation, graded bumps persisted
  - the planted-leak regression and exact-bump assertions run in normal pytest (CI) - every success signal is an automated check, no manual verification step
  - e2e uses EIDETIC_DATA_DIR temp isolation and never touches the real repo or home store

### t8 — Migration notices to every named consumer (via communicate; no code)

- depends on: t5
- covers: c19, h12, h6, c13
- acceptance:
  - notices with the new bundle schema + a worked example payload posted to: the #3 consumer thread (jetson-ai-lab-cli), the wrapper fan-out repos (rollout recipe or a tracked umbrella issue), and eidetic#37 (tells embodiment the composite fetch is live + its enrichment-level mapping)
  - each notice states the breaking change (bare list -> bundle object), the migration (read items[] instead of the top-level list), and lands before or with the release
  - eidetic's own /recall+/remember skill wrappers and docs/contract.md are checked for old-shape parsing and updated if affected (in-repo consumers migrate in the same release)

## Risks

- [follow_up] the reinforcing default bundle amplifies the #24/#32 committed-store dirtying; the write-location fix (sidecar store or reinforcement gate) lands in those issues and the bundle adopts it - explicitly not solved in this plan
- [follow_up] DR#20 enrichment adoption: when data-refinery ships real neo4j edges + a bounded traversal endpoint + persisted vector lines, swap the client-side BFS for the DR endpoint and upgrade the vector-lines tier from verbatim-record-text to true index lines
- [unknown_nonblocking] issue #16 stays open: a re-ingest by id still resets recall_count/last_recall, zeroing fractional counts too; t3 keeps floats round-tripping but the merge-forward fix is #16's own scope
