"""Tests for eidetic.memory.scoring signal strength + freshness blend (t2).

The signal function ports the proven QQ near-linear model and must be pure and
deterministic: identical ``created`` / ``last_recall`` / ``recall_count`` /
``links`` plus the same ``now`` always yield the same number, with no persisted
mutable cache.

The ranking blend has one critical back-compat invariant: a record whose
temporal data is neutral/absent (``created == DATE_UNKNOWN`` AND
``recall_count == 0`` AND ``last_recall is None``) must score and order EXACTLY
as it does without the blend — the freshness term is identity on neutral signal.
Only records carrying real temporal data get a freshness adjustment.
"""

from __future__ import annotations

import pytest

from eidetic.memory.embed import EmbedClient
from eidetic.memory.record import DATE_UNKNOWN, Record
from eidetic.memory.scope import Scope
from eidetic.memory.scoring import (
    CORROBORATION_WEIGHT,
    DECAY_RATE,
    rank,
    signal_strength,
)

# A fixed reference "now" so every test is deterministic.
NOW = "2026-06-20T12:00:00+00:00"


def _offline_embed() -> EmbedClient:
    """An EmbedClient that always falls back (deterministic local embeddings)."""
    return EmbedClient(base_url="http://127.0.0.1:1/v1")


def _rec(
    rid: str,
    text: str,
    *,
    created: str = DATE_UNKNOWN,
    last_recall: str | None = None,
    recall_count: int = 0,
    links: list[str] | None = None,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata={},
        scope=Scope(name="default", visibility="public"),
        created=created,
        last_recall=last_recall,
        recall_count=recall_count,
        links=links or [],
    )


# -- signal_strength: purity + determinism ---------------------------------


def test_signal_strength_is_deterministic() -> None:
    rec = _rec(
        "a",
        "x",
        created="2026-06-01T00:00:00+00:00",
        last_recall="2026-06-10T00:00:00+00:00",
        recall_count=3,
    )
    first = signal_strength(rec, NOW)
    second = signal_strength(rec, NOW)
    assert first == second


def test_signal_strength_does_not_mutate_record() -> None:
    rec = _rec("a", "x", created="2026-06-01T00:00:00+00:00", recall_count=2)
    before = rec.to_dict()
    signal_strength(rec, NOW)
    # No persisted/cached score smuggled onto the record.
    assert rec.to_dict() == before
    assert rec.signal is None


def test_signal_strength_is_clamped_to_unit_range() -> None:
    # A very stale, never-recalled, very old record must not go below 0.
    stale = _rec(
        "old",
        "x",
        created="2000-01-01T00:00:00+00:00",
        last_recall="2000-01-01T00:00:00+00:00",
        recall_count=0,
    )
    s = signal_strength(stale, NOW)
    assert 0.0 <= s <= 1.0


# -- DATE_UNKNOWN: decay-neutral age ---------------------------------------


def test_date_unknown_is_age_neutral() -> None:
    """An undated record gets no age penalty: its age_factor is 1.0.

    A freshly-created record (created == now) with the same recall stats should
    score identically to the undated one, because the only difference between
    them is the age term, which DATE_UNKNOWN neutralises to 1.0.
    """
    undated = _rec("u", "x", created=DATE_UNKNOWN, recall_count=2, last_recall=NOW)
    fresh = _rec("f", "x", created=NOW, recall_count=2, last_recall=NOW)
    assert signal_strength(undated, NOW) == pytest.approx(signal_strength(fresh, NOW))


def test_unparseable_created_is_age_neutral() -> None:
    garbage = _rec("g", "x", created="not-a-date", recall_count=1, last_recall=NOW)
    fresh = _rec("f", "x", created=NOW, recall_count=1, last_recall=NOW)
    assert signal_strength(garbage, NOW) == pytest.approx(signal_strength(fresh, NOW))


# -- recall_count / freshness shape ----------------------------------------


def test_more_recalls_raise_signal() -> None:
    low = _rec("lo", "x", created=NOW, recall_count=0, last_recall=NOW)
    high = _rec("hi", "x", created=NOW, recall_count=5, last_recall=NOW)
    assert signal_strength(high, NOW) > signal_strength(low, NOW)


def test_recent_recall_beats_stale_recall() -> None:
    recent = _rec("r", "x", created=NOW, last_recall=NOW, recall_count=1)
    stale = _rec("s", "x", created=NOW, last_recall="2025-01-01T00:00:00+00:00", recall_count=1)
    assert signal_strength(recent, NOW) > signal_strength(stale, NOW)


def test_fresher_creation_beats_older_creation() -> None:
    fresh = _rec("f", "x", created=NOW, recall_count=1, last_recall=NOW)
    old = _rec("o", "x", created="2024-01-01T00:00:00+00:00", recall_count=1, last_recall=NOW)
    assert signal_strength(fresh, NOW) > signal_strength(old, NOW)


