# eidetic-cli now remembers like a mind: a one-shot migration absorbs the entire old ~/.claude QQ memory (markdown notes, MongoDB RAG, Neo4j graph) into eidetic; every fact carries a dated, decaying signal that weakens with age but strengthens each time it is recalled-and-validated or corroborated by a newly remembered connected fact; and nothing is ever hard-deleted — a stronger conflicting fact shadows a weaker older one, and memories past a year archive themselves, everything still recoverable.

> eidetic-cli now remembers like a mind: a one-shot migration absorbs the entire old ~/.claude QQ memory (markdown notes, MongoDB RAG, Neo4j graph) into eidetic; every fact carries a dated, decaying signal that weakens with age but strengthens each time it is recalled, or corroborated by a newly remembered connected fact; and nothing is ever hard-deleted — a stronger conflicting fact shadows a weaker older one, and memories past a year archive themselves, everything still recoverable.

## Audience

- Every memory-using agent on this machine — all Claude Code sessions and all AgentCulture mesh/colleague backends — plus Ori as operator. Eidetic becomes their shared durable memory, replacing the per-agent ~/.claude QQ skill.

## Before → After

- Before: Today eidetic's store is empty and flat: records carry no date, no decay, no reinforcement, no conflict handling. The real knowledge lives in the old QQ skill (files + Mongo + Neo4j) which Claude loads but eidetic cannot see, and recall ranks purely on lexical or vector match, so a stale 2024 fact ranks equal to one verified today.
- After: Agents recall from one shared eidetic store whose ranking reflects how fresh, reinforced, and corroborated each fact is; all old QQ knowledge is present; contradicted facts fade behind their successors instead of misleading; nothing is lost — archived or shadowed memory is still retrievable on demand.

## Why it matters

- Memory that never forgets and never reconciles rots: contradictions accumulate, stale facts mislead, and recency or provenance are invisible to ranking. A temporal signal makes recall trustworthy, and never-delete-but-shadow makes it safe to let memory evolve.

## Requirements

- L1 Migration: a one-shot importer ingests every old QQ store — core.md and notes.md, Mongo claude_notes (importance, access_count, last_accessed), and Neo4j claude-context entities and relationships (mention_count, last_seen, verified, source_history) — into eidetic as idempotent upserts, each carrying provenance and a date signature, preserving the graph relationships as record links.
  - honesty: Re-running the migration is idempotent (same id or hash updates in place, never duplicates) and a down backend skips its layer with a warning without aborting the whole run.
- L2 Freshness: every record gains a date signature and a signal-strength scalar computed from age-decay plus recall reinforcement plus corroboration from linked facts; recall blends signal strength into the existing exact/keyword/approximate/hybrid ranking.
  - honesty: The decay-plus-reinforcement function is deterministic and recomputable at query time from stored fields (created date, last_accessed, access_count, validations, links), so ranking never depends on a mutable cached score that can silently drift.
- L3 Lifecycle: no hard delete; a stronger-signal conflicting record shadows a weaker older one (hidden from default recall, still retrievable); records older than about one year are archived (excluded from default recall, still retrievable); core/protected records are exempt from decay and archival.
  - honesty: Shadowing and archival only ever hide records from DEFAULT recall; an explicit flag (--include-shadowed / --include-archived) always returns the full set, and no code path issues a destructive delete.

## Honesty conditions

- The three layers ship as one coherent surface, not three disconnected features: the L2 signal-strength scalar is exactly what L3 thresholds on for shadowing and archival, and L1-migrated records receive a date and signal so they participate in ranking immediately.
- Every named audience consumes eidetic over the SAME subprocess/JSON boundary — Claude Code sessions, mesh/colleague backends, and Ori as operator — so one shared store serves all of them with no per-agent fork.
- The after-state is observable: a recall response exposes the freshness/reinforcement/corroboration signal (not just a lexical score), and archived/shadowed records are absent from default results yet returned under an explicit flag.
- The before-state is literally true today: record.py carries no date/decay/reinforcement/conflict fields and the store does not contain the old QQ knowledge (verified: store empty, schema lacks the fields).
- The harm is current and demonstrable on the migrated corpus: without a temporal signal a stale fact ranks equal to a verified-today one, and without shadowing a contradicted fact keeps surfacing.
- Each non-goal is enforceable: no code path extracts or summarises autonomously, no UI ships, the subprocess/JSON contract and public-only rule are unchanged, and no destructive delete exists anywhere in the tree.
- Each success signal is a runnable check: a migrated-topic recall returns the old facts with provenance; a fresh fact outranks an equal-match year-old one; a higher-signal contradiction hides the old one from default recall but --include-shadowed returns it; a >1yr record drops from default but --include-archived returns it.

