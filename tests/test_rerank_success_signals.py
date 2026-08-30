"""The spec's success-signal gate for the recall rerank stage (t8, claim c28).

Plan ``recall-rerank-stage-39`` states its success signal as three behaviours
that must hold once the stage ships, and is explicit that each must be its own
test: a single combined test can pass for the wrong reason, which is precisely
the failure this gate exists to prevent.

The three signals, and where each is pinned:

i.   With a fake remote lane scoring a distractor ``0.0``, ``--rerank`` with a
     threshold drops that distractor and reorders the survivors by
     ``rerank_score``, while each survivor's ``score`` keeps its search-mode
     (hybrid/BM25) value — :func:`test_signal_i_threshold_drops_the_zero_scored
     _distractor_and_reorders_survivors` below.
ii.  With the lane raising (i.e. not answering), ``--rerank`` exits non-zero
     with ``error:`` and ``hint:`` lines that NAME the API key variables —
     :func:`test_signal_ii_unanswered_lane_exits_non_zero_naming_the_key_vars`
     below.
iii. With the fallback explicitly permitted (``--rerank-allow-fallback``), the
     call succeeds, the payload marks the lexical lane, and the
     remote-calibrated threshold is NOT applied to lexical scores — already
     pinned exactly, and not duplicated here, by
     ``tests/test_recall_rerank.py::test_threshold_is_not_applied_to_lexical_fallback_scores``
     (that test asserts all three: a zero exit through ``args.func``, ``rerank
     == {"lane": "local", "dropped": 0}``, and that both records survive a
     ``--rerank-threshold 0.5`` far above their 0.03/0.01 lexical scores).

Signals i and ii each combine assertions that t5's suite makes in *separate*
tests (t5 pins reordering without a threshold, and the threshold drop without
asserting the search score survives; it pins the ``error:``/``hint:`` line
shape in text mode and the full variable list only in the JSON-mode sibling).
Each test here therefore asserts the signal as one behaviour, end to end.

Hermetic: the fakes and the ``$HOME``/``EIDETIC_DATA_DIR`` sandboxing are
reused verbatim from :mod:`tests.test_recall_rerank`, so no reranker,
embeddings endpoint, mongo, or neo4j is contacted and nothing is ever written
outside ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from eidetic.cli._commands import recall
from tests.test_recall_rerank import (  # noqa: F401  (fixtures used by injection)
    FakeEmbed,
    _IsolatedStore,
    _record,
    _reset_fallback_warning,
    _run,
    _seed,
    store,
)

# Seeds shared by signal i's two runs. Three records all matching "wetland"
# under BM25; no links, so no traversal tier muddies the assertions.
_SEEDS = [
    ("s-alpha", "wetland survey of the northern marsh"),
    ("s-beta", "wetland census notes"),
    ("s-distractor", "wetland drainage schedule for the district"),
]

# Deliberately the inverse of BM25's ordering for the two survivors, so an
# emitted order matching the reranker cannot be BM25 echoing back.
_REMOTE_SCORES = {
    "wetland survey of the northern marsh": 0.9,
    "wetland census notes": 0.6,
    "wetland drainage schedule for the district": 0.0,
}


def _seed_into(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the store at *data_dir* (inside tmp_path) and seed it."""
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(data_dir))
    _seed([_record(rid, text) for rid, text in _SEEDS])


