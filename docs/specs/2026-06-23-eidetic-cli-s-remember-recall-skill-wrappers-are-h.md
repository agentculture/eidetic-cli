# eidetic-cli's remember/recall skill wrappers are hardened upstream so the same bytes vendored into 57 downstream repos pass Qodo review and downstream CI

> eidetic-cli's remember/recall skill wrappers are hardened upstream so the same bytes vendored into 57 downstream repos pass Qodo review and downstream CI

## Audience

- The 57 downstream AgentCulture repos that vendor the remember/recall skills (and their Qodo + portability-lint/markdownlint CI), plus the agents — Claude and the colleague backend — that invoke the wrappers at runtime

## Before → After

- After: The same wrapper bytes, re-vendored unchanged into all 57 repos, pass Qodo review and downstream CI: the uv fallback no longer over-promises, remember.sh never hangs, no SKILL.md trips portability-lint, help text matches actual defaults, a private-scope downgrade is never silent, and recall treats 'help' as a query

## Why it matters

- These wrappers are byte-verbatim fan-out artifacts; a fix anywhere but upstream is overwritten on the next rollout. Each root cause is currently multiplied across ~57 PRs (215 Qodo findings), so the leverage of fixing upstream once is ~57x

## Requirements

- FIX-1 resolve_eidetic: stop implying a uv-checkout fallback that can never trigger in a vendored copy — either restrict the dev fallback to eidetic-cli's own repo and make the not-found error/hint honest about needing eidetic installed, or make it locate an eidetic checkout that actually exists
  - honesty: In a vendored copy (no eidetic-cli pyproject ancestor) the not-found path prints an actionable, single 'hint:' line and exits, and no longer claims a checkout fallback that isn't reachable
- FIX-2 remember.sh: never block on an interactive no-arg invocation — when there are no args and stdin is a TTY, print usage and exit non-blocking, while preserving the valid 'cat records.ndjson | remember.sh' batch path
  - honesty: remember.sh with no args on a TTY prints usage and exits >0 without reading stdin; 'printf '%s' "$record" | remember.sh' and 'cat file.ndjson | remember.sh' still ingest normally (piped/redirected stdin is not a TTY)
- FIX-3 SKILL.md: remove the literal ~/.eidetic/memory that downstream portability-lint flags — reword to a form the lint allows (e.g. $HOME/.eidetic/memory) across both SKILL.md files and the skill descriptions
  - honesty: After the reword, grep -nE '~/\.[A-Za-z]' over both SKILL.md files and the skill descriptions returns nothing the portability-lint flags, and the documented path still resolves to the same real location
- FIX-4 remember.sh usage: correct the stale 'Public data only.' line so help matches the actual --visibility private default
  - honesty: remember.sh --help no longer contains the phrase 'Public data only.' and instead states the private-by-default behavior and how --visibility public overrides it
- FIX-5 scope resolution: never silently downgrade an expected-private record to the public/default scope — emit a one-line stderr warning when the private-scope default cannot be applied (no culture.yaml / empty suffix)
  - honesty: When resolve_scope yields an empty suffix, a single warning line is written to stderr (not stdout) naming that the record falls back to the public/default scope; stdout output is unchanged
- FIX-6 recall.sh: stop swallowing the bareword query 'help' and stop exiting 0 on a missing query — only -h/--help invoke usage; a truly-empty query is a hint: + non-zero error
  - honesty: recall.sh "help" runs a real recall for the term 'help'; recall.sh with no query at all prints a 'hint:' line to stderr and exits >0; -h/--help still print usage and exit 0
- FIX-7 resolve_eidetic not-found error: collapse the multi-line hint heredoc to a single 'hint:' line to stderr per the error-contract rubric
  - honesty: The not-found path emits exactly one line beginning 'hint:' to stderr and still names the install command
- FIX-8 resolve_scope: harden the suffix parse so 'set -o pipefail' + an early-closing 'head' cannot abort the script (drop the pipe to head or guard with '|| true')
  - honesty: With 'set -euo pipefail' active and a culture.yaml present, resolve_scope returns the suffix and the script does not exit non-zero on the parse pipeline

## Honesty conditions

- The corrected wrapper bytes are byte-identical to what the rollout recipe re-vendors, so a recipe re-sync + fan-out propagates exactly these fixes with no per-repo divergence
- All 57 fanned-out PRs vendor these exact wrapper bytes (verified: recipe diff == upstream IDENTICAL), so they are the real consumers and CI surfaces the findings
- Re-vendoring the corrected bytes and re-running the rollout produces, in each repo, wrappers that exhibit none of the six fixed behaviors
- The 215 Qodo findings collapse to a handful of roots each repeated ~57x; fixing upstream once and re-syncing is the only change that isn't overwritten on the next rollout
- The diff touches only the two wrapper scripts and their SKILL.md (+ skill descriptions); no eidetic Python, schema, ranking, or storage code changes
- A local run of the flagged checks (portability-lint on the SKILL.md, the no-arg TTY behavior of remember.sh, the not-found path of resolve_eidetic) reports zero Tier-1/Tier-2 findings on the corrected bytes

## Success signals

- Running the downstream CI checks (Qodo's flagged rules: portability-lint, the empty-invocation and uv-fallback behaviors) against the corrected wrappers produces zero of the Tier-1/Tier-2 findings; a fresh recipe re-sync diff shows only the intended changes

## Scope / boundaries

- Not changing the eidetic CLI itself (Python), the record schema, ranking, or the data-refinery storage boundary; not re-running the rollout fan-out (that is a follow-up); not touching the rollout-cli recipe's localization logic except to re-sync the corrected bytes

## Decisions

- FIX-1 approach = honest error: keep the uv dev-fallback ONLY for eidetic-cli's own repo; in a vendored copy print one 'hint: install eidetic-cli' line + exit, claiming no checkout fallback (resolves q1)
- FIX-3 approach = reword '~/.eidetic/memory' to '$HOME/.eidetic/memory' in both SKILL.md files + skill descriptions (eidetic-owned, propagates to all 57 on re-sync); do NOT change the cicd portability-lint (resolves q2)
- Tier-3 disposition = fold the two cheap one-liners into this PR (single-line not-found hint; harden suffix parse vs pipefail); defer parent-dir traversal (v3) + SKILL.md long-lines (v4) as follow-ups

## Hard questions

- Restrict the dev fallback to eidetic-cli's own repo + make the error honest, OR teach the wrapper to locate an installed eidetic checkout elsewhere?
- Reword SKILL.md to $HOME/.eidetic (eidetic-owned fix), OR add a ~/.eidetic carve-out to the cicd portability-lint (not eidetic-owned)?

## Open / follow-up

- rollout-cli recipe: add/update the consumer provenance ledger (docs/skill-sources.md) + README skill-count when injecting the skills
- rollout-cli recipe: refresh uv.lock after the version bump
- rollout-cli recipe: fix CHANGELOG entry placement ([Unreleased]/ordering/version-link)
- rollout-cli recipe/PR body: note the alignment delta-check that the .claude/skills compliance rule requires
- Qodo 'restrict exit codes to 0/1/2' rule conflicts with eidetic's own 0/1/2/3+ error contract and the intentional exec passthrough — won't fix
- Qodo '--apply/--dry-run' and 'requires external CLI' findings are N/A: memory ingest isn't a destructive mass-update, and the subprocess boundary is the design — won't fix
