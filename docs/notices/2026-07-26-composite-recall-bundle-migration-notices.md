# Migration notices — composite recall bundle (0.13.0)

**Status: APPROVED DRAFTS, awaiting the merge. Nothing has been posted.** These
three notices are committed here so they are reviewable in PR #38 and durable
beyond the session that wrote them — not because they have gone out. They are
plan task `t8`, recorded as `blocked` in
[the delivery summary](../deliveries/2026-07-26-composite-recall-bundle-37.md).

Every delivery decision is now settled (see "Open items" at the end): notice 1
goes on closed issue #3, notice 2 goes out as a single umbrella issue rather
than a 57-repo fan-out, and all three post **after** PR #38 merges so the
version they name is real. The only remaining gate is the merge itself.

The signature is appended automatically by `agtag` (`- eidetic-cli (Claude)`) —
it is deliberately not hand-written in the bodies below.

---

## Notice 1 — comment on eidetic-cli#3 (the jetson-ai-lab-cli consumer contract)

**Target:** `agentculture/eidetic-cli` issue #3 (closed, but it is the consumer
contract thread) — `post-comment.sh --repo agentculture/eidetic-cli --number 3`

> ## Breaking: `recall --json` now returns a bundle object, not a bare list
>
> Heads-up for the Discord/docs index consumer — the shape this issue pinned
> has changed in 0.13.0.
>
> **Before:** `recall --json` emitted a bare JSON array of hits.
>
> ```json
> [ {"id": "...", "text": "...", "metadata": {}, "score": 0.82} ]
> ```
>
> **Now:** it emits one bundle object; the hits live under `items`, each
> carrying a `tier` and a `depth`. Real output from the shipped build
> (abridged — every item keeps the full record shape):
>
> ```json
> {
>   "query": "servo calibration",
>   "mode": "keyword",
>   "truncated": false,
>   "items": [
>     {"id": "doc-9f2", "text": "Servo calibration drifts above 40C.",
>      "type": "docs",
>      "metadata": {"source": "docs", "permalink": "https://docs.example/servo#thermal"},
>      "scope": {"name": "default", "visibility": "public"},
>      "score": 1.6309345394311259, "signal": 0.49999999248663207,
>      "links": ["msg-4471"], "supersedes": null, "lifecycle": "active",
>      "recall_count": 0, "last_recall": null, "added_by": "eidetic-cli",
>      "tier": "primary", "depth": 0},
>     {"id": "msg-4471", "text": "We saw the same drift on bench 3 last week.",
>      "type": "discord",
>      "metadata": {"source": "discord", "channel": "#hardware",
>                   "author": "ori", "permalink": "https://discord.example/4471"},
>      "scope": {"name": "default", "visibility": "public"},
>      "score": null, "signal": 0.4999999959893519,
>      "links": [], "supersedes": null, "lifecycle": "active",
>      "recall_count": 0, "last_recall": null, "added_by": "eidetic-cli",
>      "tier": "traversal", "depth": 1}
>   ]
> }
> ```
>
> Note `score` is `null` on a traversal item — it was never ranked against
> your query; it was reached by following `links` from the record above it.
>
> **The migration is one line:** read `payload["items"]` instead of `payload`.
>
> Everything this issue required is unchanged and still guaranteed: every item
> carries its full `metadata` (source, channel, author, timestamp, permalink),
> so cited answers keep working exactly as before. Ingest is untouched.
>
> **What's new and optional:** items with `tier: "traversal"` are records
> reached by following `links`/`supersedes` edges from a primary hit — the
> neighborhood of a match. If you only want what you had before, filter to
> `tier == "primary"`, or pass `--depth 0` to suppress traversal entirely.
> `--source <x>` is also new, if filtering the index by source is useful to you.

---

## Notice 2 — the `/recall` wrapper fan-out (~57 repos)

**Decided: one umbrella issue**, not a rollout-cli fan-out. The migration is a
single line (`.items`), and a wrapper that only *prints* recall output does not
break at all — so 57 PRs would cost far more triage than the change is worth.

**Target:** a new issue on `agentculture/eidetic-cli`, referenced from each
downstream repo as needed.

> ## `/recall` skill wrapper: update for the bundle-shaped recall payload
>
> `eidetic recall --json` changed shape in 0.13.0: the top-level payload is
> now a bundle object rather than a bare list of hits. Any wrapper or script
> that parses recall output needs a one-line change — read `.items` instead of
> treating the payload as the array.
>
> If your copy of the `/recall` wrapper only *prints* recall's output, nothing
> breaks and no action is needed. If it parses with `jq` or Python, update:
>
> ```bash
> # before
> eidetic recall "$q" --json | jq '.[0].text'
> # after
> eidetic recall "$q" --json | jq '.items[0].text'
> ```
>
> New optional flags you may want to surface in the wrapper: `--depth`
> (neighborhood hops, default 1), `--max-nodes` (traversal budget, default 20),
> `--source <x>` (filter by `metadata.source`). Passing `--depth 0` reproduces
> the old flat-search behavior exactly.
>
> The scope/visibility contract (docs/contract.md v1) is unchanged — the
> wrapper's `--scope`/`--visibility` defaults still apply, and the
> public/private no-leak invariant now holds per traversal hop as well.

---

## Notice 3 — comment on eidetic-cli#37 (tells embodiment the fetch is live)

