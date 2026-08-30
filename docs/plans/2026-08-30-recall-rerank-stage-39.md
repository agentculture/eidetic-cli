# Build Plan — recall rerank stage (#39)

slug: `recall-rerank-stage-39` · status: `exported` · from frame: `recall-rerank-stage-39`

> eidetic recall gains an opt-in reranker stage: --rerank re-scores the primary hits with the lobes cross-encoder and exposes `rerank_score` beside score, and the reranker's fallback to lexical overlap is never silent again

## Tasks

### t1 — record.py: add the recall-only `rerank_score` field and prove it cannot persist

- instruction: Follow the score/signal pattern exactly: dataclass field, `to_dict`, `from_dict` with .get() default. Do NOT touch `record_to_envelope` - it is a whitelist that already omits query-time fields, which is why the naive round-trip test is vacuous. Your test must bypass that whitelist and must FAIL when you delete the guard; prove it by deleting the guard once and pasting the failure.
- covers: c4, h4, c34, h24
- acceptance:
  - Record carries `rerank_score` alongside score/signal, threaded through `to_dict` and `from_dict` with a safe .get() default so legacy records load unchanged
  - a non-persistence test uses a stand-in that BYPASSES the `record_to_envelope` whitelist, so it exercises the guard rather than the whitelist
  - that test is mutation-verified: it FAILS when the guard is deliberately removed, and the failure is recorded in the PR
  - files touched: eidetic/memory/record.py + a new tests/`test_record_rerank.py` only

### t2 — embed.py: make the rerank lane self-reporting and reject short responses

- instruction: Mirror `embed_detect` exactly - it already solves this problem: it returns (vectors, online) and `_score_hybrid` zeroes alpha when offline. Do not invent a new diagnostics channel. Separately, `score_map`.get(i, 0.0) must stop defaulting: a response missing an index is a server error, not a zero-relevance document.
- covers: c2, h2, c6, h6, c35, h25
- acceptance:
  - rerank gains a detect-style variant returning (scores, online) mirroring `embed_detect`; the existing rerank() signature keeps working for any current caller
  - a test proves the caller can tell the lexical lane from the remote one WITHOUT inspecting score values
  - a response missing an index RAISES instead of substituting `score_map`.get(i, 0.0); a test feeds a short response and asserts the raise
  - the shipped `_remote_rerank` request/response handling is otherwise unchanged; any required change is reported as a separate finding, not folded in silently
  - files touched: eidetic/memory/embed.py + a new tests/`test_embed_rerank_lane.py` only

### t3 — new eidetic/memory/rerank.py: the pure ordering+threshold engine

- instruction: This is a pure engine in the style of eidetic/memory/traverse.py and lifecycle.py - read one of them first for the house shape. No imports of store, backend, embed, or datetime. It receives already-computed scores and returns ordered records plus a dropped count. The two subtle cases are the whole point of the task: a 0.0 threshold must be a no-op on strictly-positive scores, and a remote-calibrated threshold must never be applied to lexical-lane scores.
- covers: c33, h23, c36, h26, c22, h10, c20, h8
- acceptance:
  - a pure module in the style of traverse.py/lifecycle.py: no I/O, no clock, no store import, no network - given records, scores, the lane, and an optional threshold it returns the ordered records plus a dropped count
  - with no threshold (the default) it drops nothing and returns every input record reordered
  - a 0.0 threshold is a proven no-op on strictly-positive scores, with a test naming why so nobody later reads it as drop-everything-irrelevant
  - when the lane is lexical, the remote-calibrated threshold is NOT applied; a test gives a record that a remote cutoff would drop and asserts it survives
  - the dropped count is returned as data so the caller can report it; the engine never prints
  - files touched: a new eidetic/memory/rerank.py + a new tests/`test_rerank_engine.py` only

### t4 — backend.py: expose an explicit EmbedClient seam for the CLI

