# eidetic-cli stops touching files: 'migrate store' now delegates to data-refinery's store.migrate endpoint (0.6.0), and the --backend token is uniform across every verb

> eidetic-cli stops touching files: 'migrate store' now delegates to data-refinery's store.migrate endpoint (0.6.0), and the --backend token is uniform across every verb

## Audience

- Agents/operators driving eidetic over the subprocess boundary, plus the data-refinery storage-boundary contract (consumer owns memory logic; data-refinery owns storage mechanics)

## Before → After

- Before: eidetic owns migrate_store.py which builds write paths (base.glob + sibling .tmp + os.replace) — the Sonar S2083 BLOCKER on PR #14; overview's --backend uses 'graph' where every other verb uses 'neo4j', so no single token works across verbs (#12)
- After: eidetic constructs zero filesystem write paths: 'migrate store' supplies only a record->Envelope transform to data_refinery.store.migrate(); migrate_store.py and its path code are deleted; a single --backend token (e.g. neo4j) works on every verb including overview

## Why it matters

- When storage mechanics are fully transparent behind data-refinery's boundary, the consumer carries zero path-handling code and zero path-handling findings — eidetic's Sonar gate goes green with no in-repo rule suppression, and a driving agent can reuse one backend token across all verbs

## Honesty conditions

- data-refinery 0.6.0 is published and its data_refinery.store.migrate(transform, backend, base_dir, dry_run) endpoint exists and is importable after bumping eidetic's pin to >=0.6
- the data-refinery boundary contract holds: a consumer supplies only a transform + the store root it already owns, never a per-file write path
- data_refinery.store.migrate keeps already-canonical Envelope lines verbatim and only feeds legacy lines to the transform, so eidetic's transform = record_to_envelope(Record.from_dict(obj)) is correct AND a re-run is a byte-for-byte no-op even though that transform is not itself idempotent
- the S2083 BLOCKER is attributable specifically to migrate_store.py's write-path construction, so removing that code removes the finding
- with migrate_store.py gone, a repo grep for write-path primitives (os.replace/glob+write_text into a store dir) returns nothing in eidetic, and Sonar needs no in-repo suppression
- the change touches only migrate store + the --backend choice lists/aliases; record schema, ranking, scoring, lifecycle, and recall/remember JSON stay byte-identical
- after the change a driving agent can pass --backend neo4j to remember, recall, sweep, migrate AND overview and every one accepts it
- no machine consumer parses 'eidetic migrate store --json' output today (it is a human-run one-shot), so changing its report keys is safe
- every verb's --backend choice list becomes {files, mongo, neo4j, graph} with graph and neo4j both resolving to the neo4j store, and no existing 'graph' or 'neo4j' usage breaks

## Success signals

- migrate_store.py deleted; grep shows no os.replace/glob/write_text path construction left in eidetic; 'eidetic recall|overview|sweep|remember|migrate --backend neo4j' all accept the token; 'migrate store --dry-run --json' round-trips against a real mixed Record/Envelope store and a re-run is a no-op (idempotent); full test suite + teken rubric green; Sonar S2083 on migrate_store.py is gone

## Scope / boundaries

- Not in scope: changing the record schema, recall ranking, freshness/lifecycle semantics, or the JSON report contract of recall/remember (consumer-facing). Not removing --backend selection itself. Store migration stays files-granularity only (mongo/neo4j migration still raises a structured not-yet-supported error, as data-refinery does)

## Decisions

- The 'migrate store' --json report changes from record-granularity {files_scanned, records_converted, already_envelope, files_rewritten} to data-refinery's file-granularity summary {files, migrated, migrated_files, skipped, dry_run}; acceptable because 'migrate store' is a one-shot maintenance verb with no downstream machine consumer (unlike recall/remember)
- Resolve #12 by making EVERY verb (remember/recall/sweep/migrate AND overview) accept files|mongo|neo4j|graph; 'graph' and 'neo4j' both map to the neo4j store. Canonical token is data-refinery's backend name; 'graph' kept as an accepted alias for back-compat and operator preference