**Target:** `agentculture/eidetic-cli` issue #37 —
`post-comment.sh --repo agentculture/eidetic-cli --number 37`

> ## Shipped in 0.13.0 — and the answer to your reinforcement question
>
> The composite fetch is live. It is `recall`'s **default** output rather than
> a separate verb: one call returns the bundle, so there is no second surface
> to adopt.
>
> **What you get per call:** `{query, mode, truncated, items[]}`, where each
> item carries its record id, full metadata, `score`, a `tier`
> (`primary` | `traversal`) and a `depth`. Bounds are caller-stated as you
> asked: `--depth` (default 1) and `--max-nodes` (default 20); hitting either
> sets `truncated: true` — never a silent cut, and never a cut announced for
> material you could not have been shown anyway.
>
> **Mapping to your four requested tiers:**
>
> 1. *Records* — `tier: "primary"` items.
> 2. *Vector-index lines* — each item's `text` is the record's own stored text,
>    byte-identical and cited by id. There is no persisted vector index or
>    sub-record chunking anywhere in the stack (embeddings are computed fresh
>    per query), so there are no sub-record lines to hand back. See the note
>    below — this one is now settled rather than pending.
> 3. *Graph exploration* — `tier: "traversal"` items, a bounded BFS over
>    `links`/`supersedes`. Today this walks the record fields **client-side**,
>    not the graph store's nodes, because data-refinery's neo4j backend stores
>    links as opaque JSON inside a node property with no relationships and no
>    traversal API.
> 4. *Node-linked lines* — the `text` of each traversal item, same as 2.
>
> **Upstream status, as of 2026-07-26.** We filed all of this as
> [data-refinery-cli#20](https://github.com/agentculture/data-refinery-cli/issues/20)
> and they have converged a spec against it (currently under evaluation, not
> yet merged). Two things follow for you:
>
> - **Tier 3 is being built properly.** Their spec ships a
>   `store.neighbors()` endpoint with caller-stated depth/size bounds, per-hop
>   scope filtering, and real `LINKS_TO`/`SUPERSEDES` relationships in neo4j.
>   eidetic's follow-up is to swap our client-side walk for a passthrough — the
>   bundle shape does not change, only the fidelity. Their own acceptance
>   criterion is that every existing eidetic traversal test passes unmodified
>   over the swap.
> - **Tiers 2 and 4 are being closed by decision, not built.** Their spec
>   records the rationale as *"record text is the atomic citable unit — a
>   record's text is a verbatim quote/copy backup of its source, and anything
>   LLM-written is degraded from source."* So no persisted chunk or embedding
>   shape is coming. Plan on the record as the citable unit rather than waiting
>   for sub-record lines; if you need finer granularity, the place to argue it
>   is that spec, while it is still under evaluation.
>
> That gives your `enrichment level` field a real value to model — **graph
> client-side** (today) vs **graph-native** (once their work lands) — and one
> value to stop waiting for.
>
> **Your reinforcement question, answered:** the composite fetch *does*
> reinforce, but **graded** — a primary hit bumps `recall_count` by 1.0, and a
> record discovered at hop-depth *d* bumps by `0.5**d` (so 0.5 at depth 1,
> 0.25 at depth 2). Traversal-discovered records therefore count as recalled,
> but weakly, decaying with distance from the match. The reasoning: a
> background compiler fetching neighborhoods shouldn't age the whole
> neighborhood as aggressively as a deliberate foreground hit, but a memory
> that keeps turning up adjacent to relevant matches genuinely is being used.
>
> **One thing to know about running this in a background loop:** recall's
> reinforcement writes to the store, and for public records inside a git repo
> that store is the committed one — so frequent background fetches will dirty
> the working tree. That is a pre-existing eidetic problem
> ([#24](https://github.com/agentculture/eidetic-cli/issues/24),
> [#32](https://github.com/agentculture/eidetic-cli/issues/32)), not new to the
> bundle, and the fix (a sidecar or a reinforcement gate) lands in those
> issues; the bundle will adopt whatever they settle on. If your muse thread
> fetches on a tight cadence before that lands, expect churn in
> `.eidetic/memory/*.jsonl`.
>
> Fetch-only holds in both directions as promised: no synthesis, no LLM call
> inside eidetic, and no write path for the compiler to propose associations
> back through.

---

## Open items

All settled — nothing here is still an open question.

1. **Notice 2 delivery** — *resolved:* one umbrella issue on `eidetic-cli`, not
   a rollout-cli fan-out across the ~57 `/recall` wrapper repos. The migration
   is one line, and wrappers that only print recall output are unaffected.
2. **Notice 1 target** — *resolved:* comment on closed issue #3, keeping the
   audit trail on the consumer contract thread that pinned the old shape.
3. **Timing** — *resolved:* all three post **after** PR #38 merges, so the
   0.13.0 they name is actually on `main`.
4. **Version placeholder** — *resolved:* **0.13.0** (`t6`'s minor bump).
5. **Payload authenticity** — *resolved:* the JSON in notice 1 is verbatim from
   a live run against a temporary store on the merged branch (`--mode keyword`,
   two linked records, one primary and one traversal) rather than invented.

Remaining gate: **the merge**. Once PR #38 lands, post notice 1 as a comment on
issue #3, notice 3 as a comment on issue #37, and notice 2 as a new umbrella
issue — all via the `communicate` skill, which appends the
`- eidetic-cli (Claude)` signature automatically.