- instruction: Small task: add a named seam, not a refactor. StoreBackend builds its own EmbedClient in `__init__`; expose it (or a factory) so `cmd_recall` can inject a fake without monkeypatching a private attribute. Do not change search() or rank() behaviour.
- covers: c5, h5
- acceptance:
  - `cmd_recall` can obtain an EmbedClient through a named, documented seam without touching backend.`_embed`
  - a test injects a fake client through that seam with no monkeypatching of private attributes
  - files touched: eidetic/memory/backend.py + a new tests/`test_backend_embed_seam.py` only

### t5 — recall.py: wire the rerank stage behind --rerank, fail closed by default

- instruction: The integration task - depends on all of wave 1. Order is load-bearing: `_filter_lifecycle` -> rerank(pool) -> threshold -> \[:`top_k`\]. Reranking after the slice would make the stage unable to rescue anything, and reranking before the lifecycle filter would send shadowed records to the lane. Default recall (no --rerank) must be byte-identical to today: write the golden-output test FIRST and keep it green throughout. The error path must name key VARIABLES, never interpolate a resolved key value.
- depends on: t1, t2, t3, t4
- covers: c3, h3, c21, h9, c10, h11, c12, h13, c11, h12
- acceptance:
  - the pipeline order is rerank(pool) -> threshold -> slice to top-k, inserted between `_filter_lifecycle` and the \[:`top_k`\] slice; a test proves a shadowed/archived record is never sent to the reranker
  - --rerank, --rerank-pool (default 50) and --rerank-threshold (no default) are registered; --rerank is accepted with EVERY --mode including exact and keyword
  - items are ordered by `rerank_score` while score keeps its hybrid value; a test asserts the emitted order differs from a descending sort on score
  - an unreachable or 401 lane with no fallback opt-in exits non-zero with an error: line and a hint: line naming `EIDETIC_EMBED_API_KEY`, emits no bundle to stdout, and prints no traceback and no resolved key VALUE
  - with the fallback explicitly permitted the call succeeds, the payload marks the lexical lane, and a once-per-process stderr warning fires
  - a golden-output test proves default recall (no --rerank) yields a byte-identical bundle to the pre-change build
  - a temporal record scores identically under --rerank as without it, proving `_apply_blend` did not re-run
  - grep proves no completion/chat endpoint is reachable from the recall path and every emitted item text is byte-identical to the stored text
  - files touched: eidetic/cli/`_commands`/recall.py + a new tests/`test_recall_rerank.py` only

### t6 — docs: name --rerank on every flag-listing surface and state the exposures

- instruction: Docs only - no behaviour. Five surfaces, all listed in CLAUDE.md as the trio that drifts when one is forgotten, plus README and the vendored skill. Grep for --rerank across all five as your own check. Two corrections are easy to miss: exact/keyword are offline-safe only WITHOUT --rerank, and a threshold removes topically-relevant supporting records because the lane is near-binary (measured: 0.0048 for an on-topic record).
- depends on: t5
- covers: c18, h7, c23, h16, c38, h28
- acceptance:
  - all five surfaces name the flags: explain/catalog.py `_RECALL`, learn.py `_TEXT` and `_as_json_payload`, overview.py `_VERBS`, README.md, and .claude/skills/recall/{SKILL.md,scripts/recall.sh} usage text - verified by grepping for --rerank across all five, not by eyeballing
  - the docs state outright that under --rerank the item order no longer matches a descending sort on score
  - the docs state that --rerank posts record TEXT to the endpoint and that --rerank-pool sets the batch size, so a private-scope caller sees the exposure before opting in
  - the offline-safe wording for exact/keyword is corrected to offline-safe WITHOUT --rerank
  - the near-binary behaviour is stated where --rerank-threshold is documented: a cutoff also removes topically-relevant supporting records
  - teken cli doctor . --strict stays green and markdownlint passes
  - no wrapper CODE changes - recall.sh forwards flags verbatim; only its usage text moves

### t7 — drift lock, consumer notice, and version bump

