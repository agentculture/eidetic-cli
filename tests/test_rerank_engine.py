"""Tests for eidetic.memory.rerank — the PURE rerank ordering/threshold engine.

These cover the t3 rules with no I/O:

1. **No threshold is the default.** Reranking reorders; it does not drop.
   Without an explicit cutoff every input record comes back and ``dropped``
   is 0.
2. **A 0.0 threshold is a no-op.** Reranker scores are strictly positive
   (an utterly irrelevant document measured 3.2e-05 against the live lane —
   the "0.000" seen in issue output is display rounding), so a 0.0 cutoff
   drops NOTHING.
3. **A remote-calibrated threshold is never applied to lexical-lane scores.**
   The two lanes share the 0..1 range with wildly different distributions, so
   a remote cutoff would annihilate a perfectly good lexical result set.

Plus: empty input, deterministic tie order, and a records/scores length
mismatch raising rather than silently zip-truncating.
"""

from __future__ import annotations

import inspect
from dataclasses import fields

import pytest

from eidetic.memory import rerank as rerank_mod
from eidetic.memory.record import Record
from eidetic.memory.rerank import LOCAL_LANE, REMOTE_LANE, RerankResult, apply_rerank
from eidetic.memory.scope import Scope

_PUBLIC = Scope(name="default", visibility="public")


def _rec(rid: str, *, text: str = "text") -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata={},
        scope=_PUBLIC,
    )


def _ids(result: RerankResult) -> list[str]:
    return [record.id for record in result.records]


# -- shape / purity -------------------------------------------------------


def test_rerank_result_shape_and_defaults() -> None:
    names = {f.name for f in fields(RerankResult)}
    assert names == {"records", "scores", "dropped"}
    empty = RerankResult()
    assert empty.records == []
    assert empty.scores == []
    assert empty.dropped == 0


def test_module_is_pure_no_io_imports() -> None:
    """The engine must not reach a store, a backend, the network, or the clock."""
    source = inspect.getsource(rerank_mod)
    for forbidden in (
        "import os",
        "data_refinery",
        "from eidetic.memory.backend",
        "from eidetic.memory.embed",
        "import datetime",
        "from datetime",
        "import requests",
        "urllib",
        "print(",
    ):
        assert forbidden not in source, f"rerank.py must not contain {forbidden!r}"


# -- behaviour 1: no threshold is the default -----------------------------


def test_no_threshold_is_the_default_nothing_is_dropped() -> None:
    """The shipped default: ``--rerank`` REORDERS, it does not filter.

    Dropping requires the caller to pass ``--rerank-threshold``; with no
    cutoff every input record comes back, merely reordered.
    """
    records = [_rec("a"), _rec("b"), _rec("c")]
    result = apply_rerank(records, [0.01, 0.97, 0.4], lane=REMOTE_LANE)
    assert _ids(result) == ["b", "c", "a"]
    assert result.dropped == 0
    assert len(result.records) == len(records)


def test_no_threshold_keeps_even_vanishingly_small_scores() -> None:
    records = [_rec("a"), _rec("b")]
    result = apply_rerank(records, [3.2e-05, 0.968], lane=REMOTE_LANE)
    assert _ids(result) == ["b", "a"]
    assert result.dropped == 0


def test_threshold_default_is_none_in_the_signature() -> None:
    """The no-drop path must be the natural, obvious default, not opt-in."""
    signature = inspect.signature(apply_rerank)
    assert signature.parameters["threshold"].default is None


# -- behaviour 2: a 0.0 threshold is a no-op ------------------------------


def test_zero_threshold_is_a_no_op_and_drops_nothing() -> None:
    """A 0.0 cutoff drops NOTHING — do NOT "fix" this into a ``>=``.

    Reranker relevance scores are strictly positive: measured against the live
    remote lane, an utterly irrelevant document still scored 3.2e-05, not 0.0.
    The issue that requested this feature printed such scores as "0.000",
    which is display rounding only. A future reader could easily assume a 0.0
    cutoff means "drop everything irrelevant" and flip the comparison to
    ``>=``; that would silently start discarding real, merely-weak hits. The
    contract is: 0.0 (or any non-positive cutoff) filters nothing.
    """
    records = [_rec("a"), _rec("b"), _rec("c")]
    scores = [3.2e-05, 0.968, 0.0]
    result = apply_rerank(records, scores, lane=REMOTE_LANE, threshold=0.0)
    assert _ids(result) == ["b", "a", "c"]
    assert result.dropped == 0
    assert len(result.records) == 3


def test_positive_threshold_does_drop_below_the_cutoff() -> None:
    """Control for the 0.0 case: a real cutoff genuinely filters."""
    records = [_rec("a"), _rec("b"), _rec("c")]
    result = apply_rerank(records, [3.2e-05, 0.968, 0.51], lane=REMOTE_LANE, threshold=0.5)
    assert _ids(result) == ["b", "c"]
    assert result.dropped == 1


