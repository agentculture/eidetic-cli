# eidetic records now carry first-class agent attribution: every remembered record records who added it, so two agents writing into the same shared scope are no longer indistinguishable

> eidetic records now carry first-class agent attribution: every remembered record records who added it, so two agents writing into the same shared scope are no longer indistinguishable

## Audience

- mesh operators and the agents (Claude + colleague backend) that share the ~/.eidetic/memory store; plus overview --store readers asking 'who contributed in this scope'

## Before → After

- Before: author identity lives only in free-form metadata.author, populated by exactly one consumer (#3 Discord/docs index); records ingested by mesh agents via remember carry no agent attribution at all
- After: a first-class added_by field on the Record envelope is auto-stamped by remember at ingest (resolved as: explicit value in the record JSON or --added-by flag, else the agent's mesh nick, else None), round-trips verbatim across files/mongo/neo4j, and lets overview --store report distinct contributors per scope

## Why it matters

- the shared store at ~/.eidetic/memory explicitly enables two agents to write into the same scope; without attribution we cannot answer 'which agent contributed this' or 'how is work split across agents', and the overview authors line stays blocked

## Requirements

- Record gains an added_by: str | None = None envelope field, enumerated in both to_dict() and from_dict() (from_dict via .get with None default, mirroring the t1 fields)
  - honesty: a Record built with added_by set survives to_dict -> from_dict identical, and from_dict on a dict lacking 'added_by' yields added_by == None
- remember auto-stamps added_by when absent from the input record, and preserves an explicit added_by passed in the record JSON or via a --added-by flag (mirroring how created is stamped)
  - honesty: remember with no added_by in input stamps the resolved identity; remember with added_by present in the record JSON (or --added-by) keeps that exact value, verbatim
- added_by persists and round-trips on all three backends — files and mongo automatically via to_dict/from_dict, neo4j via an explicit Cypher SET property plus _node_to_record mapping
  - honesty: an upsert -> reload cycle returns added_by unchanged on files, mongo, and neo4j (neo4j proven by a round-trip test, since it maps fields explicitly)
- legacy records persisted before this field exists load cleanly with added_by == None on every backend
  - honesty: loading a stored record/node written without the added_by key/property returns a Record with added_by == None and raises no error on any backend
- docs updated: CLAUDE.md record-schema table gets an added_by row and README reflects the field + --added-by flag
  - honesty: the CLAUDE.md record-schema table lists added_by with its semantics and the README documents the --added-by flag; doc-test-alignment / markdownlint stay green
- overview --store reports distinct contributors per scope, computed in the pure compute_stats by unioning each record's added_by with its legacy metadata.author (so old #3 Discord-indexed records still surface their author)
  - honesty: compute_stats over records returns the correct per-(scope,visibility) distinct-contributor set = union of added_by and metadata.author; total/active/shadowed/archived/connections fields are unchanged

## Honesty conditions

- after this ships, a record remembered by Claude and one remembered by the colleague backend in the same scope are distinguishable by their added_by value
- the consumers named are real and currently served: the colleague backend shares ~/.eidetic/memory with Claude, and overview --store already renders per-scope stats this can extend
- today's code has no envelope attribution field — author exists only as free-form metadata.author, set by the #3 Discord/docs path and nothing else
- the shared-store design (same scope, two agents) is real and already shipped, so the inability to attribute is a present gap, not hypothetical
- added_by is stored verbatim with no verification step, no recall --added-by facet is added, and existing metadata.author values are never mutated by this change
- each clause is independently testable: stamp-when-absent, preserve-explicit, 3-backend round-trip, legacy-loads-None, overview-distinct-contributors
- the resolution order (record JSON / --added-by -> agent nick -> None) is implemented and the field survives an upsert/reload on every backend

## Success signals

- remember stamps added_by when absent and preserves an explicit value; the field round-trips on all three backends; legacy records with no added_by load cleanly as None; overview --store reports distinct contributors per scope

## Scope / boundaries

- not a permissions/identity-verification system: added_by is a free-form trust-on-write string, not authenticated; not a recall contributor-facet (deferred follow-up); not a rewrite of legacy metadata.author