- instruction: Three separate things, none of them code in eidetic/: (1) confirm `test_embed_default_drift.py` is untouched - if you needed to edit it, the flag-only decision was violated and you must stop and report that; (2) post the colleague notice about `filter_recall_records`/`COLLEAGUE_RECALL_MIN_SCORE` reading `score` while order now follows `rerank_score`; (3) run the version-bump skill.
- depends on: t5
- covers: c14, h15, c37, h27, c24, h17
- acceptance:
  - tests/`test_embed_default_drift.py` passes UNCHANGED - a diff on that file is itself the signal that the flag-only decision was violated, and the PR states that it is unmodified
  - the bundle docs state explicitly which field carries authoritative relevance under --rerank
  - colleague is told on the issue thread that a score-based `min_score` filter (`COLLEAGUE_RECALL_MIN_SCORE` in colleague/memory.py `filter_recall_records`) no longer matches rerank order, and that `score_recall_precision` ranks over an order that no longer follows score
  - the issue #39 thread records that the named consumers - jetson-ai-lab-cli (#3), the research-flow agents (#1), and the fanned-out wrapper repos - were told the flag exists, none of them being required to act
  - the version is bumped via the version-bump skill with a CHANGELOG entry, so the version-check CI job passes

### t8 — prove the offline posture, the before-state, and the three success-signal behaviours

- instruction: Verification, not features. Run the suite with the endpoint pointed at a dead port to prove hermeticity. The three success-signal behaviours must be three separately-named tests that each fail on their own - a single combined test can pass for the wrong reason, which is the failure mode this criterion exists to prevent. Mutation-verify each and paste the failures.
- depends on: t5
- covers: c13, h14, c26, h19, c28, h21
- acceptance:
  - the full suite passes with NO network route to the gateway (endpoint pointed at a dead port), proving no test depends on a live lane
  - the before-state is recorded from the parent commit: rerank( is called from nowhere outside embed.py and its tests, and recall has no rerank flag
  - the three success-signal behaviours exist as three separately-named, individually-failing tests - not one combined test that can pass for the wrong reason
  - each of the three is mutation-verified by breaking the code it covers, and the failures are recorded in the PR

### t9 — live verification against the real lobes gateway

- instruction: Manual evidence against the real gateway at <http://localhost:8001/v1> with the bearer token - add NO tests that touch the live lane. Reproduce the near-binary finding (a topically-relevant record in the 1e-3..1e-2 band while a direct answer scores >0.99) and record before/after orderings for a real query so the PR demonstrates the improvement rather than asserting it.
- depends on: t5, t6
- covers: c1, h1, c25, h18, c27, h20
- acceptance:
  - a live run on a real store reproduces the after-state: --rerank reorders so an injected distractor ranks last, and with --rerank-threshold it is dropped while the on-topic records survive
  - an unauthenticated live run exits non-zero with the remediation line naming the key variable
  - the near-binary finding is reproduced and quoted in the PR: at least one topically-relevant record scores in the 1e-3..1e-2 band while a direct answer scores >0.99
  - before/after orderings for at least one real query are recorded in the PR, so the improvement is demonstrated rather than asserted
  - no test added by this task runs against the live lane - the live run is manual evidence, the suite stays hermetic

## Risks

- [unknown_nonblocking] the other ~57 fanned-out consumers of recall output were never surveyed for the same score-thresholding collision colleague has (frame s23). If another one filters or re-sorts on score, --rerank ordering silently misleads it too - and this plan will not find that out (task t7)
- [unknown_nonblocking] no eidetic-side evaluation set exists: every quality claim about --rerank rests on ad-hoc probes run by hand against two queries. t9 demonstrates the feature works, NOT that it improves recall across the real corpus - and nothing in this plan measures that (task t9)
- [unknown_nonblocking] a caller who passes --rerank-threshold on an untemplated host (Thor/Orin, until their next `lobes init --apply`) can still lose relevant records, and eidetic cannot detect templated-vs-untemplated from the response shape. Mitigated to non-blocking only because thresholding is off by default (task t6)
- [unknown_nonblocking] rerank latency was measured on an idle fleet (0.07s for 50 docs). Behaviour under concurrent load from other mesh agents is unmeasured, and recall sits on the hot path for agent context (task t9)
- [follow_up] wave 3 tasks t6/t7/t8 are formally parallel but all land in the same PR branch as t5 recall.py work; t8 in particular edits the test file t5 creates. Sequencing is safe by wave, but a merge must re-run the full suite after every wave, not only at the end
