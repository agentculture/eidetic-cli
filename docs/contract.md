# Memory scope + visibility convention (v1)

One convention every consumer of the eidetic memory store — the vendored
`recall`/`remember` skill wrappers in this repo, a bespoke runtime
integration like the colleague backend's `colleague/memory.py`, or a future
direct CLI consumer — pins to, so that mutual visibility between agents and
backends is a documented guarantee rather than an accident of whichever
hardcoded flags happen to agree today.

This document does not change any eidetic Python code, record schema,
routing, or CLI behavior. It only states, in one place, the convention that
`recall.sh` / `remember.sh` (this repo's vendored skill wrappers) are fixed
to follow in this same change, and that the plain `eidetic remember` /
`eidetic recall` CLI already followed before this change (its own
`--visibility` argparse default is `"public"` — see
`eidetic/cli/_commands/remember.py` and `eidetic/cli/_commands/recall.py`).

## Machine-readable summary

The block below is parsed by `tests/test_contract_drift.py` and compared
against the wrapper scripts' text and the CLI's actual argparse default.
Keep it in sync with both — a future divergence on either side fails that
test.

```text
version: 1
default_visibility: public
private_requires_explicit_flag: true
scope_naming: culture.yaml-suffix-per-repo
```

## 1. Scope naming

**One agent-personal scope per repo, named by that repo's `culture.yaml`
top-level agent `suffix`** — the same identity `eidetic whoami` reports.
This is a *naming* convention layered on top of the existing `Scope`
primitive (`name: str`, `visibility: "public" | "private"`,
`eidetic/memory/scope.py`); the primitive itself is unchanged.

Record **families** — `task`, `experiment`, `lesson`, and so on — are **not**
separate scope primitives and this convention does not add any. They ride
the existing `Record.type` field (a free-form string) plus `metadata`
facets, e.g. `{"type": "lesson", "metadata": {"family": "lesson", ...}}`.
Don't invent a new scope kind to express "this is a lesson, not a task" —
that distinction belongs to `type`/`metadata`, which already round-trip
verbatim through ingest and recall.

## 2. Visibility default

**Default: `public`, for in-repo team-shared records.** Store routing
already sends a public write made inside a git repo to
`<repo-root>/.eidetic/memory` — committed alongside the code, so it travels
with a `git clone` / `git worktree add` and is visible to every agent or
backend that later checks out the repo. That is the behavior public-by-
default is *for*.

`private` stays reachable, one explicit `--visibility private` away, for
data that genuinely shouldn't travel with the repo: it resolves to
`$HOME/.eidetic/memory` (never committed) and is invisible to any other
scope's recall — including a public one (`Scope.can_serve`,
`eidetic/memory/scope.py`: a private record is served only to a query in
the *same* scope; a public record is served to any query).

This was already the plain CLI's own default. Before this convention was
written down, this repo's own vendored wrapper scripts silently
*overrode* that default to `private` for a no-flag invocation — while
colleague's separate `colleague/memory.py` runtime hardcodes `public` for
its own shell-outs. Two consumers of the same store, disagreeing on the
default, by accident rather than decision. v1 makes the decision explicit:
**public wins**, and the wrappers in this repo are fixed to match (see
[History](#history--versioning) below for why this is a deliberate
reversal, not an oversight).

## 3. Store-resolution table

Copied from `eidetic/memory/backend.py` (`_resolve_write_dir` /
`_candidate_read_dirs`) — this is the actual code path, not a paraphrase:

| Precedence | Condition | Resolves to |
|---|---|---|
| 1 | `EIDETIC_DATA_DIR` set | that directory, unconditionally — both reads and writes, regardless of visibility |
| 2 | `visibility == "public"` **and** inside a git repo | `<git-toplevel>/.eidetic/memory` (write target); reads merge this with row 3 |
| 3 | otherwise — `private`, or outside any git repo | `$HOME/.eidetic/memory` |

A read (`eidetic recall`) merges `$HOME/.eidetic/memory` with the repo store
whenever one exists and no override is set, then applies `Scope.can_serve`
per candidate record: a `public`-visibility query sees every public record
(any scope name) and no private record at all; a `private`-visibility query
sees every public record *plus* private records that exactly match its own
scope name. (Verified live, 2026-07: a `public` query never returns a
private record regardless of scope name — `can_serve`'s private branch can
only match when the query's own visibility is also `private`.)

## 4. For consumers

Any process that shells out to `eidetic remember` / `eidetic recall` —
whether through these vendored skill wrappers or a bespoke runtime
integration such as colleague's `colleague/memory.py` — should pin to this
convention with its **own** drift test, rather than trusting that its
hardcoded scope/visibility flags happen to match this document or each
other. This repo's drift test (`tests/test_contract_drift.py`) can't
literally be reused cross-repo, but the pattern is: parse this file's
machine-readable block, then assert it against your own hardcoded flags (or
argparse defaults) so a future edit on either side is caught instead of
silently drifting back into disagreement.

## History / versioning

- **v1** (this document — [eidetic-cli#28](https://github.com/agentculture/eidetic-cli/issues/28)
  / [colleague#291](https://github.com/agentculture/colleague/issues/291) S9):
  default visibility declared `public` across every surface (the CLI, the
  vendored wrappers, and colleague's runtime). This is a **deliberate
  reversal** of the `FIX-4` decision recorded in
  [`docs/specs/2026-06-23-eidetic-cli-s-remember-recall-skill-wrappers-are-h.md`](specs/2026-06-23-eidetic-cli-s-remember-recall-skill-wrappers-are-h.md),
  which affirmed that a `private` personal-scope default was intentional at
  the time and only fixed the doc/behavior *mismatch* around it (the
  wrapper's help text used to say "Public data only" while defaulting to
  private — that spec made the docs honest about the private default; it
  did not question whether private was the right default). That decision
  predates the cross-repo mutual-visibility requirement colleague#291
  surfaced. It is recorded here as a visible, deliberate convention change —
  not a silent breach of that earlier decision.
- Future revisions to this convention (scope naming, the visibility default,
  or the store-resolution table) should bump the `version` field in the
  machine-readable block above and add a dated entry here, so
  `tests/test_contract_drift.py` and a human reader both notice a version
  bump that isn't matched by a doc update (or vice versa).
