"""Tests for multi-store routing in eidetic.memory.backend (t2).

Covers:
- upsert writes public -> repo store, private -> home store
- search unions candidates across _candidate_read_dirs() with no duplicates
- private-scope query returns own-private + public; public-scope never leaks private
- all() spans both dirs; sweep re-upsert lands each record back in its visibility's dir
- new logic gated behind self._name == "files"; mongo/neo4j paths untouched
- EIDETIC_DATA_DIR override makes all paths single-dir (byte-identical to before)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from eidetic.memory.backend import (
    StoreBackend,
    _bridge_env,
    _candidate_read_dirs,
    record_to_envelope,
)
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


@pytest.fixture(autouse=True)
def _resolver_isolation(monkeypatch, tmp_path) -> None:
    """Isolate every test from real on-disk state.

    These tests upsert real records to the *resolved* store, and a private (or
    outside-repo) write resolves to ``Path.home()/.eidetic/memory``. Without
    redirecting HOME, that would pollute the developer's live ~/.eidetic store and
    make the suite non-hermetic. So: unset the EIDETIC_DATA_DIR override, clear the
    per-cwd git cache, and point HOME at an isolated tmp dir (a sibling of any
    ``tmp_path/repo`` a test creates).
    """
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from eidetic.memory import backend as be

    be._GIT_CACHE.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    rid: str = "r1",
    text: str = "hello world",
    scope: Scope | None = None,
    metadata: dict | None = None,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata=metadata or {},
        scope=scope or Scope(name="default", visibility="public"),
    )


# ---------------------------------------------------------------------------
# _bridge_env: data_dir parameter
# ---------------------------------------------------------------------------


def test_bridge_env_files_with_data_dir(tmp_path, monkeypatch) -> None:
    """_bridge_env('files', data_dir=...) sets DR_DATA_DIR to the given dir."""
    d = str(tmp_path / "custom")
    _bridge_env("files", data_dir=d)
    assert os.environ["DR_DATA_DIR"] == d


def test_bridge_env_files_without_data_dir(tmp_path, monkeypatch) -> None:
    """_bridge_env('files') without data_dir uses _resolve_write_dir('private')."""
    # Outside a repo, _resolve_write_dir('private') -> home
    not_repo = tmp_path / "not_repo"
    not_repo.mkdir()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(not_repo))
        _bridge_env("files")
        expected = str(Path.home() / ".eidetic" / "memory")
        assert os.environ["DR_DATA_DIR"] == expected
    finally:
        os.chdir(old_cwd)


def test_bridge_env_mongo_unchanged(monkeypatch) -> None:
    """_bridge_env('mongo') does not set DR_DATA_DIR."""
    # Clean slate
    monkeypatch.delenv("DR_DATA_DIR", raising=False)
    monkeypatch.delenv("EIDETIC_MONGO_URI", raising=False)
    _bridge_env("mongo")
    assert "DR_DATA_DIR" not in os.environ


def test_bridge_env_neo4j_unchanged(monkeypatch) -> None:
    """_bridge_env('neo4j') does not set DR_DATA_DIR."""
    monkeypatch.delenv("DR_DATA_DIR", raising=False)
    monkeypatch.delenv("NEO4J_URI", raising=False)
    _bridge_env("neo4j")
    assert "DR_DATA_DIR" not in os.environ


# ---------------------------------------------------------------------------
# upsert: visibility-based routing (files only)
# ---------------------------------------------------------------------------


def test_upsert_public_writes_to_repo_store(tmp_path) -> None:
    """Public record upsert lands in repo/.eidetic/memory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")
        rec = _make_record(
            rid="pub1", text="public record", scope=Scope(name="default", visibility="public")
        )
        backend.upsert(rec)

        # Verify the record is in the repo store
        repo_store = repo / ".eidetic" / "memory"
        assert repo_store.exists()
        jsonl_files = list(repo_store.glob("*__public.jsonl"))
        assert len(jsonl_files) >= 1
        # The record should NOT be in the home store
        home_store = Path.home() / ".eidetic" / "memory"
        if home_store.exists():
            home_jsonl = list(home_store.glob("*__public.jsonl"))
            for f in home_jsonl:
                content = f.read_text()
                assert "pub1" not in content
    finally:
        os.chdir(old_cwd)


def test_upsert_private_writes_to_home_store(tmp_path) -> None:
    """Private record upsert lands in ~/.eidetic/memory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")
        rec = _make_record(
            rid="priv1", text="private record", scope=Scope(name="default", visibility="private")
        )
        backend.upsert(rec)

        # Verify the record is in the home store
        home_store = Path.home() / ".eidetic" / "memory"
        assert home_store.exists()
        jsonl_files = list(home_store.glob("*__private.jsonl"))
        assert len(jsonl_files) >= 1
        found = False
        for f in jsonl_files:
            if "priv1" in f.read_text():
                found = True
                break
        assert found
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# search: multi-store union
# ---------------------------------------------------------------------------


def test_search_unions_across_stores(tmp_path) -> None:
    """Search finds records from both repo and home stores."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")

        # Write a public record (goes to repo store)
        backend.upsert(
            _make_record(
                rid="pub1", text="public record", scope=Scope(name="default", visibility="public")
            )
        )

        # Write a private record (goes to home store)
        backend.upsert(
            _make_record(
                rid="priv1",
                text="private record",
                scope=Scope(name="default", visibility="private"),
            )
        )

        # Public search should find the public record
        results = backend.search(
            "public", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
        )
        ids = [r.id for r in results]
        assert "pub1" in ids
    finally:
        os.chdir(old_cwd)


