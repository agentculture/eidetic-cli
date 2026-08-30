"""Pure rerank ordering + threshold engine for eidetic recall (t3).

Given a list of candidate records, the parallel list of rerank scores already
computed for them, and which lane produced those scores, return the records
ordered by score (descending) plus a count of how many an optional threshold
dropped. This module performs **no I/O**: it never calls a reranker, never
touches the network, never reads the clock, never imports a store, a backend,
or the embedding client, and never prints. It receives numbers and returns an
ordering, so it is deterministic and directly unit-testable, exactly like
:mod:`eidetic.memory.traverse` and :mod:`eidetic.memory.lifecycle`. The caller
(the recall command) is the one place that invokes the reranker, decides which
lane answered, and applies the returned order to its bundle.

Three rules carry the whole design:

1. **No threshold is the default.** ``--rerank`` REORDERS; it does not filter.
   Dropping is opt-in and requires the caller to pass an explicit cutoff
   (``--rerank-threshold``). With ``threshold=None`` every input record comes
   back and :attr:`RerankResult.dropped` is ``0``.

2. **A non-positive threshold is a no-op.** Reranker relevance scores are
   strictly positive — measured against the live remote lane, an utterly
   irrelevant document still scored ``3.2e-05``, not ``0.0``; a "0.000" in a
   report is display rounding. So a ``0.0`` cutoff drops **nothing**. This is
   enforced twice on purpose (see :func:`_effective_threshold`): a
   non-positive cutoff is resolved away to "no cutoff" before comparison, and
   the comparison itself is a strict ``>``. Do not "fix" either into a ``>=``
   — that would start silently discarding real, merely-weak hits.

3. **A remote-calibrated threshold is never applied to lexical-lane scores.**
   Both lanes emit numbers in ``0..1`` with nothing else in common: the remote
   cross-encoder scores ``0.968`` on-topic against ``3.2e-05`` for a
   distractor, while the offline Jaccard-overlap fallback scores ``0.034``
   against ``0.016``. A cutoff calibrated on the remote distribution would
   annihilate a perfectly good lexical result set, so when the fallback
   produced the scores, ``threshold`` is skipped entirely. A caller who has
   genuinely calibrated a cutoff FOR the fallback may pass it separately as
   ``lexical_threshold``; that one, and only that one, applies on that lane.

Ties are resolved deterministically: the sort is stable, so records with equal
scores keep their input order and repeated runs never shuffle them. A
records/scores length mismatch is a caller bug and raises ``ValueError``
rather than being silently zip-truncated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from eidetic.memory.record import Record

# Which lane produced the scores this engine is ordering by.
#
# "remote"  — the cross-encoder reranker answered over HTTP.
# "local"   — the deterministic lexical (Jaccard overlap) offline fallback.
#
# The lane is a first-class argument rather than something inferred from the
# numbers because the two distributions overlap numerically while meaning
# entirely different things (rule 3 above).
RerankLane = Literal["remote", "local"]

REMOTE_LANE: RerankLane = "remote"
LOCAL_LANE: RerankLane = "local"

_LANES: frozenset[str] = frozenset({REMOTE_LANE, LOCAL_LANE})


@dataclass
class RerankResult:
    """Outcome of :func:`apply_rerank`.

    ``records`` is the surviving subset of the input, ordered by rerank score
    descending (stable on ties). ``scores`` is the parallel list of those same
    records' scores, in the same order. ``dropped`` counts the records an
    applied threshold removed — always ``0`` when no threshold applied, which
    is the default and the shipped behaviour of ``--rerank``.
    """

    records: list[Record] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    dropped: int = 0


def _effective_threshold(
    lane: RerankLane,
    threshold: float | None,
    lexical_threshold: float | None,
) -> float | None:
    """Resolve which cutoff (if any) actually applies to *lane*'s scores.

    ``threshold`` is calibrated against the REMOTE cross-encoder distribution
    and is therefore ignored on the lexical lane, which has its own optional
    ``lexical_threshold`` (rule 3). A non-positive cutoff resolves to ``None``
    — no filtering at all — because reranker scores are strictly positive, so
    ``0.0`` can only have been meant as "no cutoff" (rule 2).
    """
    chosen = lexical_threshold if lane == LOCAL_LANE else threshold
    if chosen is None or chosen <= 0:
        return None
    return chosen


def apply_rerank(
    records: Sequence[Record],
    scores: Sequence[float],
    *,
    lane: RerankLane,
    threshold: float | None = None,
    lexical_threshold: float | None = None,
) -> RerankResult:
    """Order *records* by their *scores*, optionally dropping weak ones (PURE).

    *scores* must be positionally parallel to *records* — the score the
    reranker gave ``records[i]`` is ``scores[i]``. A length mismatch raises
    ``ValueError`` instead of zip-truncating, because a truncated zip would
    silently drop the tail of a result set.

    *lane* names which reranker produced the scores and gates the threshold
    (rule 3). *threshold*, when given and positive, keeps only records scoring
    strictly ABOVE it, and applies on the remote lane only; *lexical_threshold*
    is its lexical-lane counterpart and applies there only. The default —
    neither given — drops nothing and merely reorders (rule 1), and a
    non-positive cutoff is likewise inert (rule 2).

    The input records are never mutated and never re-scored here; the caller
    owns how the score is surfaced on the record.
    """
    if len(records) != len(scores):
        raise ValueError(
            f"rerank scores must be parallel to records: got {len(records)} "
            f"record(s) and {len(scores)} score(s)"
        )
    if lane not in _LANES:
        raise ValueError(f"unknown rerank lane {lane!r}; expected one of {sorted(_LANES)}")

    # Stable descending sort: equal scores keep their input order, so the
    # ordering is reproducible run to run.
    ordered = sorted(zip(records, scores), key=lambda pair: -pair[1])

    cutoff = _effective_threshold(lane, threshold, lexical_threshold)
    if cutoff is None:
        return RerankResult(
            records=[record for record, _ in ordered],
            scores=[score for _, score in ordered],
            dropped=0,
        )

    # Strictly greater than — see rule 2 in the module docstring before
    # changing this to `>=`.
    kept = [pair for pair in ordered if pair[1] > cutoff]
    return RerankResult(
        records=[record for record, _ in kept],
        scores=[score for _, score in kept],
        dropped=len(ordered) - len(kept),
    )