def test_threshold_comparison_is_strictly_greater_than() -> None:
    """A score exactly equal to the cutoff is dropped (strict ``>``)."""
    records = [_rec("a"), _rec("b")]
    result = apply_rerank(records, [0.5, 0.6], lane=REMOTE_LANE, threshold=0.5)
    assert _ids(result) == ["b"]
    assert result.dropped == 1


# -- behaviour 3: a remote cutoff never touches lexical-lane scores -------


def test_remote_threshold_is_never_applied_to_lexical_lane_scores() -> None:
    """A remote-calibrated cutoff must NOT annihilate a lexical result set.

    Both lanes emit numbers in 0..1, but their distributions have nothing in
    common: the remote cross-encoder scores 0.968 on-topic vs 3.2e-05 for a
    distractor, while the local Jaccard-overlap fallback scores 0.034 vs
    0.016. A 0.5 cutoff calibrated on the remote lane would delete every
    lexical hit, good ones included. So when the fallback produced the scores,
    the remote threshold is skipped entirely.
    """
    records = [_rec("a"), _rec("b")]
    lexical_scores = [0.034, 0.016]  # a perfectly good lexical result set
    result = apply_rerank(records, lexical_scores, lane=LOCAL_LANE, threshold=0.5)
    assert _ids(result) == ["a", "b"]  # both SURVIVE the remote-calibrated cutoff
    assert result.dropped == 0


def test_same_scores_on_the_remote_lane_would_have_been_dropped() -> None:
    """Control: the lane, not the numbers, is what spares the lexical set."""
    records = [_rec("a"), _rec("b")]
    result = apply_rerank(records, [0.034, 0.016], lane=REMOTE_LANE, threshold=0.5)
    assert _ids(result) == []
    assert result.dropped == 2


def test_explicit_lexical_threshold_applies_on_the_lexical_lane() -> None:
    """A caller may still supply a cutoff calibrated FOR the lexical lane."""
    records = [_rec("a"), _rec("b")]
    result = apply_rerank(
        records,
        [0.034, 0.016],
        lane=LOCAL_LANE,
        threshold=0.5,
        lexical_threshold=0.02,
    )
    assert _ids(result) == ["a"]
    assert result.dropped == 1


def test_lexical_threshold_is_ignored_on_the_remote_lane() -> None:
    records = [_rec("a"), _rec("b")]
    result = apply_rerank(
        records,
        [0.968, 0.3],
        lane=REMOTE_LANE,
        threshold=0.5,
        lexical_threshold=0.9,
    )
    assert _ids(result) == ["a"]
    assert result.dropped == 1


# -- edges: empty, ties, length mismatch ----------------------------------


def test_empty_input_is_an_empty_result() -> None:
    result = apply_rerank([], [], lane=REMOTE_LANE, threshold=0.5)
    assert result.records == []
    assert result.scores == []
    assert result.dropped == 0


def test_ties_preserve_input_order_deterministically() -> None:
    records = [_rec("a"), _rec("b"), _rec("c"), _rec("d")]
    scores = [0.5, 0.9, 0.5, 0.5]
    first = apply_rerank(records, scores, lane=REMOTE_LANE)
    assert _ids(first) == ["b", "a", "c", "d"]
    # Stable: repeated runs (and a re-run over the returned order) never
    # shuffle equal scores.
    again = apply_rerank(records, scores, lane=REMOTE_LANE)
    assert _ids(again) == _ids(first)


def test_length_mismatch_raises_rather_than_zip_truncating_more_records() -> None:
    records = [_rec("a"), _rec("b"), _rec("c")]
    with pytest.raises(ValueError, match="records"):
        apply_rerank(records, [0.9, 0.1], lane=REMOTE_LANE)


def test_length_mismatch_raises_rather_than_zip_truncating_more_scores() -> None:
    records = [_rec("a")]
    with pytest.raises(ValueError, match="records"):
        apply_rerank(records, [0.9, 0.1], lane=REMOTE_LANE)


def test_unknown_lane_raises() -> None:
    records = [_rec("a")]
    with pytest.raises(ValueError, match="lane"):
        apply_rerank(records, [0.9], lane="quantum")  # type: ignore[arg-type]


def test_input_records_are_not_mutated() -> None:
    records = [_rec("a"), _rec("b")]
    apply_rerank(records, [0.1, 0.9], lane=REMOTE_LANE, threshold=0.5)
    assert [r.id for r in records] == ["a", "b"]
    assert all(r.score is None for r in records)


def test_scores_travel_alongside_the_reordered_records() -> None:
    records = [_rec("a"), _rec("b"), _rec("c")]
    result = apply_rerank(records, [0.1, 0.9, 0.4], lane=REMOTE_LANE)
    assert result.scores == [0.9, 0.4, 0.1]
    assert _ids(result) == ["b", "c", "a"]
