"""Tests for eidetic.memory.scoring — the four recall modes."""

from __future__ import annotations

import pytest

from eidetic.cli._errors import CliError
from eidetic.memory.embed import EmbedClient
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope
from eidetic.memory.scoring import rank


def _offline_embed() -> EmbedClient:
    """An EmbedClient that always falls back (deterministic local embeddings)."""
    return EmbedClient(base_url="http://127.0.0.1:1/v1")


def _rec(rid: str, text: str) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata={},
        scope=Scope(name="default", visibility="public"),
    )


# -- exact -----------------------------------------------------------------


def test_exact_is_case_insensitive_by_default() -> None:
    cands = [_rec("a", "The Orin Nano draws 7W"), _rec("b", "Jetson AGX power")]
    out = rank("exact", "orin nano", cands, _offline_embed(), top_k=10)
    assert [r.id for r in out] == ["a"]
    assert out[0].score is not None and 0.0 < out[0].score <= 1.0


def test_exact_case_sensitive_excludes_wrong_case() -> None:
    cands = [_rec("a", "the orin nano"), _rec("b", "Orin Nano power")]
    out = rank("exact", "Orin Nano", cands, _offline_embed(), top_k=10, case_sensitive=True)
    assert [r.id for r in out] == ["b"]


def test_exact_ranks_tighter_match_higher() -> None:
    # "cat" covers all of record c (ratio 1.0) but little of the long doc.
    cands = [_rec("c", "cat"), _rec("d", "cat in the hat sat on a mat")]
    out = rank("exact", "cat", cands, _offline_embed(), top_k=10)
    assert [r.id for r in out] == ["c", "d"]
    assert out[0].score == 1.0
    assert out[1].score < out[0].score


def test_exact_drops_non_matches() -> None:
    cands = [_rec("a", "alpha"), _rec("b", "beta")]
    out = rank("exact", "gamma", cands, _offline_embed(), top_k=10)
    assert out == []


# -- keyword (BM25) --------------------------------------------------------


def test_keyword_drops_zero_overlap_and_scores_matches() -> None:
    cands = [
        _rec("a", "banana split is sweet"),
        _rec("b", "apple cherry pie"),
        _rec("c", "banana banana bread"),
    ]
    out = rank("keyword", "banana", cands, _offline_embed(), top_k=10)
    ids = {r.id for r in out}
    assert ids == {"a", "c"}  # b has no query term -> dropped
    assert all(r.score is not None and r.score > 0.0 for r in out)


def test_keyword_empty_candidates() -> None:
    assert rank("keyword", "x", [], _offline_embed(), top_k=5) == []


def test_keyword_strips_punctuation() -> None:
    # "Iceland." (trailing period) must still match the query "iceland".
    cands = [_rec("a", "Reykjavik is the capital of Iceland."), _rec("b", "no match here")]
    out = rank("keyword", "iceland", cands, _offline_embed(), top_k=10)
    assert [r.id for r in out] == ["a"]


# -- approximate (vector cosine, deterministic offline) --------------------


def test_approximate_identical_text_scores_highest() -> None:
    cands = [_rec("a", "quantum entanglement theory"), _rec("b", "banana bread recipe")]
    out = rank("approximate", "quantum entanglement theory", cands, _offline_embed(), top_k=10)
    assert out[0].id == "a"  # identical text -> cosine ~1.0
    assert out[0].score is not None and out[0].score > out[1].score


def test_approximate_returns_all_with_scores() -> None:
    cands = [_rec("a", "one"), _rec("b", "two"), _rec("c", "three")]
    out = rank("approximate", "number", cands, _offline_embed(), top_k=10)
    assert len(out) == 3
    assert all(r.score is not None for r in out)


# -- hybrid (weighted alpha blend) -----------------------------------------


def test_hybrid_offline_degrades_to_keyword_only() -> None:
    # Offline embeddings are hash junk -> alpha collapses to 0 (keyword-only).
    # The doc with the query term must rank above the one without, which scores 0.
    cands = [_rec("a", "jetson nano power modes"), _rec("b", "unrelated content here")]
    out = rank("hybrid", "jetson power", cands, _offline_embed(), top_k=10, alpha=0.9)
    assert out[0].id == "a"
    by_id = {r.id: r.score for r in out}
    assert by_id["a"] > 0.0
    assert by_id["b"] == 0.0  # no keyword overlap, offline so vector ignored


def test_hybrid_keeps_all_candidates() -> None:
    cands = [_rec("a", "alpha term"), _rec("b", "no overlap")]
    out = rank("hybrid", "alpha", cands, _offline_embed(), top_k=10)
    assert len(out) == 2  # unlike keyword mode, hybrid keeps zero-overlap docs


# -- guards ----------------------------------------------------------------


def test_unknown_mode_raises() -> None:
    with pytest.raises(CliError):
        rank("fuzzy", "q", [_rec("a", "x")], _offline_embed(), top_k=5)


def test_alpha_out_of_range_raises() -> None:
    with pytest.raises(CliError):
        rank("hybrid", "q", [_rec("a", "x")], _offline_embed(), top_k=5, alpha=1.5)


def test_top_k_limits_results() -> None:
    cands = [_rec(f"r{i}", f"shared term doc {i}") for i in range(6)]
    out = rank("keyword", "shared term", cands, _offline_embed(), top_k=3)
    assert len(out) == 3


# -- cross-backend consistency --------------------------------------------


def test_modes_identical_across_files_and_mongo(tmp_path, monkeypatch) -> None:
    """The same query+mode yields the same id ordering on files and mongo."""
    from eidetic.memory.backends.files import FilesBackend
    from eidetic.memory.backends.mongo import MongoBackend
    from tests.test_mongo_backend import _FakeClient

    monkeypatch.setenv("EIDETIC_EMBED_URL", "http://127.0.0.1:1/v1")
    records = [
        _rec("a", "jetson nano power consumption"),
        _rec("b", "orin agx thermal design"),
        _rec("c", "jetson power modes and nano clocks"),
    ]
    files = FilesBackend(base_dir=str(tmp_path / "mem"))
    mongo = MongoBackend(client=_FakeClient())
    for r in records:
        files.upsert(r)
        mongo.upsert(_rec(r.id, r.text))

    scope = Scope(name="default", visibility="public")
    for mode in ("exact", "keyword", "approximate", "hybrid"):
        q = "jetson nano" if mode == "exact" else "jetson power"
        fids = [r.id for r in files.search(q, 10, scope, None, mode)]
        mids = [r.id for r in mongo.search(q, 10, scope, None, mode)]
        assert fids == mids, f"mode {mode}: files={fids} mongo={mids}"