def test_signal_i_threshold_drops_the_zero_scored_distractor_and_reorders_survivors(
    store: _IsolatedStore,  # noqa: F811  (pytest fixture)
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal (i): drop the 0.0 distractor, reorder by rerank_score, keep `score`.

    Two runs over two SEPARATE store dirs under ``tmp_path`` — a baseline
    without ``--rerank`` and the reranked run — because a recall reinforces the
    records it returns, and a second recall over the same store would then see
    a non-neutral freshness signal and a different (blended) ``score``. Running
    each against its own freshly seeded dir keeps the two ``score`` values
    directly comparable, which is what makes "the search score is preserved" an
    exact assertion rather than a shape check.
    """
    # --- baseline: the same query with the rerank stage switched off ---------
    _seed_into(tmp_path / "baseline", monkeypatch)
    baseline = _run(["wetland", "--mode", "keyword", "--json"], capsys)
    baseline_scores = {item["id"]: item["score"] for item in baseline["items"]}
    # Control: the distractor is a genuine BM25 hit, so its later absence is
    # the rerank threshold's doing and not the search mode's.
    assert set(baseline_scores) == {"s-alpha", "s-beta", "s-distractor"}
    assert all(item["rerank_score"] is None for item in baseline["items"])
    assert "rerank" not in baseline

    # --- the reranked run ---------------------------------------------------
    _seed_into(tmp_path / "reranked", monkeypatch)
    embed = FakeEmbed(_REMOTE_SCORES)
    payload = _run(
        [
            "wetland",
            "--mode",
            "keyword",
            "--rerank",
            "--rerank-threshold",
            "0.5",
            "--json",
        ],
        capsys,
        embed,
        monkeypatch,
    )
    items: list[dict[str, Any]] = payload["items"]

    # The distractor the remote lane scored 0.0 is gone, and the cut is
    # reported rather than silent.
    assert [item["id"] for item in items] == ["s-alpha", "s-beta"]
    assert payload["rerank"] == {"lane": "remote", "dropped": 1}
    # ...it reached the reranker in the first place (so it was dropped by the
    # threshold, not by never being a candidate).
    assert embed.rerank_calls, "the reranker was never called"
    _query, docs = embed.rerank_calls[0]
    assert "wetland drainage schedule for the district" in docs

    # The survivors are ordered by the reranker's judgement...
    assert [item["rerank_score"] for item in items] == [0.9, 0.6]
    # ...and that order disagrees with a descending sort on `score`, so the
    # rerank was load-bearing.
    by_search_score = [i["id"] for i in sorted(items, key=lambda i: -i["score"])]
    assert by_search_score != [item["id"] for item in items]

    # `score` keeps its hybrid/BM25 value — byte-identical to the baseline run.
    for item in items:
        assert item["score"] == baseline_scores[item["id"]]
        assert item["score"] != item["rerank_score"]


def test_signal_ii_unanswered_lane_exits_non_zero_naming_the_key_vars(
    store: _IsolatedStore,  # noqa: F811  (pytest fixture)
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal (ii): a lane that does not answer is a non-zero, actionable failure.

    Driven through ``eidetic.cli.main`` so the real error contract runs: a
    non-zero exit code, an empty stdout (no bundle may escape), no traceback,
    an ``error:`` first line, and a ``hint:`` line that names EVERY credential
    variable the embed client actually reads — a hint naming stale variables
    would send the operator to set a token nothing looks at.
    """
    from eidetic.cli import main
    from eidetic.memory.backend import get_backend

    _seed([_record("f-one", "wetland census notes")])
    embed = FakeEmbed({"wetland census notes": 0.9}, online=False)
    backend = get_backend("files", embed_client=embed)
    monkeypatch.setattr(recall, "get_backend", lambda *_a, **_kw: backend)

    rc = main(["recall", "wetland", "--mode", "keyword", "--rerank"])
    captured = capsys.readouterr()

    assert rc != 0
    assert captured.out == "", "no bundle may be emitted when the stage fails closed"
    assert "Traceback" not in captured.err

    lines = captured.err.splitlines()
    assert lines[0].startswith("error: ")
    hints = [line for line in lines if line.startswith("hint: ")]
    assert hints, f"no hint: line in stderr: {captured.err!r}"
    hint_text = "\n".join(hints)
    assert recall.RERANK_KEY_VARS, "the credential variable list must not be empty"
    for var in recall.RERANK_KEY_VARS:
        assert var in hint_text, f"{var} is not named in the hint: {hint_text!r}"
