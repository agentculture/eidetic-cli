# composite recall bundle (#37)

> eidetic recall now returns a composite bundle by default: matching records, their verbatim text identified by record id, a bounded links/supersedes neighborhood traversal, and the text of traversed neighbors - every item tier-labeled and cited by record id, filtered per hop by can_serve, with graded reinforcement (full bump for primary hits, decaying by hop distance for traversal discoveries)
> instruction: verify with a two-scope linked-graph e2e test against a real store before shipping

## Audience

- embodiment's muse memory-compiler (first consumer of the bundle) plus every existing recall caller - jetson-ai-lab-cli (#3), the fanned-out /recall skill wrappers, and the research-flow agents (#1) - all of whom migrate to the bundle-shaped default

## Before → After

- Before: recall is flat top-k search; a consumer wanting the hit, its neighborhood, and what backs them must issue N follow-up recalls and still cannot reach the traversal (issue #37); the graph edges exist in the data model but nothing walks them
- After: a single default recall returns a composite bundle: primary hits, their verbatim text identified by record id, a bounded links/supersedes neighborhood traversal, and the text of traversed neighbors - every item tier-labeled and cited by record id, with graded reinforcement persisted; a downstream mind compiles memory from raw fetched material without reaching into the store

## Why it matters

- the division of labour is the point: memory construction is association work and belongs to a mind (the muse compiles); memory retrieval is the store's job and stays mechanical - the bundle keeps eidetic fetch-only while making the raw material rich enough to compile from

## Requirements

- one composite fetch call returns the primary hits from the existing four recall modes plus a bounded neighborhood traversal over links/supersedes ids, assembled client-side in eidetic over the dual home+repo stores (data_refinery get is single-id, single-store: store/__init__.py:53-61) with full Record payloads per item
  - honesty: traversal id-lookups span BOTH candidate stores (home + repo) exactly like search does - data_refinery get is single-store, so the bundle path must union stores itself; every bundle item is a full Record round-trip, not a projection
- every traversal hop re-applies can_serve before a discovered record enters the bundle - remember accepts arbitrary link/supersedes ids with zero scope validation (remember.py:83-149) and the only cross-scope guard today is sweep's shadow no-op (lifecycle.py:176-177), so an unchecked traversal is a brand-new leak path; mirror the double-check in backend.py:365-383
  - honesty: a regression test plants a private record reachable via links from a public record and asserts it never appears in a public-visibility bundle at any depth; the can_serve check runs per hop, not only at entry
- traversal depth and size bounds are caller-stated flags with safe defaults - the embodiment spec frames bound-setting as the caller's responsibility, not hardcoded in eidetic
  - honesty: depth and size bounds are argparse flags with documented defaults; hitting a bound truncates the traversal VISIBLY (the bundle reports truncation) - never silently
- every bundle item carries record id, full metadata, score where applicable, and a bundle-tier label (primary hit vs traversal-discovered) so the consumer can cite each item and record its enrichment level - provenance-mandatory precedent from issue #3, ledger requirement from the embodiment spec
  - honesty: every item in the bundle JSON carries record id, full metadata, its tier label, and score where applicable - a consumer can attribute every item to its tier without heuristics
- recall's default output IS the composite bundle - the bundle was always the intended use; the bare-list shape is superseded, and existing consumers (jetson-ai-lab-cli #3 contract, the fanned-out /recall skill wrappers) migrate to the bundle shape with a consumer notice
  - instruction: flip recall's default payload to the bundle object; update test_recall shape assertions, explain catalog, learn text+json, overview _VERBS; post migration notices via communicate
  - honesty: the default-shape change ships with migration notices to the #3 consumer and the wrapper fan-out, and every in-repo consumer of the old shape (tests, /recall skill wrapper, explain/learn docs) is updated in the same change
- recall gains a dedicated --source <x> flag for source-specific filtering of the bundle (alongside the existing generic --filter facets)
  - instruction: add --source to recall argparse as a first-class facet on metadata.source; test against a mixed-source store
  - honesty: --source constrains the bundle deterministically and its tier coverage (primary-only vs all tiers) is explicitly documented and tested
- graded reinforcement: primary hits reinforce fully as today; traversal-discovered records reinforce by a fraction that decays with hop distance from the recalled original node
  - instruction: thread hop depth through the traversal; bump = decay**depth (proposed decay 0.5); make recall_count float-tolerant in Record schema + signal math + the #16 merge-forward fix
  - honesty: a persistence test asserts the exact graded bumps: the full bump for a primary hit, decay**depth for a traversal discovery, and no bump at all for records excluded by scope or lifecycle filters

## Honesty conditions

- grep-level verifiable: the bundle path makes no LLM or chat call and generates no text - the only network call is the existing embeddings endpoint; every bundle text field is byte-equal to stored record text
- the bundle surface exposes no ingest parameter and persists nothing caller-supplied; the only writes on the path are eidetic's own graded reinforcement bumps
- the migration notice demonstrably reaches each named consumer (issue #3 thread, the wrapper fan-out repos, eidetic#37 thread for embodiment) before or with the release
- demonstrated by the e2e linked-graph run: one default recall --json call yields all four tiers with record ids and tier labels present on every item
- verified against current source: recall.py emits flat ranked hits only and no verb traverses links/supersedes - matching issue #37's what-exists-today section and scope entries s2/s3
- the seam holds in code review: no compile/synthesize logic lands in eidetic; embodiment's compiler consumes the bundle over the subprocess boundary only
- each signal is a concrete automated check: the bundle-shape assertion, the planted-leak regression test, and the graded-bump persistence test all green in CI
- an end-to-end live-store run seeds a small linked record graph across public+private scopes, runs default recall, and verifies all four bundle properties: tier labels + record ids on every item, zero private leakage through traversal, caller bounds honored, graded reinforcement persisted

## Success signals

- a live run shows one default recall --json emitting a tier-labeled, id-cited bundle; a planted private record reachable via links from a public hit never appears in a public bundle; persisted recall_count shows the full bump for primary hits and decayed fractional bumps for traversal discoveries

## Scope / boundaries

- no synthesis, summarization, or LLM call inside eidetic - the bundle is mechanically assembled raw material, matching the fetch-only posture issues #37 and #1 both state
- the bundle surface accepts no writes from the consumer - it is not a channel for embodiment to propose associations back into the store; the embodiment spec explicitly leaves muse write-back as a separate future question

## Non-goals

- real graph-store traversal and persisted vector lines are data-refinery storage mechanics, not built here - DR 0.6.0 neo4j keeps links as opaque metadata JSON strings on nodes with no relationships and no traversal API in any backend; making edges real and persisting chunk lines is an upstream DR ask (precedent: store.migrate via data-refinery-cli#8); v1 traversal is client-side BFS over record fields

## Assumptions

- v1 vector-index lines degrade to each record's own verbatim text identified by record id - no persisted vector index or sub-record chunking exists anywhere in the stack (embeddings are computed fresh per query, scoring.py:335-337; all three data_refinery backends store plain envelopes)
- shipping the surface touches the hand-maintained trio (explain catalog, learn _TEXT and _as_json_payload, overview _VERBS) plus tests and a version bump; the teken rubric probes a fixed global surface only, so forgetting any of these fails no gate - convention-enforced
- fractional reinforcement makes recall_count a float: bump = decay^depth (proposed decay 0.5, so depth-1 = 0.5, depth-2 = 0.25); signal_strength's access_bonus math (recall_count * 0.05) already works on floats; interacts with #16's merge-forward fix which must preserve fractional counts on re-ingest
- with the bundle as the reinforcing default, the #24/#32 committed-store dirtying is amplified rather than avoided - the write-location fix (sidecar store or reinforcement gate) still rides on those issues' general resolution; the bundle adopts whatever lands there rather than inventing its own policy

## Scope exploration

- `s1` — `eidetic/memory/scoring.py + embed.py (vector path)`: embeddings are computed fresh at query time over full candidate texts via one HTTP batch (scoring.py:330-341, embed.py:239-283); nothing is persisted and records are atomic text with no chunking - vector-index lines exist nowhere in the stack today
  - seeds: `c7`
- `s2` — `eidetic/cli/_commands/recall.py`: reinforcement is unconditional with no suppression flag (recall.py:123-138) and runs after payload emit; --json emits a bare list of Record.to_dict() (recall.py:112); mode/top-k/scope/visibility/filter/lifecycle flags are all reusable by a bundle; query is single-positional, no batch input
  - seeds: `c4`, `c10`
- `s3` — `data_refinery 0.6.0 installed source (store/__init__.py, backends/files.py, mongo.py, neo4j.py)`: public store API is put/get/list/migrate/get_backend only; get is single-id and single-store (does not span eidetic's home+repo pair); neo4j stores one Document node per envelope with links/supersedes buried in an opaque metadata JSON string property - no relationships, no traversal or neighbor API in any backend; mongo docstring explicitly disclaims embeddings
  - seeds: `c2`, `c11`
- `s4` — `eidetic/memory/scope.py + lifecycle.py + docs/contract.md section 3`: can_serve is applied twice in search as defense-in-depth (backend.py:365-366 and 378-383) but remember accepts arbitrary link/supersedes ids with zero scope validation, and the only cross-scope guard is sweep's shadow no-op (lifecycle.py:176-177) - a traversal following edges is a genuinely new leak path needing per-hop can_serve
  - seeds: `c3`
- `s5` — `eidetic/cli/__init__.py + explain/catalog.py + learn.py + overview.py + tests + teken rubric source`: new-verb blast radius is register() wiring plus the hand-maintained trio (catalog ENTRIES, learn _TEXT/_as_json_payload, overview _VERBS) plus opt-in test lists; the teken rubric runs 7 fixed bundles against the global surface only and never probes a new verb - all discoverability surfaces are convention-enforced, no drift test walks parser choices against them
  - seeds: `c12`
- `s6` — `eidetic issues #24 #32 #16 (reinforcement fragility)`: recall already dirties the committed repo store on every hit with real production pain (colleague repo repro in #32); re-ingest zeroes recall_count/last_recall (#16); a background compiler fetching primary hits plus whole neighborhoods would multiply this actively-tracked bug - fetch-only bundle must not reinforce
  - seeds: `c4`
- `s7` — `embodiment spec 2026-07-25-function-first-loops-muse-redesign.md + issues #11 #12`: consumer binds a degrade floor (muse-compiler runs on flat recall today, bundle is enrichment never a gate - zero timing pressure), expects caller-stated depth/size bounds, needs an enrichment-level distinction per item, and closes a provenance loop by threading compiled-from record ids back through links; muse write-back of associations is explicitly a separate future question
  - seeds: `c5`, `c6`, `c9`
- `s8` — `eidetic issues #3 #1 + docs/specs/2026-06-19 recall-four-ways spec + docs/contract.md`: provenance-mandatory is the founding consumer precedent (#3: recall without metadata is unusable); eidetic's fetch-only no-synthesis posture predates this ask (#1); scope enforcement is mode-invariant per the four-ways spec honesty condition
  - seeds: `c6`, `c8`
- `s9` — `adjacent open issues #34 #35 #36 #18`: silent embed hash-fallback (#34) would invisibly poison the bundle's vector tier; hybrid min-max drop bug (#35) sits under the primary fetch; typed envelopes (#36, DR 0.11) will relabel future graph nodes without touching no-leak; public-across-scope-names (#18) is intended behavior a traversal will surface more visibly - none block, all worth citing in the bundle docs

## Decisions

- the upstream storage ask is filed as data-refinery-cli#20 (real neo4j edges for links/supersedes, bounded scope-filtered-per-hop traversal endpoint, persisted vector/chunk lines); eidetic v1 ships the client-side degrade and does not wait on it
