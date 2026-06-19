# eidetic recall now searches four ways — exact, approximate (vector), keyword (BM25), and hybrid (RRF fusion) — and a vendored memory skill lets both Claude and its colleague backend remember and recall against one shared store.

> eidetic recall now searches four ways — exact, approximate (vector), keyword (BM25), and hybrid (RRF fusion) — and a vendored memory skill lets both Claude and its colleague backend remember and recall against one shared store.

## Audience

- Agents driving eidetic over a CLI/subprocess boundary: Claude and its diverse colleague backend (the two in-house testers), plus the #3 jetson-ai-lab-cli and #1 research-pipeline consumers.

## Before → After

- Before: recall does vector-cosine only; there is no exact, keyword, or hybrid mode, and no skill that lets the colleague backend remember/recall or share a store with Claude.
- After: recall picks the right matcher per query — verbatim/exact, semantic vector, keyword/BM25, or a hybrid fusion of vector+keyword — across all three backends; exact and keyword work with the embed server offline; and a shared memory skill lets Claude and colleague read each other's memories.

## Why it matters

- Different recall needs different matching: vector-only misses exact ids/quotes and keyword hits, and a per-agent memory is a silo — a shared skill turns memory into a team faculty a diverse mind can also use.

## Requirements

- A vendored .claude/skills/memory skill drives eidetic remember/recall (all four modes) and is usable by BOTH Claude and the colleague backend against ONE shared store, so the two agents read each other's memories.
  - honesty: colleague, given only the memory skill + eidetic explain/learn, can recall a record Claude remembered with zero source edits and no hand-holding; friction is a logged usability finding.
  - honesty: colleague, given only the memory skill + eidetic explain/learn, recalls a record Claude remembered with zero source edits; friction is logged as a usability finding.
- recall exposes --mode {exact,approximate,keyword,hybrid} (default hybrid): exact=case-insensitive substring, approximate=vector cosine (today's behaviour), keyword=BM25 lexical, hybrid=weighted alpha blend of approximate+keyword. All per-mode scoring lives in ONE shared module every backend calls.
  - honesty: All four modes return Records with non-None score and honour scope.can_serve + metadata --filter identically across files/mongo/neo4j; the shared scorer is the only place per-mode logic lives.

## Honesty conditions

- Shipped CLI matches the announcement: eidetic recall --mode {exact,approximate,keyword,hybrid} exists AND a remember+recall skill pair ships.
- Both Claude and the colleague backend can drive the skills; the #3/#1 JSON-in/JSON-out consumer contract is unchanged.
- A live run shows each mode returning the expected hit ordering, and exact/keyword succeed with the embed server stopped.
- git history confirms recall did vector-cosine only before this change (no --mode, no remember/recall skill).
- A query with an exact id/quote that vector-only ranks low is surfaced by exact/keyword/hybrid.
- No storage engine is replaced: the diff touches scoring + CLI + skills only, not the Backend storage protocol or the compose services.
- All four modes pass tests on all three backends, exact/keyword pass offline, and colleague recalls a Claude-written record using only the skill.
- With the embed server unreachable, exact and keyword return correct results and hybrid still produces a useful keyword-driven ranking (no crash, no meaningless cosine).

## Success signals

- recall --mode {exact,approximate,keyword,hybrid} returns scored, provenanced hits identically across all three backends; exact/keyword succeed with embeddings offline; and colleague — given only the memory skill + eidetic explain — recalls a record Claude remembered, with any friction logged as a usability finding.

## Scope / boundaries

- Not a new storage engine, not a backend rewrite, not multi-machine sync: reuse the existing files/mongo/neo4j backends and the model-gear embed/rerank servers; add modes + a skill on top.

## Assumptions

- exact and keyword never call the embed server (pure lexical), so they work fully offline; hybrid detects the embedding offline-fallback and degrades to keyword-only ranking rather than fusing meaningless cosine scores.

## Decisions

- Default recall mode (no --mode flag) is hybrid.
- --mode exact = case-insensitive verbatim substring of the query in record.text; a --case-sensitive flag tightens to exact case.
- --mode hybrid fuses by WEIGHTED ALPHA BLEND: min-max normalise the approximate(vector cosine) and keyword(BM25) score sets to [0,1], then final = alpha*vector + (1-alpha)*keyword, default alpha=0.5, tunable via --alpha. When embeddings are the offline hash-fallback, hybrid sets alpha=0 (keyword-only) so it never fuses meaningless cosine.
- Ship TWO skills — .claude/skills/remember and .claude/skills/recall — each wrapping the eidetic CLI portably; the shared store is the files backend at ~/.eidetic/memory (home dir, shared across git worktrees so Claude and the colleague backend read each other's memories).
