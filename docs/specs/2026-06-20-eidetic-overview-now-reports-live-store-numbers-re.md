# eidetic overview now reports live store numbers — records per backend, per scope, distinct authors, and link-connections — and narrows to a single scope or backend on demand

> eidetic overview now reports live store numbers — records per backend, per scope, distinct authors, and link-connections — and narrows to a single scope or backend on demand

## Audience

- an agent (or operator) introspecting the eidetic memory store via the CLI, plus the agent-first rubric that gates overview

## Before → After

- Before: overview prints only static identity/verbs/artifacts and never touches the store; an agent must run recall blindly to discover whether anything is even stored
- After: running 'eidetic overview' shows a live Store section — total records, a per-backend breakdown (files/mongo/graph), per-scope counts (name+visibility+lifecycle), distinct authors, and counted link-connections — alongside the existing identity/verbs/artifacts
- After: an agent can narrow with 'overview --backend mongo' (one store) or 'overview --scope qq' (one scope) to get a focused snapshot instead of the whole store

## Why it matters

- a static snapshot can't tell an agent whether memory is populated, how it's distributed across scopes, or whether a backend is even reachable — the numbers turn overview from a brochure into an operational probe

## Honesty conditions

- the live numbers are computed solely from backend.all() and reflect what is actually stored — total/per-scope/connection counts are reproducible from a fresh enumeration, not cached or estimated
- the Store section is fully available under --json (machine-readable) on the same stdout stream, so an agent can consume counts + per-backend status without scraping text
- every count in the Store section is derived purely by iterating backend.all() and grouping in-process; rendering one backend never requires another to be reachable
- --backend X restricts the section to store X; --scope Y restricts counts to records whose scope.name==Y; an unknown scope yields an explicit zero-count section, not an error or a crash
- a reachable-but-empty backend (0 records) is reported distinctly from an unreachable backend ('unavailable: <reason>'), so the numbers genuinely answer 'is memory populated AND is each store reachable'
- this accurately describes today's overview.py: agent_sections() returns only Identity/Verbs/Sibling-pattern artifacts and no code path in the overview handler calls into eidetic.memory
- the 'connections' number equals the total count of link-references (len(links)+ (1 if supersedes else 0)) summed across counted records, and the label/help text states it counts references, not graph edges
- no new top-level verb is registered; the 'overview' parser gains --store/--backend/--scope flags only, and the catalog/learn/_VERBS docs surfaces are updated in lockstep so the rubric's docs-consistency checks stay green
- with mongo AND graph both down, 'overview --store' prints each as 'unavailable: <reason>' and still exits 0; 'overview <bogus>' still exits 0; 'teken cli doctor . --strict' stays green; bare 'overview' performs zero store I/O
- bare 'overview' and 'cli overview' produce byte-identical output to today (no store I/O, no new section); only an explicit --store/--backend/--scope adds the section

## Success signals

- 'overview <bogus-path>' and 'overview' against a down mongo/neo4j both still exit 0; the teken --strict rubric stays green; --json carries the structured store payload

## Scope / boundaries

- not a real graph traversal — 'connections' is the count of link-references (links + supersedes) read off record properties; neo4j stores these as node properties, not edges, so no edge-walking is implied
- not a new verb and not a schema change — this extends the existing 'overview' verb and only reads via backend.all(); storage format and record schema are untouched

## Non-goals

- does not add a hard always-on dependency on mongo/neo4j being up — a down backend degrades to an 'unavailable' line, never a crash

## Decisions

- store stats are opt-in: bare 'overview' stays static + never touches the store (preserving never-fail + the cli-noun reuse of agent_sections); 'overview --store' adds the live Store section. --backend/--scope imply --store.
- the Store section probes all three backends by default and reports each as live-with-counts or unavailable-with-reason (connection status is first-class, per the operator's 'report if connected/live' note)
- distinct-authors/'users' line is deferred to a follow-up issue (no first-class who-added field exists; author lives only in free-form metadata.author). The first cut omits it rather than implying a user model.