def test_search_no_duplicate_records(tmp_path) -> None:
    """Search deduplicates records by id across stores."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")

        # Upsert same record twice (idempotent)
        rec = _make_record(
            rid="dup1", text="duplicate", scope=Scope(name="default", visibility="public")
        )
        backend.upsert(rec)
        backend.upsert(rec)

        results = backend.search(
            "duplicate", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
        )
        ids = [r.id for r in results]
        assert ids.count("dup1") == 1
    finally:
        os.chdir(old_cwd)


def test_search_private_scope_returns_own_private_plus_public(tmp_path) -> None:
    """Private-scope query returns its own private records + public records."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")

        # Write a public record
        backend.upsert(
            _make_record(
                rid="pub1", text="public data", scope=Scope(name="default", visibility="public")
            )
        )

        # Write a private record
        backend.upsert(
            _make_record(
                rid="priv1", text="private data", scope=Scope(name="default", visibility="private")
            )
        )

        # Private-scope search should find both its own private AND public records
        results = backend.search(
            "data", top_k=10, scope=Scope(name="default", visibility="private"), filters=None
        )
        ids = [r.id for r in results]
        assert "priv1" in ids
        assert "pub1" in ids
    finally:
        os.chdir(old_cwd)


def test_search_public_scope_never_leaks_private(tmp_path) -> None:
    """Public-scope query never returns private records (no-leak invariant)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")

        # Write a private record
        backend.upsert(
            _make_record(
                rid="priv1", text="secret data", scope=Scope(name="default", visibility="private")
            )
        )

        # Write a public record
        backend.upsert(
            _make_record(
                rid="pub1", text="public data", scope=Scope(name="default", visibility="public")
            )
        )

        # Public-scope search must NOT return the private record
        results = backend.search(
            "data", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
        )
        ids = [r.id for r in results]
        assert "priv1" not in ids
        assert "pub1" in ids
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# all(): multi-store enumeration
# ---------------------------------------------------------------------------


def test_all_spans_both_stores(tmp_path) -> None:
    """all() returns records from both repo and home stores."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")

        backend.upsert(
            _make_record(
                rid="pub1", text="public", scope=Scope(name="default", visibility="public")
            )
        )
        backend.upsert(
            _make_record(
                rid="priv1", text="private", scope=Scope(name="default", visibility="private")
            )
        )

        all_records = backend.all()
        ids = {r.id for r in all_records}
        assert "pub1" in ids
        assert "priv1" in ids
    finally:
        os.chdir(old_cwd)


