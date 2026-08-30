"""Tests for the ``eidetic recall --rerank`` stage (t5).

The rerank stage is an OPT-IN second pass over the primary tier: after the
search mode ranks the store and the lifecycle filter removes shadowed/archived
records, the top ``--rerank-pool`` survivors are handed to the cross-encoder
reranker, reordered by its judgement, optionally cut by ``--rerank-threshold``,
and only THEN sliced to ``--top-k``.

Two properties of that order are load-bearing and each has its own test below:

* the rerank runs **after** the lifecycle filter, so a shadowed or archived
  record is never sent to the reranker (it is not merely hidden afterwards);
* the rerank runs **before** the ``--top-k`` slice, so the pool can *rescue* a
  record the hybrid/BM25 ranking put below k — which is the entire reason the
  pool exists and is wider than k.

Everything here is hermetic. A fake embed client is injected through the named
:attr:`~eidetic.memory.backend.Backend.embed_client` seam (never the private
``_embed`` attribute), so no reranker, embeddings endpoint, mongo, or neo4j is
contacted. ``$HOME`` and ``EIDETIC_DATA_DIR`` are both redirected into
``tmp_path``: a previous incident had a test in this repo write into the
operator's real ``~/.eidetic/memory``, and
:func:`test_rerank_tests_never_touch_the_real_stores` fingerprints both real
stores to make sure it cannot happen again.

Determinism: every recall here runs ``--mode keyword`` (BM25, purely local) and
every seeded record carries the ``DATE_UNKNOWN`` created sentinel with
``recall_count == 0``, which makes the freshness signal an exact, stable 0.5
and bypasses the multiplicative blend entirely. That is what lets
:func:`test_default_recall_bundle_is_byte_identical_golden` assert a literal
payload rather than a fuzzy shape.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from eidetic.cli._commands import recall
from eidetic.memory.backend import get_backend
from eidetic.memory.record import DATE_UNKNOWN, Record
from eidetic.memory.scope import Scope

# Resolved at import time — i.e. before any fixture redirects $HOME — so the
# isolation test compares against the operator's genuinely real locations.
_REAL_HOME_STORE = Path.home() / ".eidetic" / "memory"
_REAL_REPO_STORE = Path(__file__).resolve().parents[1] / ".eidetic" / "memory"

_PUBLIC = Scope(name="default", visibility="public")


# ---------------------------------------------------------------------------
# Fakes + fixtures
# ---------------------------------------------------------------------------


class FakeEmbed:
    """Stand-in embed client injected through the ``embed_client`` seam.

    ``rerank_detect`` returns whatever *score_map* says for each document text
    (defaulting to 0.0 for anything unlisted) together with the *online* flag
    the test wants — ``True`` impersonating the remote cross-encoder lane,
    ``False`` the offline lexical fallback. Every call is recorded so a test
    can assert exactly which documents reached the reranker.
    """

    def __init__(self, score_map: dict[str, float] | None = None, online: bool = True) -> None:
        self.score_map = score_map or {}
        self.online = online
        self.rerank_calls: list[tuple[str, list[str]]] = []
        self.embed_calls: list[list[str]] = []

    def embed_detect(self, texts: list[str]) -> tuple[list[list[float]], bool]:
        self.embed_calls.append(texts)
        return [[1.0, 0.0] for _ in texts], True

    def rerank_detect(self, query: str, docs: list[str]) -> tuple[list[float], bool]:
        self.rerank_calls.append((query, list(docs)))
        return [self.score_map.get(doc, 0.0) for doc in docs], self.online

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        return self.rerank_detect(query, docs)[0]


class _IsolatedStore(NamedTuple):
    data_dir: Path
    home: Path


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _IsolatedStore:
    """Point BOTH candidate store dirs at ``tmp_path``.

    ``EIDETIC_DATA_DIR`` short-circuits store resolution to one temp dir, and
    ``$HOME`` is redirected too so that even a code path which ignored the
    explicit dir would land in the sandbox rather than the operator's store.
    """
    home = tmp_path / "home"
    home.mkdir()
    data_dir = tmp_path / "memory"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(data_dir))
    return _IsolatedStore(data_dir=data_dir, home=home)


@pytest.fixture(autouse=True)
def _reset_fallback_warning() -> None:
    """Clear the once-per-process fallback warning latch between tests.

    The warning deliberately fires at most once per process (mirroring
    :mod:`eidetic.memory.embed`'s withheld-key warning), so a test asserting it
    must start from a clean latch regardless of test order.
    """
    recall._warned_rerank_fallback = False


def _record(
    rid: str,
    text: str,
    *,
    lifecycle: str = "active",
    links: list[str] | None = None,
    created: str = DATE_UNKNOWN,
    recall_count: int | float = 0,
    scope: Scope = _PUBLIC,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata={"source": "docs"},
        scope=scope,
        lifecycle=lifecycle,
        links=links or [],
        created=created,
        recall_count=recall_count,
    )


def _seed(records: list[Record], embed: FakeEmbed | None = None) -> None:
    backend = get_backend("files", embed_client=embed or FakeEmbed())
    for record in records:
        backend.upsert(record)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    recall.register(sub)
    return parser


def _run(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
    embed: FakeEmbed | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> dict[str, Any]:
    """Run ``recall`` in-process with *embed* injected through the seam."""
    args = _parser().parse_args(["recall", *argv])
    if embed is not None:
        assert monkeypatch is not None, "injecting an embed client needs monkeypatch"
        backend = get_backend("files", embed_client=embed)
        monkeypatch.setattr(recall, "get_backend", lambda *_a, **_kw: backend)
    assert args.func(args) == 0
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# 1. The golden: a default recall (no --rerank) is unchanged
# ---------------------------------------------------------------------------

# Seeds chosen so exactly two records match "wetland" under BM25, with no links
# (hence no traversal) and the DATE_UNKNOWN/never-recalled neutral signal.
_GOLDEN_SEEDS = [
    ("g-one", "wetland survey of the northern marsh"),
    ("g-two", "wetland census notes"),
    ("g-three", "unrelated kitchen inventory"),
]

_GOLDEN_PAYLOAD: dict[str, Any] = {
    "query": "wetland",
    "mode": "keyword",
    "truncated": False,
    "items": [
        {
            "id": "g-two",
            "text": "wetland census notes",
            "type": "note",
            "hash": Record._hash("wetland census notes"),
            "metadata": {"source": "docs"},
            "scope": {"name": "default", "visibility": "public"},
            "score": 0.5295815540797021,
            "rerank_score": None,
            "created": DATE_UNKNOWN,
            "last_recall": None,
            "recall_count": 0,
            "links": [],
            "supersedes": None,
            "lifecycle": "active",
            "signal": 0.5,
            "added_by": None,
            "tier": "primary",
            "depth": 0,
        },
        {
            "id": "g-one",
            "text": "wetland survey of the northern marsh",
            "type": "note",
            "hash": Record._hash("wetland survey of the northern marsh"),
            "metadata": {"source": "docs"},
            "scope": {"name": "default", "visibility": "public"},
            "score": 0.3836764320373352,
            "rerank_score": None,
            "created": DATE_UNKNOWN,
            "last_recall": None,
            "recall_count": 0,
            "links": [],
            "supersedes": None,
            "lifecycle": "active",
            "signal": 0.5,
            "added_by": None,
            "tier": "primary",
            "depth": 0,
        },
    ],
}


def test_default_recall_bundle_is_byte_identical_golden(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--rerank`` the bundle is EXACTLY what it was before the stage.

    This is the regression fence for the whole task: the rerank stage must add
    no key, drop no key, reorder nothing, and change no number on the default
    path. Asserting a literal payload (not a shape) is what makes that binding
    — the neutral signal and BM25's determinism keep the literal stable.
    """
    _seed([_record(rid, text) for rid, text in _GOLDEN_SEEDS])
    payload = _run(["wetland", "--mode", "keyword", "--json"], capsys)
    assert payload == _GOLDEN_PAYLOAD


# ---------------------------------------------------------------------------
# 2. --rerank reorders, publishes rerank_score, and preserves the search score
# ---------------------------------------------------------------------------


def test_rerank_reorders_and_keeps_the_search_score(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """--rerank orders by rerank_score; `score` keeps its search-mode value.

    The emitted order is asserted to DIFFER from a descending sort on `score`,
    which is what proves the reranker's judgement actually drove the ordering
    rather than the bundle merely echoing BM25 back.
    """
    _seed(
        [
            _record("r-one", "wetland survey of the northern marsh"),
            _record("r-two", "wetland census notes"),
            _record("r-three", "wetland drainage and the marsh census"),
        ]
    )
    # Deliberately the inverse of what BM25 will decide.
    embed = FakeEmbed(
        {
            "wetland survey of the northern marsh": 0.9,
            "wetland drainage and the marsh census": 0.6,
            "wetland census notes": 0.2,
        }
    )
    payload = _run(
        ["wetland", "--mode", "keyword", "--rerank", "--json"], capsys, embed, monkeypatch
    )
    items = payload["items"]

    assert [i["id"] for i in items] == ["r-one", "r-three", "r-two"]
    assert [i["rerank_score"] for i in items] == [0.9, 0.6, 0.2]
    # The hybrid/BM25 score survives untouched on every item...
    for item in items:
        assert isinstance(item["score"], float)
        assert item["score"] != item["rerank_score"]
    # ...and it disagrees with the emitted order, so the rerank was load-bearing.
    by_search_score = [i["id"] for i in sorted(items, key=lambda i: -i["score"])]
    assert by_search_score != [i["id"] for i in items]
    # The lane is reported and nothing was cut.
    assert payload["rerank"] == {"lane": "remote", "dropped": 0}


# ---------------------------------------------------------------------------
# 3. Pipeline order, part one: lifecycle filter runs BEFORE the reranker
# ---------------------------------------------------------------------------


def test_shadowed_and_archived_records_never_reach_the_reranker(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lifecycle-hidden record is not merely dropped after reranking — it is
    never sent to the reranker at all.

    Pins ``search -> lifecycle filter -> rerank``. Asserting only on the emitted
    bundle would pass even if the order were reversed (the record would be
    filtered afterwards); asserting on the DOCUMENTS the fake reranker was
    handed is what makes this test order-sensitive.
    """
    _seed(
        [
            _record("l-active", "wetland census notes"),
            _record("l-shadowed", "wetland shadowed duplicate", lifecycle="shadowed"),
            _record("l-archived", "wetland archived duplicate", lifecycle="archived"),
        ]
    )
    embed = FakeEmbed({"wetland census notes": 0.9})
    payload = _run(
        ["wetland", "--mode", "keyword", "--rerank", "--json"], capsys, embed, monkeypatch
    )

    assert len(embed.rerank_calls) == 1
    _query, docs = embed.rerank_calls[0]
    assert docs == ["wetland census notes"]
    assert "wetland shadowed duplicate" not in docs
    assert "wetland archived duplicate" not in docs
    assert [i["id"] for i in payload["items"]] == ["l-active"]


def test_include_shadowed_does_send_the_shadowed_record_to_the_reranker(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control for the test above: the exclusion is the lifecycle filter's doing.

    Without this, ``test_shadowed_and_archived_records_never_reach_the_reranker``
    could pass vacuously (e.g. if the shadowed record never matched the query in
    the first place). With ``--include-shadowed`` the very same record DOES
    reach the reranker.
    """
    _seed(
        [
            _record("l-active", "wetland census notes"),
            _record("l-shadowed", "wetland shadowed duplicate", lifecycle="shadowed"),
        ]
    )
    embed = FakeEmbed({"wetland census notes": 0.9, "wetland shadowed duplicate": 0.8})
    _run(
        ["wetland", "--mode", "keyword", "--rerank", "--include-shadowed", "--json"],
        capsys,
        embed,
        monkeypatch,
    )
    _query, docs = embed.rerank_calls[0]
    assert "wetland shadowed duplicate" in docs


# ---------------------------------------------------------------------------
# 4. Pipeline order, part two: the reranker runs BEFORE the --top-k slice
# ---------------------------------------------------------------------------


def test_rerank_pool_promotes_a_record_ranked_below_top_k(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool RESCUES a record the search mode ranked below --top-k.

    This is the reason ``--rerank-pool`` is wider than k. If the stage ran after
    the ``[:top_k]`` slice it could only reshuffle an already-decided answer and
    the promoted record would be unreachable — so this test fails outright on
    the wrong pipeline order rather than merely looking different.
    """
    _seed(
        [
            _record("p-strong", "wetland wetland wetland census"),
            _record("p-middle", "wetland survey notes"),
            _record("p-weak", "a passing mention of wetland in the appendix index"),
        ]
    )
    # Establish the baseline: without rerank, top-k 1 returns the BM25 winner
    # and p-weak is nowhere near it.
    baseline = _run(["wetland", "--mode", "keyword", "--top-k", "1", "--json"], capsys)
    assert [i["id"] for i in baseline["items"]] == ["p-strong"]

    embed = FakeEmbed(
        {
            "a passing mention of wetland in the appendix index": 0.99,
            "wetland wetland wetland census": 0.10,
            "wetland survey notes": 0.05,
        }
    )
    payload = _run(
        ["wetland", "--mode", "keyword", "--top-k", "1", "--rerank", "--json"],
        capsys,
        embed,
        monkeypatch,
    )
    # All three candidates were pooled (the pool is not the top-k slice)...
    assert len(embed.rerank_calls[0][1]) == 3
    # ...and the record BM25 ranked last is the single hit returned.
    assert [i["id"] for i in payload["items"]] == ["p-weak"]
    assert payload["items"][0]["rerank_score"] == 0.99


# ---------------------------------------------------------------------------
# 5. Fail closed when the remote lane does not answer
# ---------------------------------------------------------------------------


def test_rerank_fails_closed_without_the_fallback_opt_in(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unanswered remote reranker is an ERROR, not a silent downgrade.

    Run through ``eidetic.cli.main`` (not the handler) so the whole contract is
    exercised: non-zero exit, ``error:``/``hint:`` on stderr, the credential
    VARIABLE named in the hint, nothing on stdout, and no traceback.
    """
    from eidetic.cli import main

    _seed([_record("f-one", "wetland census notes")])
    embed = FakeEmbed({"wetland census notes": 0.9}, online=False)
    backend = get_backend("files", embed_client=embed)
    monkeypatch.setattr(recall, "get_backend", lambda *_a, **_kw: backend)

    rc = main(["recall", "wetland", "--mode", "keyword", "--rerank", "--json"])
    captured = capsys.readouterr()

    assert rc != 0
    assert captured.out == "", "no bundle may be emitted when the stage fails closed"
    assert "Traceback" not in captured.err
    payload = json.loads(captured.err)
    assert payload["code"] == rc
    assert "did not answer" in payload["message"]
    # The hint names the key VARIABLES...
    for var in recall.RERANK_KEY_VARS:
        assert var in payload["remediation"]
    assert "--rerank-allow-fallback" in payload["remediation"]


def test_rerank_fail_closed_text_mode_emits_error_and_hint_lines(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same failure in text mode renders the rubric's ``error:``/``hint:`` pair."""
    from eidetic.cli import main

    _seed([_record("f-one", "wetland census notes")])
    embed = FakeEmbed({"wetland census notes": 0.9}, online=False)
    backend = get_backend("files", embed_client=embed)
    monkeypatch.setattr(recall, "get_backend", lambda *_a, **_kw: backend)

    rc = main(["recall", "wetland", "--mode", "keyword", "--rerank"])
    captured = capsys.readouterr()

    assert rc != 0
    assert captured.out == ""
    assert "Traceback" not in captured.err
    lines = captured.err.splitlines()
    assert lines[0].startswith("error: ")
    assert any(line.startswith("hint: ") for line in lines)
    assert "EIDETIC_EMBED_API_KEY" in captured.err


def test_fail_closed_remediation_never_interpolates_a_key_value(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolved bearer token must never reach the error message.

    The message goes to stderr, agent logs, and CI output; a leaked credential
    there is unrecoverable. Every candidate variable is set to a distinctive
    sentinel and the whole rendered error is checked for it.
    """
    from eidetic.cli import main

    sentinel = "sk-do-not-leak-me-0123456789"
    for var in recall.RERANK_KEY_VARS:
        monkeypatch.setenv(var, sentinel)

    _seed([_record("f-one", "wetland census notes")])
    embed = FakeEmbed({"wetland census notes": 0.9}, online=False)
    backend = get_backend("files", embed_client=embed)
    monkeypatch.setattr(recall, "get_backend", lambda *_a, **_kw: backend)

    assert main(["recall", "wetland", "--mode", "keyword", "--rerank"]) != 0
    captured = capsys.readouterr()
    assert sentinel not in captured.err
    assert sentinel not in captured.out


def test_rerank_key_vars_match_the_embed_clients_own_resolution_order() -> None:
    """Drift guard: the variables the hint names are the ones the client reads.

    A remediation that named stale variables would send the operator to set a
    token nothing reads, which is worse than no hint at all.
    """
    from eidetic.memory import embed as embed_module

    assert recall.RERANK_KEY_VARS == embed_module._API_KEY_VARS


# ---------------------------------------------------------------------------
# 6. Fallback permitted: the bundle names the lane and stderr carries a warning
# ---------------------------------------------------------------------------


def test_permitted_fallback_marks_the_lexical_lane_and_warns_once(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the opt-in, the lexical lane succeeds — loudly and legibly.

    Three things must all hold: the call succeeds, the payload names the LOCAL
    lane (a consumer must be able to tell the two score distributions apart),
    and a warning reaches stderr exactly once per process.
    """
    _seed(
        [
            _record("w-one", "wetland survey of the northern marsh"),
            _record("w-two", "wetland census notes"),
        ]
    )
    embed = FakeEmbed(
        {"wetland survey of the northern marsh": 0.03, "wetland census notes": 0.01},
        online=False,
    )
    backend = get_backend("files", embed_client=embed)
    monkeypatch.setattr(recall, "get_backend", lambda *_a, **_kw: backend)
    argv = [
        "wetland",
        "--mode",
        "keyword",
        "--rerank",
        "--rerank-allow-fallback",
        "--json",
    ]

    args = _parser().parse_args(["recall", *argv])
    assert args.func(args) == 0
    first = capsys.readouterr()

    payload = json.loads(first.out)
    assert payload["rerank"] == {"lane": "local", "dropped": 0}
    assert [i["id"] for i in payload["items"]] == ["w-one", "w-two"]
    assert "warning:" in first.err
    assert "lexical" in first.err

    # Once per process: a second call in the same process stays quiet.
    args2 = _parser().parse_args(["recall", *argv])
    assert args2.func(args2) == 0
    second = capsys.readouterr()
    assert second.err == ""


def test_threshold_is_not_applied_to_lexical_fallback_scores(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote-calibrated cutoff would annihilate the lexical lane's results.

    The lexical scores here (0.03 / 0.01) all sit far below a plausible remote
    cutoff of 0.5; on the local lane that cutoff must be skipped entirely, so
    nothing is dropped.
    """
    _seed(
        [
            _record("w-one", "wetland survey of the northern marsh"),
            _record("w-two", "wetland census notes"),
        ]
    )
    embed = FakeEmbed(
        {"wetland survey of the northern marsh": 0.03, "wetland census notes": 0.01},
        online=False,
    )
    payload = _run(
        [
            "wetland",
            "--mode",
            "keyword",
            "--rerank",
            "--rerank-allow-fallback",
            "--rerank-threshold",
            "0.5",
            "--json",
        ],
        capsys,
        embed,
        monkeypatch,
    )
    assert payload["rerank"] == {"lane": "local", "dropped": 0}
    assert len(payload["items"]) == 2


# ---------------------------------------------------------------------------
# 7. Threshold: the cut is reported, and a dropped record leaves no trace
# ---------------------------------------------------------------------------


def _stored(rid: str) -> Record:
    """Read *rid* straight back out of the store (post-reinforcement state)."""
    backend = get_backend("files", embed_client=FakeEmbed())
    found = backend.get_many([rid], _PUBLIC)
    assert rid in found, f"{rid} is missing from the store"
    return found[rid]


def test_threshold_reports_the_drop_and_the_dropped_record_leaves_no_trace(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relevance cut is reported, and the cut record is gone from every path.

    Issue #37 established that a bound cutting the traversal short reports
    ``truncated`` — never a silent cut. A threshold cut is the same class of
    thing, so the count is published. The dropped record must then be absent
    from BOTH tiers, must not seed the traversal (its own neighbour must not
    appear either), and must not be reinforced — all three are asserted, the
    last read back from the STORE rather than from the payload.
    """
    _seed(
        [
            _record("t-keep", "wetland census notes", links=["t-neighbour"]),
            _record(
                "t-drop",
                "wetland survey of the northern marsh",
                links=["t-orphan"],
            ),
            _record("t-neighbour", "companion record for the kept hit"),
            _record("t-orphan", "companion record for the dropped hit"),
        ]
    )
    embed = FakeEmbed({"wetland census notes": 0.9, "wetland survey of the northern marsh": 0.1})
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

    # The cut is reported, never silent.
    assert payload["rerank"] == {"lane": "remote", "dropped": 1}
    # Absent from every tier — and so is the neighbour it would have seeded.
    ids = [i["id"] for i in payload["items"]]
    assert ids == ["t-keep", "t-neighbour"]
    assert "t-drop" not in ids
    assert "t-orphan" not in ids
    # Never reinforced: read the durable state back out of the store.
    dropped = _stored("t-drop")
    assert dropped.recall_count == 0
    assert dropped.last_recall is None
    # Control: the records that DID survive were reinforced, so a
    # "nothing was ever bumped" bug cannot make the assertion above vacuous.
    assert _stored("t-keep").recall_count == 1.0
    assert _stored("t-keep").last_recall is not None
    assert _stored("t-neighbour").recall_count == 0.5


def test_no_threshold_drops_nothing(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --rerank-threshold the stage reorders and never filters.

    The companion to the test above: the same weak record (0.1) survives when
    no cutoff is in force, which is what makes dropping strictly opt-in.
    """
    _seed(
        [
            _record("t-keep", "wetland census notes"),
            _record("t-drop", "wetland survey of the northern marsh"),
        ]
    )
    embed = FakeEmbed({"wetland census notes": 0.9, "wetland survey of the northern marsh": 0.1})
    payload = _run(
        ["wetland", "--mode", "keyword", "--rerank", "--json"], capsys, embed, monkeypatch
    )
    assert payload["rerank"]["dropped"] == 0
    assert [i["id"] for i in payload["items"]] == ["t-keep", "t-drop"]


# ---------------------------------------------------------------------------
# 8. The freshness blend does not re-run under --rerank
# ---------------------------------------------------------------------------


def test_a_temporal_record_scores_identically_with_and_without_rerank(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """--rerank must not re-apply the freshness blend to `score`.

    A dated, previously-recalled record has a non-neutral signal, so its `score`
    carries the multiplicative blend. If the rerank stage re-blended (or wrote
    the rerank score over `score`), that number would move. The store is
    re-seeded between the two runs so the first run's own reinforcement bump
    cannot be mistaken for a rerank effect.
    """
    temporal = [
        _record(
            "s-dated",
            "wetland census notes",
            created="2020-01-01T00:00:00+00:00",
            recall_count=4,
        )
    ]

    _seed(temporal)
    plain = _run(["wetland", "--mode", "keyword", "--json"], capsys)
    plain_item = plain["items"][0]
    assert plain_item["signal"] != 0.5, "the fixture must be non-neutral for this to bite"

    # Re-seed: the plain run above bumped recall_count, which would otherwise
    # change the signal (and hence the blended score) on the second run.
    _seed(temporal)
    embed = FakeEmbed({"wetland census notes": 0.77})
    reranked = _run(
        ["wetland", "--mode", "keyword", "--rerank", "--json"], capsys, embed, monkeypatch
    )
    reranked_item = reranked["items"][0]

    # `approx` only absorbs the sub-microsecond age drift between the two runs
    # clocks (relative ~1e-12); a re-applied blend would move the score by the
    # blend's own factor, orders of magnitude outside this tolerance.
    assert reranked_item["score"] == pytest.approx(plain_item["score"], rel=1e-9)
    assert reranked_item["signal"] == pytest.approx(plain_item["signal"], rel=1e-9)
    assert reranked_item["rerank_score"] == 0.77
    assert plain_item["rerank_score"] is None


# ---------------------------------------------------------------------------
# 9. --rerank composes with every search mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["exact", "approximate", "keyword", "hybrid"])
def test_rerank_is_accepted_with_every_mode(
    mode: str,
    store: _IsolatedStore,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage is a post-pass over the primary tier, so no mode is excluded.

    ``exact`` and ``keyword`` are the interesting ones: they do not otherwise
    touch the embed client at all, and a stage wired into the vector path only
    would silently no-op (or crash) here.
    """
    _seed(
        [
            _record("m-one", "wetland census notes"),
            _record("m-two", "wetland survey of the northern marsh"),
        ]
    )
    embed = FakeEmbed({"wetland census notes": 0.2, "wetland survey of the northern marsh": 0.8})
    payload = _run(["wetland", "--mode", mode, "--rerank", "--json"], capsys, embed, monkeypatch)
    assert payload["mode"] == mode
    assert payload["rerank"]["lane"] == "remote"
    assert [i["id"] for i in payload["items"]] == ["m-two", "m-one"]


# ---------------------------------------------------------------------------
# 10. Isolation: none of this may reach the operator's real stores
# ---------------------------------------------------------------------------


def _fingerprint(root: Path) -> list[tuple[str, int, float]]:
    if not root.exists():
        return []
    return sorted(
        (str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime)
        for p in root.rglob("*")
        if p.is_file()
    )


def test_rerank_tests_never_touch_the_real_stores(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full seed + rerank cycle leaves both real stores byte-identical.

    A previous incident in this repo had a test write into the operator's own
    ``~/.eidetic/memory``. This module refuses to repeat it: the real home and
    repo stores are fingerprinted around a complete run, and the redirected
    ``$HOME`` is checked to be untouched as well.
    """
    before_home = _fingerprint(_REAL_HOME_STORE)
    before_repo = _fingerprint(_REAL_REPO_STORE)

    _seed([_record("i-one", "wetland census notes")])
    embed = FakeEmbed({"wetland census notes": 0.9})
    _run(["wetland", "--mode", "keyword", "--rerank", "--json"], capsys, embed, monkeypatch)

    assert _fingerprint(_REAL_HOME_STORE) == before_home
    assert _fingerprint(_REAL_REPO_STORE) == before_repo
    assert not (store.home / ".eidetic").exists()
    assert store.data_dir.exists(), "the sandbox store is where the writes went"
