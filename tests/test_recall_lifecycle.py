"""Tests for t3 recall features: lifecycle filtering, signal in output, passive reinforcement.

These tests are written FIRST (TDD) and exercise:
1. Default recall excludes shadowed/archived records.
2. --include-shadowed brings back shadowed records.
3. --include-archived brings back archived records.
4. Each hit in JSON output carries a numeric 'signal' field.
5. After a recall hit, the store shows bumped recall_count and set last_recall,
   while the first recall's OWN emitted payload is unchanged (pre-bump).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from eidetic.cli._commands import recall
from eidetic.memory.backend import get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _make_record(
    rid: str,
    text: str,
    lifecycle: str = "active",
    recall_count: int = 0,
    last_recall: str | None = None,
    scope: Scope | None = None,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata={},
        scope=scope or Scope(name="default", visibility="public"),
        lifecycle=lifecycle,
        recall_count=recall_count,
        last_recall=last_recall,
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> str:
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


@pytest.fixture
def mixed_lifecycle_seeded(data_dir: str) -> None:
    """Seed records with different lifecycle states."""
    backend = get_backend("files")
    backend.upsert(_make_record("active1", "active record one"))
    backend.upsert(_make_record("active2", "active record two"))
    backend.upsert(_make_record("shadowed1", "shadowed record one", lifecycle="shadowed"))
    backend.upsert(_make_record("archived1", "archived record one", lifecycle="archived"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    recall.register(sub)
    return parser


# ---------------------------------------------------------------------------
# 1. Lifecycle filtering
# ---------------------------------------------------------------------------


def test_default_recall_excludes_shadowed(
    data_dir: str, mixed_lifecycle_seeded: None, capsys
) -> None:
    """Default recall must NOT return shadowed records."""
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    ids = {h["id"] for h in hits}
    assert "shadowed1" not in ids, "default recall must exclude shadowed records"


def test_default_recall_excludes_archived(
    data_dir: str, mixed_lifecycle_seeded: None, capsys
) -> None:
    """Default recall must NOT return archived records."""
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    ids = {h["id"] for h in hits}
    assert "archived1" not in ids, "default recall must exclude archived records"


def test_default_recall_returns_active_records(
    data_dir: str, mixed_lifecycle_seeded: None, capsys
) -> None:
    """Default recall must still return active records."""
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    ids = {h["id"] for h in hits}
    assert "active1" in ids or "active2" in ids, "default recall must return active records"


def test_include_shadowed_flag_returns_shadowed(
    data_dir: str, mixed_lifecycle_seeded: None, capsys
) -> None:
    """--include-shadowed should bring back shadowed records."""
    parser = _build_parser()
    args = parser.parse_args(
        ["recall", "record", "--mode", "exact", "--include-shadowed", "--json"]
    )
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    ids = {h["id"] for h in hits}
    assert "shadowed1" in ids, "--include-shadowed must include shadowed records"
    # Archived must still be excluded unless --include-archived is also passed
    assert "archived1" not in ids, "--include-shadowed alone must not include archived records"


def test_include_archived_flag_returns_archived(
    data_dir: str, mixed_lifecycle_seeded: None, capsys
) -> None:
    """--include-archived should bring back archived records."""
    parser = _build_parser()
    args = parser.parse_args(
        ["recall", "record", "--mode", "exact", "--include-archived", "--json"]
    )
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    ids = {h["id"] for h in hits}
    assert "archived1" in ids, "--include-archived must include archived records"
    # Shadowed must still be excluded unless --include-shadowed is also passed
    assert "shadowed1" not in ids, "--include-archived alone must not include shadowed records"


def test_both_flags_return_all_lifecycle_states(
    data_dir: str, mixed_lifecycle_seeded: None, capsys
) -> None:
    """--include-shadowed --include-archived should return all lifecycle states."""
    parser = _build_parser()
    args = parser.parse_args(
        [
            "recall",
            "record",
            "--mode",
            "exact",
            "--include-shadowed",
            "--include-archived",
            "--json",
        ]
    )
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    ids = {h["id"] for h in hits}
    assert "active1" in ids or "active2" in ids
    assert "shadowed1" in ids
    assert "archived1" in ids


def test_lifecycle_filter_applied_before_top_k(data_dir: str, capsys) -> None:
    """Lifecycle filter must apply BEFORE top-k truncation.

    Seed 3 active + 2 shadowed records with --top-k 3. Without filtering,
    top-k would be 3 and might include shadowed. After lifecycle-filtering only
    active records are in the candidate set, so top-k sees at most 3 active.
    """
    backend = get_backend("files")
    for i in range(3):
        backend.upsert(_make_record(f"act{i}", f"active record {i}"))
    for i in range(2):
        backend.upsert(_make_record(f"shd{i}", f"shadowed active record {i}", lifecycle="shadowed"))

    parser = _build_parser()
    # --top-k 3 should give us 3 active records (not shadowed ones even though
    # they also match the query "active record")
    args = parser.parse_args(
        ["recall", "active record", "--mode", "exact", "--top-k", "3", "--json"]
    )
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    for hit in hits:
        assert (
            hit.get("lifecycle", "active") == "active"
        ), "lifecycle filtering must exclude shadowed even with top-k=3"


# ---------------------------------------------------------------------------
# 2. Signal in output
# ---------------------------------------------------------------------------


def test_recall_json_output_has_numeric_signal(
    data_dir: str, mixed_lifecycle_seeded: None, capsys
) -> None:
    """Each hit in JSON output must carry a numeric 'signal' field."""
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    assert len(hits) > 0, "there should be some hits for this query"
    for hit in hits:
        assert "signal" in hit, f"hit {hit['id']} missing 'signal' field"
        assert isinstance(
            hit["signal"], (int, float)
        ), f"hit {hit['id']} signal must be numeric, got {type(hit['signal'])}"


def test_recall_signal_in_valid_range(data_dir: str, mixed_lifecycle_seeded: None, capsys) -> None:
    """Signal must be in [0, 1]."""
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    for hit in hits:
        sig = hit["signal"]
        assert 0.0 <= sig <= 1.0, f"signal {sig} out of [0, 1] range for hit {hit['id']}"


# ---------------------------------------------------------------------------
# 3. Passive reinforcement (write-on-read)
# ---------------------------------------------------------------------------


def test_passive_reinforcement_bumps_recall_count(data_dir: str, capsys) -> None:
    """After a recall hit, the store must show recall_count incremented."""
    backend = get_backend("files")
    backend.upsert(_make_record("reinforce1", "reinforcement test record", recall_count=0))

    parser = _build_parser()
    args = parser.parse_args(["recall", "reinforcement", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    first_hits = json.loads(capsys.readouterr().out)
    assert any(h["id"] == "reinforce1" for h in first_hits), "record should be a hit"

    # Now do a second recall and check recall_count advanced
    args2 = parser.parse_args(["recall", "reinforcement", "--mode", "exact", "--json"])
    rc2 = args2.func(args2)
    assert rc2 == 0
    second_hits = json.loads(capsys.readouterr().out)
    hit2 = next(h for h in second_hits if h["id"] == "reinforce1")
    # After the first recall bumped recall_count to 1, the second recall emits
    # the state *at query time* (which is 1) and bumps to 2 in the store.
    # So the second recall's emitted recall_count should be 1.
    assert (
        hit2["recall_count"] == 1
    ), f"second recall should see recall_count=1 (set by first recall), got {hit2['recall_count']}"


def test_passive_reinforcement_sets_last_recall(data_dir: str, capsys) -> None:
    """After a recall hit, the store must show last_recall set (not None)."""
    backend = get_backend("files")
    backend.upsert(_make_record("lastrecall1", "last recall test record", last_recall=None))

    parser = _build_parser()
    args = parser.parse_args(["recall", "last recall", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    first_hits = json.loads(capsys.readouterr().out)
    assert any(h["id"] == "lastrecall1" for h in first_hits), "record should be a hit"

    # Second recall sees last_recall now set (from the first recall's bump)
    args2 = parser.parse_args(["recall", "last recall", "--mode", "exact", "--json"])
    rc2 = args2.func(args2)
    assert rc2 == 0
    second_hits = json.loads(capsys.readouterr().out)
    hit2 = next(h for h in second_hits if h["id"] == "lastrecall1")
    assert (
        hit2["last_recall"] is not None
    ), "second recall should see last_recall set (by first recall's reinforcement)"


def test_first_recall_emits_pre_bump_recall_count(data_dir: str, capsys) -> None:
    """The FIRST recall's own emitted payload must show pre-bump recall_count.

    The record is stored with recall_count=0.  The first recall bumps the store
    to 1, but the emitted payload for THIS call should still show 0 (the value
    at query time, before reinforcement).
    """
    backend = get_backend("files")
    backend.upsert(_make_record("prebump1", "pre bump test record", recall_count=0))

    parser = _build_parser()
    args = parser.parse_args(["recall", "pre bump", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    hit = next(h for h in hits if h["id"] == "prebump1")
    assert (
        hit["recall_count"] == 0
    ), f"first recall's emitted recall_count should be 0 (pre-bump), got {hit['recall_count']}"


def test_first_recall_emits_pre_bump_last_recall(data_dir: str, capsys) -> None:
    """The FIRST recall's own emitted payload must show last_recall=None (pre-bump)."""
    backend = get_backend("files")
    backend.upsert(_make_record("prebump2", "last recall pre bump record", last_recall=None))

    parser = _build_parser()
    args = parser.parse_args(["recall", "last recall pre", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    hit = next(h for h in hits if h["id"] == "prebump2")
    assert (
        hit["last_recall"] is None
    ), f"first recall's emitted last_recall should be None (pre-bump), got {hit['last_recall']}"


def test_reinforcement_verified_by_second_recall(data_dir: str, capsys) -> None:
    """Full round-trip: first recall bumps, second recall reads the bumped state."""
    backend = get_backend("files")
    backend.upsert(_make_record("roundtrip1", "roundtrip reinforcement record", recall_count=5))

    parser = _build_parser()

    # First recall: emits recall_count=5 (pre-bump), bumps store to 6
    args1 = parser.parse_args(["recall", "roundtrip reinforcement", "--mode", "exact", "--json"])
    rc = args1.func(args1)
    assert rc == 0
    hits1 = json.loads(capsys.readouterr().out)
    hit1 = next(h for h in hits1 if h["id"] == "roundtrip1")
    assert (
        hit1["recall_count"] == 5
    ), f"first recall should emit pre-bump count=5, got {hit1['recall_count']}"

    # Second recall: emits recall_count=6 (the bumped value), bumps store to 7
    args2 = parser.parse_args(["recall", "roundtrip reinforcement", "--mode", "exact", "--json"])
    rc2 = args2.func(args2)
    assert rc2 == 0
    hits2 = json.loads(capsys.readouterr().out)
    hit2 = next(h for h in hits2 if h["id"] == "roundtrip1")
    assert (
        hit2["recall_count"] == 6
    ), f"second recall should emit bumped count=6, got {hit2['recall_count']}"


def test_passive_reinforcement_does_not_persist_score_or_signal(data_dir: str, capsys) -> None:
    """Recall reinforcement must write durable state only — never the query-time
    score/signal (score is recall-output-only; signal is recomputed each query).

    Regression for qodo "Recall upsert() persists score": the bumped copy used
    to carry the ranked score and the output signal straight into the store.
    """
    backend = get_backend("files")
    backend.upsert(_make_record("noleak1", "score leak regression record"))

    parser = _build_parser()
    args = parser.parse_args(["recall", "score leak regression", "--mode", "exact", "--json"])
    assert args.func(args) == 0
    emitted = json.loads(capsys.readouterr().out)
    # The emitted hit still exposes both (output contract is unchanged)...
    hit = next(h for h in emitted if h["id"] == "noleak1")
    assert hit["score"] is not None
    assert hit["signal"] is not None

    # ...but the persisted record must carry neither.
    stored = next(r for r in get_backend("files").all() if r.id == "noleak1")
    assert stored.score is None, "score must never be persisted (recall-output-only)"
    assert stored.signal is None, "signal must never be persisted (recomputed at query time)"
    # Durable reinforcement state was still written.
    assert stored.recall_count == 1
    assert stored.last_recall is not None
