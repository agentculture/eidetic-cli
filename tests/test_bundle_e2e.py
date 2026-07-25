"""End-to-end success signals for the composite recall bundle (task t7).

This module turns the spec's success signal (claim c23) into automated checks
that run in ordinary ``pytest`` — no manual verification step and no live
external service. The flagship test seeds a small LINKED record graph spanning
BOTH a public and a private scope, runs **one** default ``recall --json``
through the real CLI (a subprocess, the way a consumer calls it), and asserts
all four bundle properties in that single run:

1. every item carries a record id plus a ``tier``/``depth`` label, so a
   consumer attributes every item without heuristics (h9);
2. zero private leakage — a private record reachable via ``links`` from a
   public hit never enters a public-visibility bundle (h13, c3/h3);
3. the caller's bounds are honored and a bound that cuts the walk reports
   ``truncated: true`` — never a silent cut (h16);
4. graded reinforcement is persisted: primary hits at a full 1.0 step and
   traversal discoveries at ``DECAY**depth`` (c24), read back from the STORE
   rather than from the emitted payload (which is deliberately pre-bump).

Three standalone regressions back it up: the planted-leak test (written so it
CANNOT go vacuous, see ``_UnfilteredStore``), the exact depth-1 *and* depth-2
bump values (proving the exponent, not merely "less than 1"), and an explicit
store-isolation check.

**Isolation.** Every test runs against ``EIDETIC_DATA_DIR`` pointed at a pytest
``tmp_path``, with ``$HOME`` redirected to a temp dir as well. The operator's
real stores — ``$HOME/.eidetic/memory`` and ``<repo>/.eidetic/memory``,
captured at import time below — are fingerprinted before and after a full
seed + recall cycle and asserted byte-identical. A previous incident had a test
pollute the operator's real store; this module refuses to repeat it.

**Determinism.** The bundle runs use ``--mode keyword`` (BM25, purely local).
The bound/tier/bump assertions all depend on exactly ONE record matching the
query lexically, and the vector modes would reach the embeddings endpoint — so
keyword mode is what keeps these checks hermetic and offline. Every *bundle*
setting (depth, max-nodes, top-k, tiers, reinforcement) is left at its default
in the flagship run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import pytest

import eidetic.memory.backend as be
from eidetic.cli._commands import recall
from eidetic.memory.backend import Backend, get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope
from eidetic.memory.scoring import DECAY

# ---------------------------------------------------------------------------
# The operator's REAL stores, resolved at import time — i.e. before any fixture
# redirects $HOME. No test in this module may read or write either of them; the
# isolation test fingerprints both and asserts they never change.
# ---------------------------------------------------------------------------

_REAL_HOME_STORE = Path.home() / ".eidetic" / "memory"
_REAL_REPO_STORE = Path(__file__).resolve().parents[1] / ".eidetic" / "memory"


# ---------------------------------------------------------------------------
# The seeded graph — one source of truth for every test in this module
# ---------------------------------------------------------------------------

_PUBLIC = Scope(name="default", visibility="public")
_PRIVATE = Scope(name="vault", visibility="private")

# Only the public seed matches this query lexically, so every other record can
# reach a bundle ONLY through the traversal.
_QUERY = "axolotl"

_SEED_ID = "e2e-pub-seed"  # primary hit, depth 0
_NEAR_ID = "e2e-pub-near"  # links neighbour, depth 1
_FAR_ID = "e2e-pub-far"  # links neighbour of the neighbour, depth 2
_OLDER_ID = "e2e-pub-older"  # supersedes neighbour, depth 1
_PRIV_ID = "e2e-priv-secret"  # PLANTED LEAK: private, linked from the public seed
_PRIV_DEEP_ID = "e2e-priv-annex"  # private, one hop further along the private chain

_PUBLIC_RECORDS: list[dict[str, Any]] = [
    {
        "id": _SEED_ID,
        "text": "axolotl census briefing for the wetland survey",
        "type": "discord",
        "metadata": {"source": "discord", "channel": "field-reports", "author": "ada"},
        "links": [_NEAR_ID, _PRIV_ID],
        "supersedes": _OLDER_ID,
    },
    {
        "id": _NEAR_ID,
        "text": "adjacent habitat field notes, one hop out",
        "type": "docs",
        "metadata": {"source": "docs", "permalink": "https://example.invalid/near"},
        "links": [_FAR_ID],
    },
    {
        "id": _FAR_ID,
        "text": "tertiary reference sheet, two hops out",
        "type": "docs",
        "metadata": {"source": "docs"},
    },
    {
        "id": _OLDER_ID,
        "text": "superseded earlier register of sightings",
        "type": "discord",
        "metadata": {"source": "discord"},
    },
]

_PRIVATE_RECORDS: list[dict[str, Any]] = [
    {
        "id": _PRIV_ID,
        "text": "classified roster dossier",
        "type": "note",
        "metadata": {"source": "discord"},
        "links": [_PRIV_DEEP_ID],
    },
    {
        "id": _PRIV_DEEP_ID,
        "text": "classified annex, deeper still",
        "type": "note",
        "metadata": {"source": "discord"},
    },
]

_ALL_IDS = {raw["id"] for raw in (*_PUBLIC_RECORDS, *_PRIVATE_RECORDS)}
_PRIVATE_IDS = {raw["id"] for raw in _PRIVATE_RECORDS}

# The private texts share this token and no public record does, so its absence
# from a payload is a direct check that no private CONTENT was emitted. (The
# private *ids* legitimately appear in the public seed's own ``links`` field,
# so an id-substring check would be wrong here.)
_PRIVATE_TEXT_MARKER = "classified"


# ---------------------------------------------------------------------------
# Isolation fixture + helpers
# ---------------------------------------------------------------------------


class _IsolatedStore(NamedTuple):
    """The temp store a test may touch, plus the temp ``$HOME`` beside it."""

    data_dir: Path
    home: Path


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _IsolatedStore:
    """Point the files backend at a temp dir and redirect ``$HOME`` beside it.

    ``EIDETIC_DATA_DIR`` short-circuits store resolution to exactly one
    directory, so neither the repo store nor the home store can be reached.
    ``$HOME`` is redirected anyway as defense in depth: were the override ever
    to stop winning, the fallback would land in ``tmp_path`` rather than in the
    operator's real store. ``monkeypatch`` restores both on teardown.
    """
    home = tmp_path / "home"
    home.mkdir()
    data_dir = tmp_path / "memory"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(data_dir))
    be._GIT_CACHE.clear()
    return _IsolatedStore(data_dir=data_dir, home=home)


def _fingerprint(path: Path) -> list[tuple[str, int, int]] | None:
    """Return a (name, size, mtime_ns) listing of *path*, or None if absent."""
    if not path.exists():
        return None
    return sorted(
        (child.name, child.stat().st_size, child.stat().st_mtime_ns) for child in path.iterdir()
    )


def _cli(
    args: list[str],
    store: _IsolatedStore,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m eidetic <args>`` against the isolated store."""
    env = {**os.environ, "EIDETIC_DATA_DIR": str(store.data_dir), "HOME": str(store.home)}
    return subprocess.run(
        [sys.executable, "-m", "eidetic", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _seed_via_cli(store: _IsolatedStore) -> None:
    """Ingest the whole graph through the real ``remember`` CLI (batch NDJSON)."""
    for records, scope in ((_PUBLIC_RECORDS, _PUBLIC), (_PRIVATE_RECORDS, _PRIVATE)):
        ndjson = "\n".join(json.dumps(raw) for raw in records)
        proc = _cli(
            ["remember", "--scope", scope.name, "--visibility", scope.visibility, "--json"],
            store,
            stdin=ndjson,
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["upserted"] == len(records)


def _record(raw: dict[str, Any], scope: Scope) -> Record:
    """Build a :class:`Record` from one entry of the seed graph."""
    return Record(
        id=raw["id"],
        text=raw["text"],
        type=raw["type"],
        hash="",
        metadata=dict(raw["metadata"]),
        scope=scope,
        links=list(raw.get("links", [])),
        supersedes=raw.get("supersedes"),
    )


def _seed_in_process() -> None:
    """Ingest the same graph directly through the backend (faster than the CLI).

    Writes the identical store the CLI seeder produces — the ``remember`` verb
    itself is covered by the flagship run and by ``tests/test_e2e_memory.py``.
    """
    backend = get_backend("files")
    for raw in _PUBLIC_RECORDS:
        backend.upsert(_record(raw, _PUBLIC))
    for raw in _PRIVATE_RECORDS:
        backend.upsert(_record(raw, _PRIVATE))


def _recall_bundle(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    """Run ``recall`` in-process (``--mode keyword --json``) and return the bundle."""
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    recall.register(sub)
    args = parser.parse_args(["recall", _QUERY, "--mode", "keyword", *argv, "--json"])
    assert args.func(args) == 0
    return json.loads(capsys.readouterr().out)


def _stored() -> dict[str, Record]:
    """Read every record straight back out of the store, by id."""
    return {record.id: record for record in get_backend("files").all()}


def _by_id(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in bundle["items"]}


# ---------------------------------------------------------------------------
# The flagship: one default recall, all four bundle properties
# ---------------------------------------------------------------------------


def test_one_default_recall_satisfies_every_bundle_property(store: _IsolatedStore) -> None:
    """The spec's success signal (c23), end to end, in a single default run.

    Seeds a linked graph across a public and a private scope through the real
    ``remember`` CLI, then runs ONE ``recall --json`` at the default bounds
    through a real subprocess — the composite fetch exactly as a consumer
    issues it — and checks all four properties against that one payload.
    """
    _seed_via_cli(store)

    proc = _cli(["recall", _QUERY, "--mode", "keyword", "--json"], store)
    assert proc.returncode == 0, proc.stderr
    bundle = json.loads(proc.stdout)

    # The payload is ONE bundle object, not a bare list.
    assert set(bundle) == {"query", "mode", "truncated", "items"}
    items = bundle["items"]
    by_id = _by_id(bundle)

    # -- property 1: every item is attributable without heuristics -----------
    assert items, "the seeded graph must produce a non-empty bundle"
    for item in items:
        assert item["id"], "every item must cite a record id"
        assert item["tier"] in {"primary", "traversal"}
        assert isinstance(item["depth"], int)
        assert isinstance(item["metadata"], dict), "provenance is mandatory (issue #3)"
    assert by_id[_SEED_ID]["tier"] == "primary"
    assert by_id[_SEED_ID]["depth"] == 0
    assert by_id[_NEAR_ID]["tier"] == "traversal", "a links neighbour is traversal-tier"
    assert by_id[_NEAR_ID]["depth"] == 1
    assert by_id[_OLDER_ID]["tier"] == "traversal", "a supersedes neighbour is traversal-tier"
    assert by_id[_OLDER_ID]["depth"] == 1
    # Provenance and text round-trip verbatim, so a consumer can cite them.
    assert by_id[_SEED_ID]["metadata"] == _PUBLIC_RECORDS[0]["metadata"]
    assert by_id[_NEAR_ID]["text"] == _PUBLIC_RECORDS[1]["text"]

    # -- property 2: zero private leakage ------------------------------------
    assert _PRIVATE_IDS.isdisjoint(by_id), "a private neighbour leaked into a public bundle"
    assert _PRIVATE_TEXT_MARKER not in proc.stdout, "private record text reached stdout"
    for item in items:
        assert item["scope"] == {"name": "default", "visibility": "public"}

    # -- property 3: bounds honored, and a cut is VISIBLE --------------------
    assert _FAR_ID not in by_id, "the default --depth 1 must stop one hop short of the far node"
    assert max(item["depth"] for item in items) == 1
    assert bundle["truncated"] is True, "a depth-bounded cut must be reported, never silent"

    # -- property 4: graded reinforcement PERSISTED --------------------------
    # Read the store back rather than trusting the payload: the emitted bundle
    # is deliberately pre-bump, which the first assertion below pins down.
    assert by_id[_SEED_ID]["recall_count"] == 0, "the emitted payload is pre-bump by design"
    stored = _stored()
    assert stored[_SEED_ID].recall_count == 1.0, "a primary hit steps by the full 1.0"
    assert stored[_SEED_ID].last_recall is not None
    assert stored[_NEAR_ID].recall_count == DECAY, "a depth-1 discovery bumps by DECAY**1"
    assert stored[_NEAR_ID].last_recall is not None
    assert stored[_OLDER_ID].recall_count == DECAY, "supersedes discoveries grade the same way"
    assert stored[_FAR_ID].recall_count == 0, "an unreached record is never reinforced"
    assert stored[_FAR_ID].last_recall is None
    for priv_id in _PRIVATE_IDS:
        assert stored[priv_id].recall_count == 0, "a scope-excluded record is never reinforced"
        assert stored[priv_id].last_recall is None

    # -- isolation, asserted inline in the flagship run itself ---------------
    assert be._candidate_read_dirs() == [str(store.data_dir)]
    assert not (store.home / ".eidetic").exists(), "nothing may be written outside the temp store"


# ---------------------------------------------------------------------------
# Planted-leak regression — written so it CANNOT go vacuous
# ---------------------------------------------------------------------------


class _UnfilteredStore:
    """A backend stand-in whose ``get_many`` deliberately IGNORES scope visibility.

    ``StoreBackend.get_many`` already applies ``can_serve`` per store dir, so a
    leak test written against the real backend passes even if the CLI's per-hop
    predicate were deleted outright — the store would have withheld the private
    record anyway. That test would be vacuous. This stand-in hands back every id
    it knows about, whatever its scope, so the ONLY thing that can keep the
    planted private record out of a public bundle is the ``can_serve``-bound
    predicate ``recall`` injects into ``discover``.

    Verified discriminating: replacing that predicate in
    ``eidetic/cli/_commands/recall.py`` with an always-admit lambda makes the
    test below FAIL on the private ids (see the t7 build report).
    """

    def __init__(self, inner: Backend) -> None:
        self._inner = inner
        # `all()` is the maintenance path — it bypasses scope filtering, which
        # is precisely what makes this stand-in leak by construction.
        self._records = {record.id: record for record in inner.all()}

    def upsert(self, record: Record) -> None:
        self._inner.upsert(record)

    def all(self) -> list[Record]:
        return self._inner.all()

    def search(self, *args: Any, **kwargs: Any) -> list[Record]:
        return self._inner.search(*args, **kwargs)

    def get_many(self, ids: list[str], scope: Scope) -> dict[str, Record]:
        return {rid: self._records[rid] for rid in ids if rid in self._records}


def test_planted_private_link_never_leaks_even_when_the_store_does_not_filter(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-hop predicate — not the store — is what stops the planted leak."""
    _seed_in_process()
    unfiltered = _UnfilteredStore(get_backend("files"))

    # CONTROL: the stand-in genuinely still leaks. Without this the assertions
    # below could pass for the wrong reason (a store that filtered after all).
    leaked = unfiltered.get_many(sorted(_PRIVATE_IDS), _PUBLIC)
    assert set(leaked) == _PRIVATE_IDS, "the stand-in must resolve private ids for a public scope"
    assert all(record.scope == _PRIVATE for record in leaked.values())

    monkeypatch.setattr(recall, "get_backend", lambda *_a, **_kw: unfiltered)
    bundle = _recall_bundle(capsys, "--depth", "3")
    by_id = _by_id(bundle)

    # Non-vacuity: the walk really ran, and ran deep enough that the private
    # chain (1 and 2 hops from the seed) was well inside the depth budget.
    assert _SEED_ID in by_id, "the public seed must still be a primary hit"
    assert _NEAR_ID in by_id and _FAR_ID in by_id, "the public chain must be walked to depth 2"

    assert _PRIV_ID not in by_id, "per-hop can_serve must reject the linked private record"
    assert _PRIV_DEEP_ID not in by_id, "a rejected neighbour must be a dead end, not a transit node"
    for item in bundle["items"]:
        assert item["scope"]["visibility"] == "public"
        assert _PRIVATE_TEXT_MARKER not in item["text"]


def test_planted_private_record_is_still_visible_to_its_owning_scope(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """The guard is scope-correct, not a blanket drop of everything linked.

    Without this, a traversal that simply refused to emit *any* linked record
    would pass the leak test above while destroying the feature.
    """
    _seed_in_process()
    bundle = _recall_bundle(
        capsys, "--scope", _PRIVATE.name, "--visibility", _PRIVATE.visibility, "--depth", "3"
    )
    by_id = _by_id(bundle)

    assert by_id[_PRIV_ID]["tier"] == "traversal"
    assert by_id[_PRIV_ID]["depth"] == 1
    assert by_id[_PRIV_DEEP_ID]["depth"] == 2, "the private chain is walked in its own scope"
    assert by_id[_PRIV_ID]["text"] == _PRIVATE_RECORDS[0]["text"]


# ---------------------------------------------------------------------------
# Exact graded bumps — the exponent, not merely "less than 1"
# ---------------------------------------------------------------------------


def test_persisted_bumps_are_exact_at_depth_one_and_depth_two(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """One ``--depth 2`` recall, then read the EXACT persisted counts back.

    Depth 1 and depth 2 are asserted in the same run so the *exponent* is
    pinned: a buggy flat fractional bump (0.5 at every hop) passes a
    "less than 1.0" check but fails the 0.25 assertion below.
    """
    _seed_in_process()
    _recall_bundle(capsys, "--depth", "2")
    stored = _stored()

    assert stored[_SEED_ID].recall_count == 1.0, "primary: full bump"
    assert stored[_NEAR_ID].recall_count == DECAY**1 == 0.5, "depth 1: DECAY**1"
    assert stored[_OLDER_ID].recall_count == DECAY**1 == 0.5, "depth 1 via supersedes: DECAY**1"
    assert stored[_FAR_ID].recall_count == DECAY**2 == 0.25, "depth 2: DECAY**2"

    # Discriminating: a depth-independent fraction would make these equal.
    assert stored[_FAR_ID].recall_count != stored[_NEAR_ID].recall_count
    assert stored[_FAR_ID].recall_count == stored[_NEAR_ID].recall_count * DECAY

    for record_id in (_SEED_ID, _NEAR_ID, _OLDER_ID, _FAR_ID):
        assert stored[record_id].last_recall is not None, "every bumped record is stamped"
    for priv_id in _PRIVATE_IDS:
        assert stored[priv_id].recall_count == 0
        assert stored[priv_id].last_recall is None


# ---------------------------------------------------------------------------
# Isolation — the real stores are never read from or written to
# ---------------------------------------------------------------------------


def test_the_e2e_run_never_touches_the_real_repo_or_home_store(
    store: _IsolatedStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """A full seed + recall cycle leaves the operator's real stores untouched."""
    before = (_fingerprint(_REAL_HOME_STORE), _fingerprint(_REAL_REPO_STORE))

    _seed_in_process()
    _recall_bundle(capsys, "--depth", "2")

    # Store resolution cannot even reach the real dirs while the override is set.
    assert be._candidate_read_dirs() == [str(store.data_dir)]
    assert be._resolve_write_dir("public") == str(store.data_dir)
    assert be._resolve_write_dir("private") == str(store.data_dir)
    assert str(_REAL_HOME_STORE) != str(store.data_dir)
    assert str(_REAL_REPO_STORE) != str(store.data_dir)

    # Everything the run wrote is in the temp store, and nowhere else.
    assert set(_stored()) == _ALL_IDS
    assert not (store.home / ".eidetic").exists()

    after = (_fingerprint(_REAL_HOME_STORE), _fingerprint(_REAL_REPO_STORE))
    assert (
        after == before
    ), "the real home/repo stores changed during the test — isolation is broken"
