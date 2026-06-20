# Build Plan — eidetic records now carry first-class agent attribution: every remembered record records who added it, so two agents writing into the same shared scope are no longer indistinguishable

slug: `eidetic-records-now-carry-first-class-agent-attrib` · status: `exported` · from frame: `eidetic-records-now-carry-first-class-agent-attrib`

> eidetic records now carry first-class agent attribution: every remembered record records who added it, so two agents writing into the same shared scope are no longer indistinguishable

## Tasks

### t1 — Add added_by field to the Record envelope (record.py): added_by: str | None = None, enumerated in to_dict() and from_dict() (from_dict via .get(...,None) like the t1 fields)

- covers: c8, h1, c11, h4, c1, c3, h10
- acceptance:
  - Record(...).to_dict() includes 'added_by'; from_dict round-trips a record with added_by set to an equal Record
  - Record.from_dict on a dict lacking 'added_by' yields record.added_by is None with no KeyError

### t2 — remember stamps added_by at ingest (remember.py): resolve absent value as --added-by flag > agent mesh nick (read_agent_fields) > None; preserve an explicit value from the record JSON or flag verbatim; add the --added-by argument

- depends on: t1
- covers: c9, h2, c14, h14, h7, c5, h11, c6, h12
- acceptance:
  - remember on a record JSON lacking added_by stamps the resolved agent nick; a value present in the JSON or via --added-by is preserved verbatim
  - resolution order is flag > nick > None (no env var); when nick is unresolved and no flag given, added_by is None

### t3 — Round-trip added_by on the neo4j backend (neo4j.py): add m.added_by to the Cypher SET clause + params dict and map it in _node_to_record(); add a neo4j round-trip test

- depends on: t1
- covers: c10, h3
- acceptance:
  - neo4j upsert -> reload returns added_by unchanged; a node written without the added_by property loads as added_by is None

### t4 — Verify added_by round-trips on the files and mongo backends via tests (no production change beyond to_dict/from_dict): upsert -> reload equality and legacy-load-None on both

- depends on: t1
- covers: c10, h3, c11, h4
- acceptance:
  - files and mongo upsert -> reload return added_by unchanged; a record/doc persisted without added_by loads as None on both backends

### t5 — overview --store reports distinct contributors per scope (stats.py compute_stats + overview.py render): contributor set = union of each record's added_by and its metadata.author

- depends on: t1
- covers: c15, h8, c2, h9
- acceptance:
  - compute_stats returns a per-(scope,visibility) distinct-contributor set equal to union(added_by, metadata.author); total/active/shadowed/archived/connections are unchanged
  - render_store_text emits a contributors line per scope; --json payload carries the contributor set

### t6 — Docs: add an added_by row to the CLAUDE.md record-schema table and document the --added-by flag + field in README

- depends on: t2, t5
- covers: c13, h6
- acceptance:
  - CLAUDE.md record-schema table has an added_by row stating semantics + default resolution; README documents --added-by and the overview contributors line
  - markdownlint-cli2 and the doc-test-alignment check stay green

### t7 — End-to-end success-signal test: remember(no added_by) -> reload -> overview contributors, asserting stamp/preserve/round-trip/legacy-None/overview across backends (files always; mongo/neo4j skip when unavailable)

- depends on: t2, t3, t4, t5
- covers: c7, h13
- acceptance:
  - an e2e test on the files backend asserts: stamped nick present, value round-trips, a legacy record loads added_by None, overview lists the contributor; mongo/neo4j variants skip cleanly when the service is down

### t8 — Update the agent-first in-CLI docs surface to teach the new added_by changes: learn.py (_TEXT/_as_json_payload) and explain/catalog.py entries for remember + overview, so 'eidetic learn' and 'eidetic explain' describe the added_by field, the --added-by flag, and the overview contributors line

- depends on: t2, t5
- covers: c13, h6
- acceptance:
  - eidetic learn (text and --json) teaches the added_by field, the --added-by flag, and the overview contributors line
  - eidetic explain remember and eidetic explain overview mention added_by; teken cli doctor . --strict rubric stays green

### t9 — Add a neo4j service to the GitHub Actions test pipeline (.github/workflows) so the neo4j round-trip test runs live in CI instead of skipping (a live neo4j is available locally for dev too); wire NEO4J_URI/USER/PASSWORD env for the test job

- depends on: t3
- covers: h3
- acceptance:
  - the CI workflow provisions a neo4j service and exports NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD; the neo4j round-trip test executes (does not skip) and passes in the GitHub Actions run
  - local dev without neo4j still skips the test cleanly rather than failing

## Risks

- [unknown_nonblocking] neo4j round-trip test (h3) needs a live neo4j; CI/dev without the docker-compose service must skip it cleanly rather than fail (task t3)
