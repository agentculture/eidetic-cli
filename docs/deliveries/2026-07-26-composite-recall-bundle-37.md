# Delivery Summary — composite recall bundle (#37)

plan: `composite-recall-bundle-37` · run: `partial` · date: `2026-07-26`
baseline: `devague summary skeleton`

## Intent

This run executed the converged `composite-recall-bundle-37` plan: make
`eidetic recall` return a **composite fetch-only bundle** — the search hits plus
a bounded `links`/`supersedes` neighborhood walk out from those hits — as its
*default* output, for a downstream memory compiler (embodiment's muse) that
needs raw fetched material it can compile without reaching into the store.
Eight tasks across four dependency waves, fanned out to parallel agents in
isolated git worktrees by `/assign-to-workforce`, each merge TDD-gated. Seven
tasks delivered and merged into `feat/composite-recall-bundle` (PR #38); the
eighth is drafted and deliberately held at the human gate, which is why this
run is recorded as **partial** rather than complete.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Traversal engine: pure bounded BFS over links/supersedes (new eidetic/memory/traverse.py + tests/test_traverse.py)
- `t2` — Dual-store id-lookup: StoreBackend.get_many spanning home+repo stores (eidetic/memory/backend.py + tests/test_backend.py)
- `t3` — Float-tolerant recall_count groundwork (eidetic/memory/record.py + scoring.py + tests/test_record.py, test_signal_strength.py)
- `t4` — Bundle assembly: recall default becomes the composite bundle + --source/--depth/--max-nodes flags (eidetic/cli/_commands/recall.py + tests/test_recall.py)
- `t5` — Graded reinforcement wiring: full bump for primary, decay**depth for traversal (recall.py reinforcement loop + tests/test_recall.py)
- `t6` — Docs surface: explain catalog, learn, overview, CLAUDE.md, CHANGELOG + version bump
- `t7` — E2E + CI success signals: two-scope linked-graph live run + leak regression + bump assertions (new tests/test_bundle_e2e.py)
- `t8` — Migration notices to every named consumer (via communicate; no code)

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `eidetic/memory/traverse.py` — pure bounded BFS, no I/O / no clock / no store import; `discover(seeds, fetch, can_serve, max_depth, max_nodes)` returning `TraversalResult(nodes, truncated)`. 23 tests in `tests/test_traverse.py`. Merged `1e85b6e` (wave 0, sonnet). |
| `t2` | delivered | `StoreBackend.get_many(ids, scope)` added to the `Backend` Protocol and adapter, spanning both candidate store dirs because `data_refinery.store.get` is single-id / single-store. `can_serve` is applied **per store dir before dedup**, so a non-serveable duplicate cannot shadow a serveable one. Merged `ba4794a` (wave 0, sonnet). |
| `t3` | delivered | `recall_count: int \| float` on `Record`; default stays integer `0` so untouched records serialize byte-identically. Merged `b18a30a` (wave 0, sonnet). |
| `t4` | delivered | `recall`'s default `--json` payload is now the bundle object `{query, mode, truncated, items[]}`; new `--source`, `--depth` (default 1), `--max-nodes` (default 20). Merged `3feb659` (wave 1, opus). |
| `t5` | delivered | Graded reinforcement: `1.0` for a primary hit, `DECAY**depth` for a traversal discovery (`DECAY = 0.5`, beside the other tunables in `scoring.py`); primary wins over traversal when a record appears in both tiers, bumped once. Merged `361d637` (wave 2, sonnet). |
| `t6` | delivered | `explain/catalog.py` `_RECALL`, `learn.py` `_TEXT`/`_as_json_payload`, `overview.py` `_VERBS`, `CLAUDE.md`, `CHANGELOG.md`, version → `0.13.0`. Merged `883eeab` (wave 3, sonnet). |
| `t7` | delivered | `tests/test_bundle_e2e.py` — 5 tests over a 6-record two-scope graph seeded through the real `remember` CLI and read back through one real `python -m eidetic recall --json` subprocess, plus real-store isolation fingerprinting. Merged `1a53a57` (wave 3, opus). |
| `t8` | blocked | **Nothing posted.** Three notices drafted and committed for review at `docs/notices/2026-07-26-composite-recall-bundle-migration-notices.md`. Blocked on two delivery decisions (see Remaining Work) and on PR #38 merging, so the version they name is real. |

## Mid-work Decisions

- `d1` — add `t9`: replace the client-side BFS with a `data_refinery`
  `store.neighbors()` passthrough and bump the `[store]` pin past 0.6.
  Reason (from the record): data-refinery-cli converged a spec for its issue
  #20 on 2026-07-26 that ships `store.neighbors()` with per-hop `can_serve`, a
  `truncated` flag defined to match eidetic's genuinely-admissible-cut
  semantics, and real `LINKS_TO`/`SUPERSEDES` relationships — so the plan's
  client-side BFS is a documented interim rather than the endpoint. The same
  spec closes its tier 3 by decision ("record text is the atomic citable
  unit") instead of building persisted vector lines, so `t6`'s degradation
  docs change meaning too: pending-upstream becomes closed-by-decision.
- `d2` — add `t10`: live-test the `store.neighbors()` passthrough against the
  real data-refinery stack (mongo + neo4j), not just the files backend.
  Reason (from the record): neither side's CI exercises real graph edges —
  data-refinery's spec pins hermetic tests against an injected fake Cypher
  session with no services block, and eidetic's bundle e2e suite is
  files-backend only.
- **Lifecycle filtering had to fold into the traversal predicate** (`t4`, no
  deviation record covers this — it was discovered mid-wave before `/deviate`
  was in use on this run). The plan scoped `t4` to bundle assembly; in
  practice, filtering only at the seed let a `sweep`-shadowed record re-enter
  the bundle through its own `supersedes` edge. `can_serve`, lifecycle
  visibility, and `--source` are therefore one predicate re-run at every hop.
- **`t4`'s blast radius was 8 test files, not the single `tests/test_recall.py`
  the plan named** — making the bundle the default changed the shape every
  recall-touching test asserted against.
- **`t8` was not delegated to a subagent.** Posting to other repositories is
  outward-facing and irreversible, so the notices are drafted by the main agent
  for human review instead. This was proposed and approved by the user at
  gate 2 (the implementation split plan), so it is an approved departure — it
  simply predates any `dN` record on this run.
- **Qodo review triage: one fix, two pushbacks.** The fix is real and is
  described under Drift. The two pushbacks: traversal items keep `score: null`
  (they were never ranked against the query, and a fabricated number would
  mislead anyone sorting on it), and graded reinforcement is the user's
  explicit design decision rather than a contract violation (Qodo compared
  against the pre-PR contract, which the docs moved with the code).
- **Two pre-existing SonarCloud issues were initially left alone, then fixed
  at the user's direction** (`863af3d`). They are `python:S8997` hand-rolled
  `os.environ` mutation in `tests/test_store_routing_e2e.py`, introduced by
  `625b014` (PR #23), not by this run. The fix is a genuine isolation
  improvement, not a lint silencer: routing the clean-slate deletes through
  `monkeypatch` records the prior state of the `DR_*` names that `_bridge_env`
  assigns directly, so teardown restores them instead of leaking them.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t1` (`d1`) | data-refinery-cli converged a spec for its issue #20 on 2026-07-26 shipping `store.neighbors()` with per-hop `can_serve` and a matching `truncated` semantic, plus real `LINKS_TO`/`SUPERSEDES` relationships — so the plan's client-side BFS is a documented interim rather than the endpoint | needs-follow-up |
| `t7` (`d2`) | neither side's CI exercises real graph edges: data-refinery's spec pins hermetic fake-session tests with no services block, and eidetic's bundle e2e suite is files-backend only | needs-follow-up |
| `t4` | the plan scoped this task to bundle assembly, but lifecycle filtering had to fold into the same per-hop predicate — otherwise a `sweep`-shadowed record leaks back into the bundle via its own `supersedes` edge | acceptable |
| `t4` | the plan named `tests/test_recall.py` as the test surface; making the bundle the default actually touched 8 test files | acceptable |
| `t8` | not delivered — notices drafted and committed for review, nothing posted; gated on two delivery decisions and on PR #38 merging | needs-follow-up |
| `t8` | not delegated to a subagent as the plan's fan-out assumed; held for human review because cross-repo posting is outward-facing and irreversible (approved by the user at gate 2, before `/deviate` was in use on this run) | acceptable |
| (no plan item) | three commits landed outside the task graph: `a382286` and `63ce951` fixing findings from the post-merge Sonar/Qodo review, and `863af3d` fixing the two pre-existing Sonar issues plus a coverage gap. Post-review fix-ups are not modelled as plan tasks | acceptable |
| (no plan item) | `63ce951` fixed a real defect the plan did not anticipate: `truncated` was set from *unresolved* edge ids at the depth bound, so a dangling or out-of-scope neighbor faked truncation — and worse, made the flag an **information side channel**, letting a public caller infer that a private record sits just past the bound. Truncation now resolves and admission-tests each edge before counting it | acceptable |

## Evidence

- tests: full suite `uv run pytest -n auto` — **461 passed, 2 skipped**. Both
  skips are pre-existing and unrelated: `tests/test_added_by_e2e.py:334` (live
  Neo4j not reachable) and `tests/test_scoring.py:184` (a cross-backend ranking
  test retired when storage moved to data-refinery).
- tests: `tests/test_bundle_e2e.py::test_one_default_recall_satisfies_every_bundle_property` — pass
- tests: `tests/test_bundle_e2e.py::test_planted_private_link_never_leaks_even_when_the_store_does_not_filter` — pass
- tests: `tests/test_bundle_e2e.py::test_persisted_bumps_are_exact_at_depth_one_and_depth_two` — pass
- tests: `tests/test_bundle_e2e.py::test_the_e2e_run_never_touches_the_real_repo_or_home_store` — pass
- tests: `tests/test_recall.py::test_private_linked_record_never_enters_a_public_bundle` — pass
- tests: `tests/test_recall.py::test_traversal_leak_guard_holds_even_when_the_store_does_not_filter` — pass
- tests: `tests/test_traverse.py::test_can_serve_rejects_a_deep_private_record_reachable_only_through_an_accepted_hop` — pass
- tests: `tests/test_traverse.py::test_depth_bound_with_only_unservable_records_beyond_is_not_truncated` — pass
- **mutation checks** (the discriminating evidence, run by the main agent
  rather than trusted from an agent report): each security-relevant test was
  re-run against a deliberately broken implementation to prove it is not
  vacuous — `t1` entry-level-only `can_serve` (2 failures), `t2`
  filter-after-dedup (1), `t4` always-admit predicate (1), `t5` flat `0.5` bump
  (3), `t7` always-admit (1), and the new visited-edge truncation branch (1).
  Every mutant failed exactly its intended tests.
- lint: `black --check`, `isort --check-only`, `flake8`, `bandit -c pyproject.toml -r eidetic` — all clean (bandit: 0 low / 0 medium / 0 high)
- coverage: **94.39 %** total (CI gate `fail_under=60`); `eidetic/memory/traverse.py` at **100 %**
- rubric: `uv run teken cli doctor . --strict` — `healthy: 26/26 passed, 0 errors, 0 warnings`
- commits: `b115d75..` (the `feat/composite-recall-bundle` branch; `b115d75` is
  its merge-base with `main`, and `863af3d` is the last commit before this
  artifact was written)
- PRs / issues: PR #38 · issue #37 (this feature) · data-refinery-cli#20 (upstream storage ask) · #24, #32 (store-dirtying, amplified by the reinforcing default) · #16 (re-ingest zeroes `recall_count`)

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `recall`'s default `--json` output is the bundle object `{query, mode, truncated, items[]}`, each item carrying the full record shape plus `tier` and `depth` | high | test `tests/test_recall.py::test_bundle_is_one_object_with_the_documented_keys` · commit `3feb659` |
| A private record reachable via `links`/`supersedes` from a public hit never enters a public bundle **at any depth**, and a rejected record is a dead end rather than a transit node | high | test `tests/test_bundle_e2e.py::test_planted_private_link_never_leaks_even_when_the_store_does_not_filter` · mutation-verified against an always-admit predicate · file `eidetic/memory/traverse.py` |
| `--depth` and `--max-nodes` bound the walk, and either bound cutting it short sets `truncated: true` — never a silent cut, and never a cut reported for material the caller could not have seen | high | tests `tests/test_recall.py::test_depth_bound_truncation_is_reported`, `tests/test_traverse.py::test_depth_bound_with_only_unservable_records_beyond_is_not_truncated` · commit `63ce951` |
| Reinforcement is graded: a primary hit bumps `recall_count` by `1.0`, a traversal discovery at hop depth *d* by `0.5**d`, and a record in both tiers is bumped once at the primary rate | high | test `tests/test_bundle_e2e.py::test_persisted_bumps_are_exact_at_depth_one_and_depth_two` · commit `361d637` |
| `--depth 0` reproduces the pre-0.13.0 flat, primary-only result set (inside the new bundle envelope) | high | test `tests/test_recall.py::test_depth_zero_gives_a_flat_primary_only_bundle` |
| The traversal engine is pure — no I/O, no clock read, no store import — and therefore testable in isolation | high | test `tests/test_traverse.py::test_module_is_pure_no_io_no_clock_no_store_import` · file `eidetic/memory/traverse.py` |
| The e2e suite leaves the operator's real repo and home stores byte-identical | high | test `tests/test_bundle_e2e.py::test_the_e2e_run_never_touches_the_real_repo_or_home_store` |
| The bundle behaves correctly against a live store (no-leak across a hop; 5 recalls → primary `5.0`, depth-1 traversal `2.0`) | medium | verified interactively against a real files-backend store during the run; the same semantics are pinned in-suite by `tests/test_bundle_e2e.py::test_persisted_bumps_are_exact_at_depth_one_and_depth_two`, but the live run itself is not re-runnable from this artifact |
| The bundle behaves correctly against the **mongo and neo4j** backends | unverified | no test or live run covers it — the e2e suite is files-backend only and CI has no services block. This is exactly what `d2` / `t10` exists to close |
| Tier 2 and tier 4 of issue #37 ("vector lines", "node-linked lines") are delivered at full fidelity | unverified | **not claimed** — each item's `text` is the record's own stored text; no persisted vector index or sub-record chunking exists anywhere in the stack. Documented as a degradation in `CLAUDE.md` and the explain catalog |
| Migration notices reached the named consumers | unverified | **not claimed** — drafted only, nothing posted (see `t8`) |

## Remaining Work / Follow-up

- **`t8` — post the three migration notices.** Drafts are committed at
  `docs/notices/2026-07-26-composite-recall-bundle-migration-notices.md`.
  Blocked on two decisions that are the user's to make: (1) whether notice 2
  goes out as one umbrella issue or as a rollout-cli recipe across the ~57
  repos carrying the `/recall` wrapper, and (2) whether notice 1 comments on
  closed issue #3 (keeping the audit trail on the consumer contract) or opens a
  fresh issue referencing it. Nothing posts until PR #38 merges, so the version
  the notices name is real. Owner: user decides, main agent posts.
- **`t9` (`d1`) — adopt `data_refinery.store.neighbors()`.** Swap
  `recall`'s `_traverse` for a thin mapping from the store's result to
  `TraversalNode(record, depth, via)` + `truncated`, and bump the pin past
  `data-refinery-cli[store]>=0.6,<0.7`. Every existing traversal and bundle
  test must pass unmodified — that is data-refinery's own stated acceptance
  criterion, and it is the guard that the passthrough preserved the semantics.
  Also update the honesty text in `CLAUDE.md` and the explain catalog: the
  "vector lines" degradation moves from *pending upstream* to *closed by
  decision* (record text is the atomic citable unit). Blocked until
  data-refinery-cli#20 ships; their spec is converged and under evaluation.
- **`t10` (`d2`) — live-test the passthrough against the real stack.** Bring up
  `data-refinery stack up` and exercise the bundle against mongo and neo4j:
  real `LINKS_TO`/`SUPERSEDES` traversal, the cross-backend equivalence claim,
  and the placeholder-node convention. Nobody else covers this — data-refinery
  tests against an injected fake Cypher session and eidetic's e2e is
  files-backend only. Depends on `t9`.
- **The reinforcing default amplifies a known store-dirtying problem.** Every
  `recall` writes `last_recall`/`recall_count` back, and for public records
  inside a git repo that store is the committed one — so a downstream compiler
  fetching on a tight cadence will churn `.eidetic/memory/*.jsonl`. Pre-existing
  and tracked in #24 and #32; the bundle adopts whatever they settle on. Not
  fixed here.
- **#16 is still open and still bites the consumer in issue #3.** Re-ingesting
  a record resets `recall_count` and `last_recall`, so a bulk re-index erases
  the reinforcement this feature accumulates. Untouched by this run.
- **Two pre-existing skips remain.** `tests/test_added_by_e2e.py:334` needs a
  live Neo4j; `tests/test_scoring.py:184` is a retired cross-backend ranking
  test. Neither is a regression from this run.