## Success signals

- After migration, recalling a topic the old QQ skill knew returns the same facts with provenance; a freshly-validated fact outranks an equally-matching year-old one; remembering a higher-signal contradicting fact hides the old one from default recall while --include-shadowed still returns it; a record older than one year drops out of default recall while --include-archived returns it; no record is ever physically removed.

## Scope / boundaries

- Not an autonomous fact-extraction or summarisation agent (eidetic only remembers and retrieves; producers still author records); not a UI; does not change the subprocess/JSON contract or the public-data-only rule; never hard-deletes; the decay model is a transparent heuristic, not a learned ML relevance model.

## Assumptions

- The old QQ stores are reachable at migration time (Mongo qq_memory/claude_notes and Neo4j claude-context up, or a file export available); if a backend is down its layer is skipped with a warning, not fatal.

## Decisions

- Port and adapt the proven QQ importance-decay model (access_bonus, age_factor, staleness) and its recoverable-archive and promote-to-core tiers, rather than inventing a new relevance model.
- L2 reinforcement is PASSIVE: any recall counts as reinforcement (no cooldown, no explicit validate verb). A record stores exactly three temporal fields — creation date, last-recall date plus recall count, and related-memory links (to older and newer facts). Signal strength is a deterministic function of these three.
- Conflict detection for shadowing is HYBRID: an explicit supersedes link (declared on ingest) is authoritative and is the only thing that auto-shadows; eidetic may additionally SUGGEST likely conflicts (high embedding overlap plus an opposing claim) for human or agent confirmation, but never auto-shadows on a guess.
- Shadowing is WITHIN-SCOPE ONLY: a record can shadow only another record in the same scope; there are no cross-scope shadow interactions, which preserves the load-bearing no-leak invariant (private never surfaces in a public recall).
- Decay curve (default): port QQ's near-linear strength = (base - staleness + access_bonus) * age_factor initially — proven and transparent — with decay_rate tunable; an exponential half-life model is deferred.
- Archival triggers (default): a record archives when it is older than about one year OR its signal strength drops below threshold (either condition fires); core/protected records are exempt from both.
- Legacy dating (default): on migration derive each record's date signature from Mongo last_accessed / Neo4j last_seen when present, else markdown file mtime, else a decay-neutral date-unknown sentinel so undated facts are not penalised by age-decay.

## Open / follow-up

- RESOLVED (see decision c13): passive reinforcement, 3 temporal fields (creation date, last-recall date + recall count, related-memory links). Follow-up: exact field names + serialisation.
- RESOLVED (see decision c14): hybrid conflict detection — explicit supersedes link authoritative + assisted suggestions, never auto-shadow on a guess. Follow-up: how suggestions surface to the operator.
- RESOLVED (see decision c15): shadowing is within-scope only. Follow-up: none material.
- RESOLVED (see decision c16): near-linear QQ decay ported initially, decay_rate tunable; exponential half-life deferred.
- RESOLVED (see decision c17): archive on age>~1yr OR signal-below-threshold (either fires); core/protected exempt.
- RESOLVED (see decision c18): date cascade last_accessed/last_seen -> file mtime -> decay-neutral date-unknown sentinel.
- Link-corroboration boost (new, beyond QQ's age+access model): how much does a newly remembered connected fact raise an older linked fact's signal, and does the boost propagate across related-memory links? Mechanism and magnitude undecided.
