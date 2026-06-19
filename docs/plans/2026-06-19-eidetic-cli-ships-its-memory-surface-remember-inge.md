# Build Plan — eidetic-cli ships its memory surface: remember ingests records, recall returns top-k cited hits with text plus full provenance metadata plus score

slug: `eidetic-cli-ships-its-memory-surface-remember-inge` · status: `exported` · from frame: `eidetic-cli-ships-its-memory-surface-remember-inge`

> eidetic-cli ships its memory surface: remember ingests records, recall returns top-k cited hits with text plus full provenance metadata plus score

## Tasks

### t1 — Record envelope and content hashing (eidetic/memory/record.py)

- covers: c8, h1
- acceptance:
  - a Record carries id, text, type, hash, metadata, scope, and score and round-trips to and from a dict; hash derives deterministically from text when omitted; identical text yields an identical hash

### t2 — Scope model with public or private visibility and an isolation guard (eidetic/memory/scope.py)

- covers: c6, h10
- acceptance:
  - a Scope has a name and a visibility of public or private; an isolation guard prevents records in a private scope from satisfying a public scope query; the default scope is configurable

### t3 — Backend protocol and registry with files as default (eidetic/memory/backend.py)

- acceptance:
  - Backend is a protocol exposing upsert(record) and search(query, top_k, scope, filters); a registry resolves a backend by name with files as the default; selecting an unavailable backend raises CliError rather than leaking a traceback

### t4 — model-gear embeddings and reranker client over stdlib HTTP with lexical fallback (eidetic/memory/embed.py)

- covers: h3
- acceptance:
  - embed posts to the model-gear v1 embeddings endpoint using only stdlib urllib with no third-party import; rerank is isolated behind the same client; when no endpoint is configured a deterministic lexical fallback scorer is used; nothing here adds to project dependencies

### t5 — Files backend: zero-dep JSONL store with idempotent upsert and cosine search (eidetic/memory/backends/files.py)

- depends on: t1, t2, t3, t4
- covers: c3, h7, h1
- acceptance:
  - a JSONL-per-scope store under a data directory; upsert by id or hash is idempotent so the same id twice yields one record updated in place; search ranks by cosine over embeddings with a lexical fallback and applies scope and facet filters; records persist and are retrievable across separate process invocations

### t8 — remember verb: one JSON object or NDJSON on stdin, idempotent upsert (eidetic/cli/_commands/remember.py)

- depends on: t1, t3, t5
- covers: c7, h11, c8
- acceptance:
  - remember accepts one JSON object as an argument or NDJSON on stdin with one record per line; each record upserts idempotently; --scope sets the namespace; --json emits a structured result and text mode a summary; re-ingesting the same id leaves the record count unchanged; bad input raises CliError with a hint and no traceback

### t9 — recall verb: top-k ranked hits with text, full metadata, score, and scope isolation (eidetic/cli/_commands/recall.py)

- depends on: t1, t2, t3, t5
- covers: c5, h9, h2, c12, h12
- acceptance:
  - recall query --top-k N returns records ranked by relevance, each with text plus full metadata plus score; --scope filters by namespace and a public scope recall never returns a private scope record; recall never emits a hit missing metadata or score; facet filters such as source, channel, time window, paper, topic are supported; --json supported

### t10 — Register remember and recall in the CLI parser (eidetic/cli/__init__.py)

- depends on: t8, t9
- covers: c4, h8
- acceptance:
  - python -m eidetic remember and python -m eidetic recall resolve and run; the previously absent commands are now registered in _build_parser; failures still raise CliError so no traceback leaks; the stdout and stderr split is preserved

### t11 — Rubric-green docs surface for remember and recall: explain catalog, learn, overview

- depends on: t8, t9
- covers: h4
- acceptance:
  - explain remember and explain recall return verbatim markdown; learn lists remember and recall with purpose, exit codes, and --json; overview includes both verbs; uv run teken cli doctor . --strict stays green

### t12 — Scope-isolation test: no private-to-public leak (tests/test_scope_isolation.py)

- depends on: t9
- covers: h10, h12
- acceptance:
  - a test stores a private scope record and asserts that a public scope recall never returns it on the files backend; the test fails if scope isolation regresses

### t13 — End-to-end and multi-consumer contract test (tests/test_e2e_memory.py)

- depends on: t8, t9, t10
- covers: c1, h5, c2, h6
- acceptance:
  - an end-to-end test runs batch NDJSON ingest then recall --top-k --json and asserts metadata and score are present and a duplicate-id re-ingest leaves the count unchanged; a discord record, a research record, and a private claude-memory record all round-trip with no per-consumer code; the #3 path is exercised via subprocess using python -m eidetic with no extra dependencies; memory-skill style facts round-trip through remember and recall

### t14 — Declare neo4j and pymongo as dependencies and update the zero-dep docs (pyproject.toml, CLAUDE.md, README)

- covers: h3
- acceptance:
  - pyproject.toml declares neo4j and pymongo in dependencies; CLAUDE.md and README no longer claim a zero-dep runtime and instead state that consumers stay dependency-free via the subprocess boundary while eidetic depends on neo4j and pymongo; uv sync installs them

### t6 — Neo4j backend, required, logic adapted from data-refinery (eidetic/memory/backends/neo4j.py)

- depends on: t1, t2, t3, t14
- acceptance:
  - a required neo4j backend (neo4j is a declared dependency); upsert via MERGE keyed on id; search via a vector index returns text, metadata, and score; the cypher and embedding logic is adapted from data-refinery (cite-don't-import), not depending on its qq_memory database; the driver import is local to the module to keep CLI startup fast

### t7 — Mongo backend, required, own schema adapted from data-refinery (eidetic/memory/backends/mongo.py)

- depends on: t1, t2, t3, t14
- acceptance:
  - a required mongo backend (pymongo is a declared dependency) using eidetic's own database and collections, not the qq_memory database; upsert by id is idempotent; search returns hits with metadata and score and respects scope; the store logic is adapted from data-refinery (cite-don't-import); the pymongo import is local to the module

## Risks

- [follow_up] adapt data-refinery's store, embedding, and cypher logic into eidetic (cite-don't-import) for the required Neo4j and Mongo backends, with eidetic owning its own schema rather than depending on the qq_memory database
- [unknown_nonblocking] confirm the model-gear embeddings and reranker endpoint contract: base URL, model id, the v1 embeddings request and response shape, and the rerank API shape
- [unknown_nonblocking] decide the private-scope local store location and per-host layout for Claude-agent memory