# -- tunable constants ------------------------------------------------------


def test_decay_rate_is_a_tunable_constant() -> None:
    assert isinstance(DECAY_RATE, float)
    assert 0.0 < DECAY_RATE < 1.0


def test_corroboration_weight_is_a_tunable_constant() -> None:
    # Frame v7 magnitude undecided -> defaults to a no-op (0).
    assert isinstance(CORROBORATION_WEIGHT, float)
    assert CORROBORATION_WEIGHT == 0.0


# -- blend: identity on neutral, freshness-adjusted otherwise ---------------


def test_blend_is_identity_on_neutral_records_keyword() -> None:
    """Neutral records (no temporal data) keep today's keyword score + order."""
    cands = [
        _rec("a", "banana split is sweet"),
        _rec("c", "banana banana bread"),
    ]
    baseline = rank("keyword", "banana", cands, _offline_embed(), top_k=10)
    baseline_order = [r.id for r in baseline]
    baseline_scores = {r.id: r.score for r in baseline}

    # Re-rank with an explicit now: neutral records must not move.
    again = rank("keyword", "banana", cands, _offline_embed(), top_k=10, now=NOW)
    assert [r.id for r in again] == baseline_order
    for r in again:
        assert r.score == pytest.approx(baseline_scores[r.id])


def test_blend_is_identity_on_neutral_records_exact() -> None:
    cands = [_rec("c", "cat"), _rec("d", "cat in the hat sat on a mat")]
    baseline = rank("exact", "cat", cands, _offline_embed(), top_k=10)
    again = rank("exact", "cat", cands, _offline_embed(), top_k=10, now=NOW)
    assert [r.id for r in again] == [r.id for r in baseline]
    for b, a in zip(baseline, again):
        assert a.score == pytest.approx(b.score)


def test_fresher_record_ranks_above_older_identical_text() -> None:
    """Two candidates identical except for ``created``: fresher wins."""
    fresh = _rec("fresh", "banana bread recipe", created=NOW, recall_count=0)
    old = _rec(
        "old",
        "banana bread recipe",
        created="2023-01-01T00:00:00+00:00",
        recall_count=0,
    )
    # last_recall must differ from None to make these non-neutral? created alone
    # is enough to make them non-neutral (created != DATE_UNKNOWN).
    out = rank("keyword", "banana bread", [old, fresh], _offline_embed(), top_k=10, now=NOW)
    assert [r.id for r in out] == ["fresh", "old"]
    assert out[0].score > out[1].score


def test_neutral_record_ranks_exactly_as_today_against_dated_peer() -> None:
    """A neutral record's own score is unchanged even when a dated peer is present.

    The blend touches only the dated record; the neutral record keeps its exact
    lexical score. We hold the candidate SET fixed (BM25 stats are corpus-
    dependent) and compare a blended run against an all-neutral run of the same
    set: the neutral record's score must be byte-identical between them, while
    the dated record is free to move.
    """
    neutral = _rec("neutral", "banana bread recipe")
    dated = _rec("dated", "banana bread recipe", created=NOW, recall_count=4, last_recall=NOW)
    dated_neutralised = _rec("dated", "banana bread recipe")  # same text, no temporal data

    cands_blended = [neutral, dated]
    cands_all_neutral = [_rec("neutral", "banana bread recipe"), dated_neutralised]

    blended = rank("keyword", "banana bread", cands_blended, _offline_embed(), top_k=10, now=NOW)
    all_neutral = rank(
        "keyword", "banana bread", cands_all_neutral, _offline_embed(), top_k=10, now=NOW
    )

    neutral_blended = next(r for r in blended if r.id == "neutral").score
    neutral_baseline = next(r for r in all_neutral if r.id == "neutral").score
    assert neutral_blended == pytest.approx(neutral_baseline)

    # And the dated peer DID move (proving the blend is live, not a global no-op).
    dated_blended = next(r for r in blended if r.id == "dated").score
    dated_baseline = next(r for r in all_neutral if r.id == "dated").score
    assert dated_blended != pytest.approx(dated_baseline)


def test_blend_defaults_now_at_entry_point() -> None:
    """rank() with no now still works (defaults to current time only at entry).

    Neutral records are unaffected regardless of what 'now' is, so the result
    must match the explicit-now neutral baseline.
    """
    cands = [_rec("a", "banana split"), _rec("c", "banana bread")]
    out_default = rank("keyword", "banana", cands, _offline_embed(), top_k=10)
    out_explicit = rank("keyword", "banana", cands, _offline_embed(), top_k=10, now=NOW)
    assert [r.id for r in out_default] == [r.id for r in out_explicit]
