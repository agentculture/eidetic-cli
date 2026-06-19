# eidetic-cli ships its memory surface: remember ingests records, recall returns top-k cited hits with text plus full provenance metadata plus score

> eidetic-cli ships its memory surface: remember ingests records, recall returns top-k cited hits with text plus full provenance metadata plus score

## Audience

- AI-agent consumers over a subprocess boundary, with no bespoke per-consumer coupling: (a) any AgentCulture mesh or colleague agent, present and future - the #3 jetson-ai-lab-cli read-only Discord and docs agent, the #1 research pipeline stages (arxivist, tensor, reduce, prove), and other current or future mesh agents; and (b) all Claude (Claude Code) agents, which call eidetic as a drop-in replacement for the existing ~/.claude memory skill (core and working notes plus MongoDB RAG plus Neo4j knowledge graph)

## Before → After

- Before: only the scaffold exists (whoami, learn, explain, overview, doctor); remember and recall are unbuilt; consumers have nowhere durable to store or retrieve
- After: eidetic durably stores text records and serves ranked, cited recall, so the scaffold finally does its declared job of perfect-recall memory

## Why it matters

- consumers build cited answers, so recall is unusable unless every hit round-trips full provenance metadata plus a score

## Requirements

- ingest is idempotent upsert by id or hash; recall returns text plus full metadata plus score and honors --json; each new noun group stays rubric-green with overview, learn entry, and explain catalog
  - honesty: remember the same id twice yields exactly one record, updated in place, proven by a test
  - honesty: recall never returns a hit missing metadata or score; provenance is mandatory
  - honesty: the runtime keeps dependencies empty; heavy embedding and store deps live only behind the eidetic process boundary
  - honesty: teken cli doctor --strict stays green: each new noun group exposes overview, a learn entry, and an explain catalog
- every record belongs to a scope (namespace) labeled public or private; recall is scope-aware and records in a private scope never leak into a public scope recall; private scopes such as Claude-agent memory are local-only; eidetic replaces the existing ~/.claude memory skill, which becomes a thin client over remember and recall
  - honesty: a recall in a public scope never returns a record from a private scope, proven by an isolation test; and the existing memory skill facts round-trip through eidetic remember and recall

## Honesty conditions

- the #3 consumer (jetson-ai-lab-cli) can remember a record and later recall it with full provenance across a subprocess boundary, adding zero eidetic-side dependency to the consumer
- the remember and recall contract is generic enough that any consumer - a mesh or colleague agent, a future agent like arxivist, or a Claude Code agent using eidetic in place of the existing memory skill - can call it over the subprocess boundary with no eidetic-side change per consumer
- a remembered record survives across separate CLI invocations (process restarts) and is later retrievable: storage is durable, not in-memory only
- no existing eidetic command stores or retrieves records today; remember and recall are genuinely absent, not partially present
- a recall hit carries enough provenance (source plus locator or permalink) for the consumer to render a citation without a second lookup
- eidetic performs no discovery, conjecture, decomposition, or proving; every scope is labeled public or private and a private scope's records never leak into a public scope's recall; the #3 public scope holds public data only
- an end-to-end test shows batch NDJSON ingest then recall top-k json with metadata and score, and a duplicate-id re-ingest leaves the record count unchanged

## Success signals

- the #3 consumer can batch-remember via NDJSON on stdin and recall with top-k JSON, every hit carrying mandatory metadata plus score; re-ingesting the same id never duplicates

## Scope / boundaries

- eidetic only remembers and retrieves; it does not discover, conjecture, decompose, or prove; not a general database. Data sensitivity is per-namespace: the #3 discord and docs namespace is public-data-only (its own contract), while private namespaces such as Claude-agent memory are local-only and never exposed to public or shared consumers; there is no global public-only rule

## Decisions

- storage and embedding weight stays behind the eidetic process boundary so consumers stay dependency-free: subprocess, not import
- memory storage is a pluggable backend supporting files, neo4j, and mongo; embeddings and reranking go through model-gear over HTTP; data-refinery is the backing store and will be improved by adding the missing ingest upsert and vector search and by wiring the currently unused mongo; the files backend keeps the default zero-dep while neo4j and mongo deps are optional and lazy-imported behind backend selection
- data sensitivity is per-scope visibility, public or private, chosen over a global public-only rule and over per-record access control; the load-bearing invariant is no private-to-public leak; public and shared scopes live in the data-refinery store while private scopes are local-only

## Open / follow-up

- RESOLVED by decision c11 (pluggable files/neo4j/mongo backend over model-gear embeddings+reranker, data-refinery as backing store). Non-blocking residual: extend data-refinery with an ingest/upsert path and a vector/top-k search, and wire the currently-unused qq_memory mongo
