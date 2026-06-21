# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`eidetic-cli` is an **AgentCulture mesh agent**, scaffolded from
`culture-agent-template`. Its declared domain is "Agent/CLI providing eidetic
perfect-recall memory" — and **that memory surface is built** (v0.3.0+).
The scaffold provides an agent-first introspection CLI (cited from
[teken](https://github.com/agentculture/teken)'s `afi-cli` `python-cli`
reference), a mesh identity, the vendored guildmaster skill kit, and a
build/CI/deploy baseline. Memory verbs (`remember`, `recall`, `sweep`,
`migrate`) are added as additional noun groups on top of this scaffold —
they do not replace it.

The runtime package declares `data-refinery-cli[store]>=0.6,<0.7` as its storage
dependency; `neo4j` and `pymongo` arrive transitively via the `[store]` extra.
eidetic imports `data_refinery.store` (opaque KV + the `store.migrate` endpoint)
and `data_refinery.quality` (validate/dedup/integrity/freshness) and keeps all
memory semantics: the record schema, the four recall ranking modes, scoring, the
freshness signal, and the no-hard-delete lifecycle. **eidetic constructs no
filesystem write path** — even the on-disk format upgrade (`migrate store`) is
delegated to `data_refinery.store.migrate` (0.6.x; see issue #8), so the path
mechanics live behind data-refinery's boundary. Embeddings and rerank still go
over HTTP (no dep), and the files backend stays dependency-light for
private/local scopes. Consumers stay dependency-free because they call `eidetic`
over a subprocess boundary — that subprocess-not-import shape is the whole reason
this is a CLI.

## Memory surface (built — v0.3.0+)

The memory surface is live. `remember`, `recall`, `sweep`, and `migrate` are
all implemented and rubric-green. Two open issues remain the authoritative
consumer contracts — **read them before changing the record schema or I/O
format** (#3 calls its shape "negotiable", #1 calls its objects "proposed"):

- **[#3] First consumer — `jetson-ai-lab-cli`.** A read-only Discord/docs agent
  uses `eidetic remember` (ingest) and `eidetic recall "<query>"` (top-k
  semantic retrieval) across a CLI subprocess boundary: JSON in/out, plus a
  **batch NDJSON-on-stdin** ingest path for bulk re-index. **Provenance is
  mandatory** on recall — every hit returns its `text` + full `metadata`
  (`source`, channel, author, timestamp, permalink) + a `score`, because the
  consumer builds *cited* answers. Ingest must be idempotent: upsert by
  `id`/`hash`, never duplicate. Public data only.

- **[#1] Research-flow role.** In the split-agent pipeline
  `arxivist → tensor-cli → reduce-cli → prove-cli → eidetic-cli`, eidetic is the
  *durable* end — it stores paper records, idea-space maps, claim graphs,
  proofs/refutations, and reusable lemmas, and exposes them back to earlier
  stages on later runs. It does **not** discover, conjecture, decompose, or
  prove; it only remembers and retrieves.

### Record schema

Every stored item is a record with a common envelope plus a typed `metadata`
payload selected by `type`. The schema below is implemented and stable — field
names match what the backends persist and what `recall --json` emits:

| Field | Required? | Notes |
|-------|-----------|-------|
| `id` | yes | stable identity; the upsert key |
| `text` | yes | the chunk being remembered |
| `type` | yes | selects the `metadata` shape |
| `hash` | recommended | content hash for dedup; derived from `text` when omitted |
| `metadata` | recommended | provenance + facets; **round-trips verbatim** on recall |
| `created` | auto-stamped | ISO-8601 UTC; stamped by `remember` if absent; drives age decay |
| `added_by` | auto-stamped | agent or caller that ingested this record; stamped by `remember` if absent. Resolution order: explicit value in the record JSON or `--added-by` flag wins; else the agent's mesh nick (the `suffix` from `culture.yaml`); else `None` (e.g. wheel install with no `culture.yaml`). Never overwrites an explicit caller-supplied value. `None` for legacy records. |
| `supersedes` | optional | id of an earlier same-scope record this one replaces; `sweep` auto-shadows the target |
| `links` | optional | list of related-memory ids; reserved for corroboration scoring |
| `last_recall` | system | ISO-8601; bumped by each `recall` hit (passive reinforcement) |
| `recall_count` | system | integer; bumped by each `recall` hit |
| `lifecycle` | system | `active` (default), `shadowed`, or `archived`; set by `sweep` |
| `score` | recall-only | relevance, set by `recall`, never sent on ingest |
| `signal` | recall-only | freshness strength in [0, 1]; computed at recall time, blends into ranking |

Object `type`s and the `metadata`/relationships that distinguish them:

- **Consumer index (#3):** `discord` / `docs` records — `metadata`: `source`
  (`discord`|`docs`), `channel` (name + id), `author` (or pseudonymous id),
  `timestamp`, `permalink`. **Public data only.**
- **Research memory (#1):** `PaperMemory`, `IdeaSpaceMemory`, `ClaimMemory`,
  `LemmaMemory`, `ProofMemory`, `RefutationMemory`, `ExperimentNeed`,
  `ImplementationCandidate`, `ResearchThreadSummary`. Relationships form a claim
  graph: a `ClaimMemory` links to the `PaperMemory` it came from;
  `ProofMemory`/`RefutationMemory` attach to a `ClaimMemory`; `LemmaMemory` is
  reusable across claims; each record carries producer provenance (which of
  arxivist/tensor/reduce/prove emitted it, on which run).

### Ingest — `eidetic remember`

Accepts **one record as a JSON object** or a **batch as NDJSON on stdin** (one
record per line) for bulk re-index. Required on every record: `id`, `text`,
`type` (`hash`/`metadata` recommended, `hash` derived from `text` when absent).
**Idempotent upsert by `id`/`hash`** — re-ingesting the same record updates in
place, never duplicates. `created` is auto-stamped if absent. `added_by` is
auto-stamped if absent: the `--added-by` flag overrides; otherwise the agent's
mesh nick (from `culture.yaml`) is used; falling back to `None` in wheel installs
without a `culture.yaml`. An explicit `added_by` value in the record JSON is
always preserved verbatim. `supersedes` and `links` are accepted in the record
JSON and persisted as-is.

### Retrieval — `eidetic recall "<query>"`

Input: a query string plus optional facet filters. Output: top-k records ranked
by relevance, each returned with its `text` + **full `metadata`** + `score` +
`signal` — **provenance is mandatory** (recall without metadata is unusable; the
consumer in issue #3 builds *cited* answers). Freshness signal blends multiplicatively into
ranking for records that carry real temporal data; undated/legacy records are not
affected. Lifecycle filtering: `shadowed` and `archived` records are **excluded by
default** — pass `--include-shadowed` / `--include-archived` to retrieve them.
Each `recall` hit passively bumps `last_recall` and `recall_count` on the matched
records (reinforcement).

Facet filters span both consumers: `source`, `channel`, time window (#3) and
`paper`, `topic`, `claim`, `lemma`, `method`, `author`, downstream `task` (#1).

### Freshness signal

`eidetic.memory.scoring.signal_strength(record, now)` computes a float in `[0, 1]`
from the QQ near-linear model:

```text
access_bonus = min(0.5, recall_count * 0.05)
age_factor   = 1 / (1 + days_since_creation * 0.01)   # decay-neutral when undated
staleness    = days_since_last_recall * 0.01           # 0 when never recalled
signal       = clamp((0.5 - staleness + access_bonus) * age_factor, 0, 1)
```

The blend into the lexical/vector score is multiplicative around the neutral
midpoint (β = 0.25): `blended = score * (1 + 0.25 * (signal - 0.5))`. Neutral
records (`created == DATE_UNKNOWN` AND `recall_count == 0` AND
`last_recall is None`) bypass the blend entirely and are an exact no-op. Tunable
module constants live in `eidetic/memory/scoring.py`.

### Lifecycle — no hard-delete

`eidetic sweep` applies transitions across the whole store and is the **only**
command that writes `lifecycle` changes. The engine (`eidetic/memory/lifecycle.py`)
is pure — no I/O, no clock reads, deterministic and testable.

**Rules:**

1. **Within-scope shadowing (authoritative).** If record A declares
   `A.supersedes == B.id` and A and B share the SAME scope (name AND visibility),
   B is marked `shadowed`. Cross-scope `supersedes` links are ignored — this
   preserves the public/private no-leak invariant. Never auto-applied by
   `remember`; only triggered by `sweep`.

2. **Archival.** A record is marked `archived` when: (a) its age exceeds
   `ARCHIVE_AGE_DAYS` (365 days; DATE_UNKNOWN dates are age-neutral — never
   archived by age), OR (b) its signal falls below `ARCHIVE_SIGNAL_THRESHOLD`
   (0.25).

3. **Protected exemption.** Records with `metadata.protected` set to a truthy
   value are exempt from all transitions — never shadowed, never archived.

4. **Suggestions.** Same-scope records with identical normalised text are returned
   as advisory conflict hints for human confirmation. Never auto-applied.

`sweep --dry-run` reports what would change without writing anything.

### QQ memory migration

`eidetic migrate qq` performs a one-shot idempotent import of the legacy QQ
memory stack (Claude's personal knowledge) into a private eidetic scope:

- **Sources:** `~/.claude/skills/memory/references/core.md` + `notes.md` (one
  record per `##` section), MongoDB `claude_notes` collection, Neo4j entities
  tagged `knowledge_context="claude"`.
- **Destination:** `--scope qq --visibility private` by default — personal data
  is never served to a public recall.
- **Idempotent:** stable per-source ids (`qq-file:<path>#<slug>`, `qq-mongo:<id>`,
  `qq-neo4j:<id>`) make re-runs safe.
- **Resilient:** a down Mongo or Neo4j is skipped with a warning; the run
  completes using the remaining sources.

The dependency story is settled: embeddings + rerank go over HTTP to
`model-gear`'s OpenAI-compatible endpoint — no extra dep — and fall back to
deterministic local lexical when offline. Heavy deps are behind eidetic's *process*
boundary so consumers stay dependency-free. Storage is now owned by
[data-refinery-cli](https://github.com/agentculture/data-refinery-cli) (0.6.x;
tracked in eidetic#13 / data-refinery-cli#1) — eidetic consumes it by importing
`data_refinery.store` / `data_refinery.quality`. Storage mechanics never cross
the boundary back: the on-disk format upgrade (`migrate store`) is delegated to
`data_refinery.store.migrate` (issue #8), so eidetic constructs no write path and
carries no `pythonsecurity:S2083` sink. The stack is the
`ghcr.io/agentculture/data-refinery-stack` GHCR image, brought up with
`data-refinery stack up` (mongo on host :27018, neo4j bolt :7687 / UI :7474, apoc,
no auth — connection defaults are unchanged).

## Commands

```bash
uv sync                                   # install deps into .venv
uv run pytest -n auto                     # full test suite (xdist parallel)
uv run pytest tests/test_cli.py -v        # one file
uv run pytest -k whoami                   # one test by keyword
uv run pytest --cov=eidetic --cov-report=term   # with coverage (CI gate: fail_under=60)

# Lint — CI runs all of these; run them before opening a PR:
uv run black --check eidetic tests
uv run isort --check-only eidetic tests
uv run flake8 eidetic tests
uv run bandit -c pyproject.toml -r eidetic
markdownlint-cli2 "**/*.md" "#node_modules" "#.claude/skills"

uv run teken cli doctor . --strict        # the agent-first rubric gate CI enforces
```

Run the CLI itself with `python -m eidetic <verb>` or `uv run eidetic <verb>`.

> **Gotcha:** the console script is named `eidetic` (`[project.scripts]` in
> `pyproject.toml`), but the dist name, `prog`, and every help/doc string say
> `eidetic-cli`. So `uv run eidetic-cli …` (as written in some docs) does **not**
> resolve to a script — use `uv run eidetic …` or `python -m eidetic …`.

## Architecture

The CLI follows the **agent-first** pattern — every surface is introspectable and
machine-readable, designed to be driven by another agent, not just a human.

- **`eidetic/cli/__init__.py`** — `main(argv)` is the single entry point. It
  builds the argparse tree (`_build_parser`), then `_dispatch` invokes the
  matched handler and translates exceptions to exit codes. Each command lives in
  `eidetic/cli/_commands/<verb>.py` and exposes a `register(subparsers)` function
  called from `_build_parser`. **To add a verb or noun group, write that module
  and add one `register()` call** — the marked spot in `_build_parser` shows
  where.

- **Error contract (`eidetic/cli/_errors.py`, `_output.py`)** — every failure
  raises `CliError(code, message, remediation)`; `_dispatch` catches it (and
  wraps any stray exception) so **no Python traceback ever reaches stderr**.
  Argparse's own errors are routed through the same path via
  `_CliArgumentParser.error()`. Exit codes: `0` success, `1` user error,
  `2` environment error, `3+` reserved. This policy lives in exactly one place —
  don't `sys.exit()` or `print` errors from handlers.

- **stdout/stderr split (`_output.py`)** — results go to **stdout**, errors and
  diagnostics to **stderr**, never mixed. `--json` mode routes structured
  payloads to the same streams. Every command accepts `--json`; honor it in any
  new command (text errors render `error:` + `hint:` lines; the `hint:` prefix is
  required by the rubric).

- **`eidetic/memory/backend.py`** — single storage adapter that delegates to
  `data_refinery.store`; the former per-backend files (`backends/files.py`,
  `backends/mongo.py`, `backends/neo4j.py`) are replaced by this adapter. eidetic
  retains all memory semantics: record schema, ranking modes, scoring, signal, and
  lifecycle. `data_refinery.quality` handles validate/dedup/integrity/freshness at
  the boundary.

- **`eidetic/explain/catalog.py`** — `explain <path>` resolves command-path
  tuples to verbatim markdown docs. Adding a verb means adding its catalog entry
  here, plus its line in `eidetic/cli/_commands/learn.py`'s
  `_TEXT`/`_as_json_payload` and `eidetic/cli/_commands/overview.py`'s `_VERBS` —
  these three are the hand-maintained "docs" surface and drift if you forget one.

- **Identity (`whoami.py`, `doctor.py`)** — `find_culture_yaml()` walks up from
  the module to locate the repo's own `culture.yaml` (so identity is the agent's,
  not the CWD's), parsed without a YAML dep. In a wheel install no `culture.yaml`
  ships, so these degrade gracefully to defaults / an info-only doctor report.
  `doctor` mirrors the invariants `steward doctor` checks: **prompt-file-present**
  and **backend-consistency** (`claude`→`CLAUDE.md`, `acp`→`AGENTS.md`,
  `gemini`→`GEMINI.md`), plus **skills-present**.

## Mesh identity

`culture.yaml` declares `suffix: eidetic-cli` / `backend: claude`. The
`backend: claude` value requires this `CLAUDE.md` to exist at the repo root —
that pairing is what `doctor` and `steward doctor` enforce. If you change the
backend, rename the prompt file to match.

## Non-negotiable workflow rules

- **Bump the version on every PR** — even docs/config/CI-only changes. The
  `version-check` CI job fails the PR if `pyproject.toml`'s version equals
  `main`'s. Use the `version-bump` skill (updates `pyproject.toml` + prepends a
  Keep-a-Changelog entry to `CHANGELOG.md`). `__version__` is read from installed
  package metadata, so the single source of truth is `pyproject.toml`.

- **The agent-first rubric must stay green** — `teken cli doctor . --strict`
  gates CI. It checks the seven-bundle rubric (every noun with action-verbs
  exposes `overview`; `learn` is ≥200 chars and names purpose/commands/exit-codes/
  `--json`/`explain`; the error contract; etc.). The empty `cli` noun group exists
  solely to satisfy `overview_cli_noun_exists` — don't delete it.

- **Skills are vendored cite-don't-import** — `.claude/skills/` is copied from
  guildmaster (some re-broadcast from `devague`; `ask-colleague` directly from
  `colleague`). **Do not hand-edit vendored skill scripts.** Provenance, the
  re-sync procedure, and tracked local divergences are in
  [`docs/skill-sources.md`](docs/skill-sources.md). `.claude/skills/` is excluded
  from Sonar and markdownlint.

## PR lifecycle

Branch → implement → bump version → open PR → address review → merge. The `cicd`
skill is this repo's PR lane (layered on `devex pr`): it adds `status` (SonarCloud
quality gate + hotspots + unresolved threads) and `await` (blocks until CI
settles, non-zero exit on a red Sonar gate or unresolved threads). SonarCloud
gating only engages when `SONAR_TOKEN` is set (fork PRs and token-less repos stay
green). `sonarclaude` queries the SonarCloud API directly; `communicate` files
cross-repo issues and posts to mesh channels.

## Renaming this template

This repo is still a clonable scaffold — the name `eidetic` / `eidetic-cli` is
hard-coded in ~30 tracked files (package dir, CLI strings, tests,
`sonar-project.properties`, `README.md`). To find every occurrence before a
rename:

```bash
git grep -lI 'eidetic'
```