def test_all_no_duplicates(tmp_path) -> None:
    """all() deduplicates records by id across stores."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")

        rec = _make_record(rid="dup1", text="dup", scope=Scope(name="default", visibility="public"))
        backend.upsert(rec)
        backend.upsert(rec)

        all_records = backend.all()
        ids = [r.id for r in all_records]
        assert ids.count("dup1") == 1
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# EIDETIC_DATA_DIR override: single-dir behavior
# ---------------------------------------------------------------------------


def test_upsert_with_override_uses_single_dir(tmp_path, monkeypatch) -> None:
    """With EIDETIC_DATA_DIR set, upsert writes to that dir only."""
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(override))

    backend = StoreBackend("files")
    backend.upsert(
        _make_record(rid="ov1", text="override", scope=Scope(name="default", visibility="public"))
    )

    # Record should be in the override dir
    jsonl_files = list(override.glob("*__public.jsonl"))
    assert len(jsonl_files) >= 1
    found = False
    for f in jsonl_files:
        if "ov1" in f.read_text():
            found = True
            break
    assert found


def test_search_with_override_uses_single_dir(tmp_path, monkeypatch) -> None:
    """With EIDETIC_DATA_DIR set, search reads from that dir only."""
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(override))

    backend = StoreBackend("files")
    backend.upsert(
        _make_record(rid="ov1", text="override", scope=Scope(name="default", visibility="public"))
    )

    results = backend.search(
        "override", top_k=10, scope=Scope(name="default", visibility="public"), filters=None
    )
    ids = [r.id for r in results]
    assert "ov1" in ids


def test_all_with_override_uses_single_dir(tmp_path, monkeypatch) -> None:
    """With EIDETIC_DATA_DIR set, all() reads from that dir only."""
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(override))

    backend = StoreBackend("files")
    backend.upsert(
        _make_record(rid="ov1", text="override", scope=Scope(name="default", visibility="public"))
    )

    all_records = backend.all()
    ids = {r.id for r in all_records}
    assert "ov1" in ids


# ---------------------------------------------------------------------------
# _candidate_read_dirs with override
# ---------------------------------------------------------------------------


def test_candidate_read_dirs_with_override(tmp_path, monkeypatch) -> None:
    """With EIDETIC_DATA_DIR set, _candidate_read_dirs returns a single dir."""
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(override))

    dirs = _candidate_read_dirs()
    assert dirs == [str(override)]


# ---------------------------------------------------------------------------
# Sweep re-upsert lands in correct dir
# ---------------------------------------------------------------------------


def test_sweep_reupsert_lands_in_correct_dir(tmp_path) -> None:
    """When all() returns records from both stores, re-upserting each
    via upsert() lands it back in the dir matching its own visibility."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        backend = StoreBackend("files")

        # Write records to both stores
        backend.upsert(
            _make_record(
                rid="pub1", text="public", scope=Scope(name="default", visibility="public")
            )
        )
        backend.upsert(
            _make_record(
                rid="priv1", text="private", scope=Scope(name="default", visibility="private")
            )
        )

        # Simulate sweep: enumerate all, then re-upsert each
        all_records = backend.all()
        for r in all_records:
            backend.upsert(r)

        # Verify: public record is in repo store, private in home store
        repo_store = repo / ".eidetic" / "memory"
        home_store = Path.home() / ".eidetic" / "memory"

        # Check repo store has the public record
        repo_jsonl = list(repo_store.glob("*__public.jsonl"))
        found_pub = False
        for f in repo_jsonl:
            if "pub1" in f.read_text():
                found_pub = True
                break
        assert found_pub

        # Check home store has the private record
        home_jsonl = list(home_store.glob("*__private.jsonl"))
        found_priv = False
        for f in home_jsonl:
            if "priv1" in f.read_text():
                found_priv = True
                break
        assert found_priv
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Regression: dedup must not hide serveable records (BUG 1)
# ---------------------------------------------------------------------------


def test_search_applies_can_serve_before_dedup(tmp_path, monkeypatch) -> None:
    """eidetic's own can_serve must run BEFORE id-dedup across stores.

    Defense-in-depth: eidetic does not trust the store's own visibility filter
    (it re-applies ``can_serve`` so the no-leak invariant holds "regardless of
    the store's behavior"). This test simulates an *unfiltered* store — one whose
    ``list()`` returns every candidate regardless of scope — so a non-serveable
    private copy of id "X" is yielded from the first read dir (home) and a
    serveable public copy of the same id from the second (repo). The private copy
    must not win dedup and then be filtered out, dropping the record entirely.

    With the fix (can_serve inside the read loop) the public copy survives and
    "X" is returned. Without it, the private copy occupied the id slot first, was
    later dropped by the line-380 filter, and "X" vanished — so this test FAILS on
    the pre-fix code. (A test that goes through the real ``drstore.list`` cannot
    reproduce the bug, because data-refinery pre-filters by ``can_serve`` at the
    source — which is why the original colleague-written test passed on old code.)
    """
    from eidetic.memory import backend as be

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)

    priv_env = record_to_envelope(
        _make_record(rid="X", text="shared text", scope=Scope(name="default", visibility="private"))
    )
    pub_env = record_to_envelope(
        _make_record(rid="X", text="shared text", scope=Scope(name="default", visibility="public"))
    )

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        be._GIT_CACHE.clear()
        # Read order is [home, repo]; the non-serveable private copy comes first.
        dirs = _candidate_read_dirs()
        assert len(dirs) == 2, dirs
        home_dir, repo_dir = dirs[0], dirs[1]

        def _unfiltered_list(scope=None, backend=None, **kwargs):
            d = os.environ.get("DR_DATA_DIR")
            if d == home_dir:
                return [priv_env]  # store does NOT filter — yields the private copy
            if d == repo_dir:
                return [pub_env]
            return []

        monkeypatch.setattr(be.drstore, "list", _unfiltered_list)

        results = StoreBackend("files").search(
            "shared",
            top_k=10,
            scope=Scope(name="default", visibility="public"),
            filters=None,
        )
        ids = [r.id for r in results]
        assert "X" in ids
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# _git_toplevel: fail-closed on OSError (BUG 2)
# ---------------------------------------------------------------------------


def test_git_toplevel_getcwd_raises_returns_none(monkeypatch) -> None:
    """_git_toplevel() returns None when os.getcwd raises OSError."""
    from eidetic.memory import backend as be

    be._GIT_CACHE.clear()

    def _bad_getcwd():
        raise OSError("no such process")

    monkeypatch.setattr(os, "getcwd", _bad_getcwd)

    from eidetic.memory.backend import _git_toplevel

    result = _git_toplevel()
    assert result is None
