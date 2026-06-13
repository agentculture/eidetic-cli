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
commands even parse `culture.yaml` by hand rather than import a YAML library).

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
  (and its line in `learn.py`'s `_TEXT`/`_as_json_payload` and `overview.py`'s
  `_VERBS`) — these three are the hand-maintained "docs" surface and drift if you
  forget one.

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
