# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`eidetic-cli` is an **AgentCulture mesh agent**, scaffolded from
`culture-agent-template`. Its declared domain is "Agent/CLI providing eidetic
perfect-recall memory" — but **that memory functionality is not yet built**.
What exists today is the scaffold: an agent-first introspection CLI (cited from
[teken](https://github.com/agentculture/teken)'s `afi-cli` `python-cli`
reference), a mesh identity, the vendored guildmaster skill kit, and a
build/CI/deploy baseline. New domain code is added as additional noun groups on
top of this scaffold — it does not replace it.

The runtime package has **zero third-party dependencies** (`dependencies = []`).
`teken` and the lint/test tooling are dev-only. Keep it that way: the
self-contained runtime is a load-bearing property (the `whoami`/`doctor`
commands even parse `culture.yaml` by hand rather than import a YAML library) —
but see **Planned domain** below, where that property meets its first real test.

## Planned domain: the memory surface (not yet built)

The scaffold's reason to exist is a memory layer that doesn't exist yet. Two open
issues are the source of truth for its contract — **read them before writing any
`remember`/`recall` code**; the **Contract shapes** subsection below distills
them into a written shape, but the issues stay authoritative (#3 calls its shape
"negotiable", #1 calls its objects "proposed"):

- **[#3] First consumer — `jetson-ai-lab-cli`.** A read-only Discord/docs agent
  needs `eidetic remember` (ingest) and `eidetic recall "<query>"` (top-k
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

### Contract shapes (distilled from #1/#3 — proposed, not frozen)

The three deliverables on #1 — *object schema*, *ingest contract*, *retrieval
contract* — are written out here so a future instance builds against a shape
instead of re-deriving it from the issues each time. Treat field names as a
starting contract to confirm with the consumer, not a frozen API.

**Memory objects.** Every stored item is a record with a common envelope plus a
typed `metadata` payload selected by `type`:

| Field      | Required?  | Notes                                                                   |
|------------|------------|-------------------------------------------------------------------------|
| `id`       | yes        | stable identity; the upsert key                                         |
| `text`     | yes        | the chunk being remembered (Discord msg, doc section, paper summary, claim, …) |
| `type`     | yes        | one of the object types below; selects the `metadata` shape             |
| `hash`     | recommended | content hash for dedup/idempotency; derived from `text` when omitted   |
| `metadata` | recommended | provenance + facets; **round-trips verbatim** on recall                |
| `score`    | recall-only | relevance, set by `recall`, never sent on ingest                       |

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

**Ingest — `eidetic remember`.** Accepts **one record as a JSON object** or a
**batch as NDJSON on stdin** (one record per line) for bulk re-index. Required
on every record: `id`, `text`, `type` (`hash`/`metadata` recommended, `hash`
derived from `text` when absent). **Idempotent upsert by `id`/`hash`** —
re-ingesting the same record updates in place, never duplicates. Public data
only; the consumer guarantees it and eidetic must not assume otherwise.
Producers: the #3 consumer emits `discord`/`docs` records; the #1 pipeline
stages each emit their own object `type`.

**Retrieval — `eidetic recall "<query>" --top-k N --json`.** Input: a query
string plus optional facet filters. Output: top-k records ranked by relevance,
each returned with its `text` + **full `metadata`** + a `score` — **provenance
is mandatory** (recall without metadata is unusable; the #3 consumer builds
*cited* answers). Facet filters span both consumers: `source`, `channel`, time
window (#3) and `paper`, `topic`, `claim`, `lemma`, `method`, `author`,
downstream `task` (#1). An optional rerank pass (model-gear reranker) may live
in eidetic or in the caller — undecided.

**This is where the zero-dep property meets its first real test.** A memory layer
needs embeddings + a store, and the deliberate decision (not a default to drift
into) is *where* that weight lives: call `model-gear`'s OpenAI-compatible
`/v1/embeddings` and reranker over HTTP — keeping `dependencies = []` — or
lazy-import a vector store behind the CLI. Either way, keep the heavy deps behind
eidetic's *process* boundary so consumers stay dependency-free; that
subprocess-not-import shape is the whole reason this is a CLI. The sibling
`../autonomous-intelligence/data-refinery` (local neo4j + mongo) is the candidate
backing store for the graph/RAG side. Build memory as new noun groups (`remember`,
`recall`, …) on top of the scaffold per the **Architecture** pattern below — and
keep each one rubric-green (`overview`, `learn` entry, `explain` catalog).

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
