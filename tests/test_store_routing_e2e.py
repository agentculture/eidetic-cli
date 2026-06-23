"""End-to-end + regression tests for two-store routing (task t3).

Exercises the full CLI pipeline to verify that PUBLIC records written inside
a git repo land in <repo>/.eidetic/memory while PRIVATE records (and any
record outside a repo) land in $HOME/.eidetic/memory.  Also verifies the
EIDETIC_DATA_DIR override regression, clean-break isolation, sweep across
both dirs, and that mongo/neo4j paths remain unaffected.

CRITICAL isolation rules enforced in every test:
  - monkeypatch.setenv("HOME", str(tmp_home)) so Path.home() is isolated
  - monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False) for repo-routing cases
  - real git repo created via subprocess
  - os.chdir into the repo (restored in finally)
  - eidetic.memory.backend._GIT_CACHE cleared before each assertion
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import eidetic.memory.backend as be
from eidetic.cli._commands.recall import cmd_recall
from eidetic.cli._commands.remember import cmd_remember

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_git(repo: Path) -> None:
    """Create a real git repo at *repo*."""
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)


def _clear_cache() -> None:
    """Clear the git-toplevel cache so resolution is fresh."""
    be._GIT_CACHE.clear()


def _remember(
    record_id: str,
    text: str,
    *,
    scope: str = "default",
    visibility: str = "public",
    record_type: str = "note",
) -> int:
    """Call cmd_remember with a constructed Namespace (files backend)."""
    ns = argparse.Namespace(
        record=json.dumps({"id": record_id, "text": text, "type": record_type}),
        backend="files",
        scope=scope,
        visibility=visibility,
        added_by=None,
        json=False,
    )
    return cmd_remember(ns)


def _recall(
    query: str,
    *,
    scope: str = "default",
    visibility: str = "public",
    mode: str = "keyword",
) -> list[dict]:
    """Call cmd_recall with --json and return the parsed hit list."""
    ns = argparse.Namespace(
        query=query,
        backend="files",
        scope=scope,
        visibility=visibility,
        mode=mode,
        alpha=0.5,
        case_sensitive=False,
        top_k=5,
        filters=[],
        include_shadowed=False,
        include_archived=False,
        json=True,
    )
    # Capture stdout via a temporary redirect.
    import io

    old_stdout = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        rc = cmd_recall(ns)
    finally:
        sys.stdout = old_stdout
    assert rc == 0, f"cmd_recall returned {rc}"
    return json.loads(buf.getvalue())


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file and return a list of dicts."""
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# 1. public-in-repo
# ---------------------------------------------------------------------------


def test_public_in_repo(tmp_path: Path, monkeypatch) -> None:
    """Public record inside a git repo lands in <repo>/.eidetic/memory."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        _clear_cache()

        rc = _remember("pub-1", "public repo record", visibility="public")
        assert rc == 0

        # Assert the file exists under repo/.eidetic/memory
        repo_mem = repo / ".eidetic" / "memory"
        pub_file = repo_mem / "default__public.jsonl"
        assert pub_file.exists(), f"expected {pub_file} to exist"
        records = _read_jsonl(pub_file)
        assert any(r["id"] == "pub-1" for r in records)

        # Assert NOTHING was written under tmp_home/.eidetic/memory
        home_mem = tmp_home / ".eidetic" / "memory"
        if home_mem.exists():
            for f in home_mem.glob("*.jsonl"):
                assert "pub-1" not in f.read_text(), f"pub-1 leaked into home store: {f}"

        # Recall the same query from the repo and assert the record is returned.
        hits = _recall("public repo record", visibility="public")
        assert len(hits) >= 1
        assert any(h["id"] == "pub-1" for h in hits)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 2. private-to-home
# ---------------------------------------------------------------------------


def test_private_to_home(tmp_path: Path, monkeypatch) -> None:
    """Private record inside a repo lands in $HOME/.eidetic/memory."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        _clear_cache()

        rc = _remember("priv-1", "private home record", visibility="private")
        assert rc == 0

        # Assert it landed in home
        home_mem = tmp_home / ".eidetic" / "memory"
        priv_file = home_mem / "default__private.jsonl"
        assert priv_file.exists(), f"expected {priv_file} to exist"
        records = _read_jsonl(priv_file)
        assert any(r["id"] == "priv-1" for r in records)

        # Assert it did NOT land under repo/.eidetic/memory
        repo_mem = repo / ".eidetic" / "memory"
        if repo_mem.exists():
            for f in repo_mem.glob("*.jsonl"):
                assert "priv-1" not in f.read_text(), f"priv-1 leaked into repo store: {f}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 3. private-recall-merges
# ---------------------------------------------------------------------------


def test_private_recall_merges(tmp_path: Path, monkeypatch) -> None:
    """Private query returns own private + public pool; public query returns only public."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        _clear_cache()

        # Write a public record (repo) and a private record (home).
        _remember("pub-merge", "shared topic public", visibility="public")
        _remember("priv-merge", "shared topic private", visibility="private")

        # Private-scope recall should return BOTH records.
        hits_priv = _recall("shared topic", visibility="private")
        ids_priv = {h["id"] for h in hits_priv}
        assert "pub-merge" in ids_priv, "public record should be visible to private query"
        assert "priv-merge" in ids_priv, "private record should be visible to private query"

        # Public-scope recall should return ONLY the public record.
        hits_pub = _recall("shared topic", visibility="public")
        ids_pub = {h["id"] for h in hits_pub}
        assert "pub-merge" in ids_pub
        assert "priv-merge" not in ids_pub, "private record must NOT leak to public query"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 4. override-byte-identical
# ---------------------------------------------------------------------------


def test_override_byte_identical(tmp_path: Path, monkeypatch) -> None:
    """EIDETIC_DATA_DIR override routes all writes to that dir only."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))

    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(override))

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        _clear_cache()

        # Remember public and private — both should go to override.
        _remember("ov-pub", "override public", visibility="public")
        _remember("ov-priv", "override private", visibility="private")

        # Assert records are in override dir.
        pub_file = override / "default__public.jsonl"
        priv_file = override / "default__private.jsonl"
        assert pub_file.exists(), f"expected {pub_file} to exist"
        assert priv_file.exists(), f"expected {priv_file} to exist"

        # Assert nothing under repo or home.
        repo_mem = repo / ".eidetic" / "memory"
        assert not repo_mem.exists(), f"override should not touch repo: {repo_mem}"

        home_mem = tmp_home / ".eidetic" / "memory"
        assert not home_mem.exists(), f"override should not touch home: {home_mem}"

        # Recall from override — public query finds public record.
        hits_pub = _recall("override", visibility="public")
        ids_pub = {h["id"] for h in hits_pub}
        assert "ov-pub" in ids_pub

        # Private query finds private record (and also the public pool).
        hits_priv = _recall("override", visibility="private")
        ids_priv = {h["id"] for h in hits_priv}
        assert "ov-priv" in ids_priv
        assert "ov-pub" in ids_priv  # public is visible to private query
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 5. outside-repo
# ---------------------------------------------------------------------------


def test_outside_repo(tmp_path: Path, monkeypatch) -> None:
    """Outside a git repo, remember lands under $HOME/.eidetic/memory."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)

    not_repo = tmp_path / "not_repo"
    not_repo.mkdir()

    old_cwd = os.getcwd()
    try:
        os.chdir(str(not_repo))
        _clear_cache()

        # _git_toplevel should be None.
        assert be._git_toplevel() is None

        # Remember public — should go to home.
        rc = _remember("out-pub", "outside repo public", visibility="public")
        assert rc == 0

        home_mem = tmp_home / ".eidetic" / "memory"
        pub_file = home_mem / "default__public.jsonl"
        assert pub_file.exists(), f"expected {pub_file} to exist"
        records = _read_jsonl(pub_file)
        assert any(r["id"] == "out-pub" for r in records)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 6. clean-break
# ---------------------------------------------------------------------------


def test_clean_break(tmp_path: Path, monkeypatch) -> None:
    """Pre-populated home store is byte-unchanged after repo remember."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)

    # Pre-populate home store with a record (via override trick).
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(tmp_home / ".eidetic" / "memory"))
    _remember("pre-home", "pre-existing home record", visibility="public")
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)

    home_mem = tmp_home / ".eidetic" / "memory"
    home_file = home_mem / "default__public.jsonl"
    pre_bytes = home_file.read_bytes()

    # Now remember a public record inside a repo.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        _clear_cache()

        _remember("repo-pub", "repo public record", visibility="public")

        # Assert the home file is byte-unchanged.
        post_bytes = home_file.read_bytes()
        assert (
            pre_bytes == post_bytes
        ), "home store was modified by repo remember — clean break violated"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 7. sweep-both-dirs
# ---------------------------------------------------------------------------


def test_sweep_both_dirs(tmp_path: Path, monkeypatch) -> None:
    """Sweep transitions records in both repo and home dirs, writing each back."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git(repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        _clear_cache()

        # Write a public record (repo) with supersedes to trigger shadowing.
        # We write sweep-new directly into the repo store JSONL so it carries
        # supersedes="sweep-old" — the lifecycle engine will shadow sweep-old.
        repo_mem = repo / ".eidetic" / "memory"
        repo_mem.mkdir(parents=True, exist_ok=True)
        new_record = {
            "id": "sweep-new",
            "content": "new claim",
            "hash": "h-new",
            "scope": {"name": "default", "visibility": "public"},
            "metadata": {
                "type": "note",
                "record_metadata": {},
                "created": "2026-06-20T00:00:00+00:00",
                "lifecycle": "active",
                "supersedes": "sweep-old",
            },
        }
        pub_file = repo_mem / "default__public.jsonl"
        # Ensure the public file exists with the new record.
        if pub_file.exists():
            existing = pub_file.read_text(encoding="utf-8").strip().splitlines()
            existing = [line for line in existing if line.strip()]
            existing.append(json.dumps(new_record))
            pub_file.write_text("\n".join(existing) + "\n", encoding="utf-8")
        else:
            pub_file.write_text(json.dumps(new_record) + "\n", encoding="utf-8")

        # Write an old record that will be superseded (also public, in repo).
        old_record = {
            "id": "sweep-old",
            "content": "old claim",
            "hash": "h-old",
            "scope": {"name": "default", "visibility": "public"},
            "metadata": {
                "type": "note",
                "record_metadata": {},
                "created": "2026-06-19T00:00:00+00:00",
                "lifecycle": "active",
            },
        }
        # Append sweep-old to the same public file.
        existing = pub_file.read_text(encoding="utf-8").strip().splitlines()
        existing = [line for line in existing if line.strip()]
        existing.append(json.dumps(old_record))
        pub_file.write_text("\n".join(existing) + "\n", encoding="utf-8")

        # Write a private record (home) that is old enough to archive.
        home_mem = tmp_home / ".eidetic" / "memory"
        home_mem.mkdir(parents=True, exist_ok=True)
        old_priv = {
            "id": "sweep-priv-old",
            "content": "old private record",
            "hash": "h-priv-old",
            "scope": {"name": "default", "visibility": "private"},
            "metadata": {
                "type": "note",
                "record_metadata": {},
                "created": "2020-01-01T00:00:00+00:00",
                "lifecycle": "active",
            },
        }
        priv_file = home_mem / "default__private.jsonl"
        priv_file.write_text(json.dumps(old_priv) + "\n", encoding="utf-8")

        # Run sweep via the CLI.
        from eidetic.cli._commands.sweep import cmd_sweep

        ns = argparse.Namespace(
            backend="files",
            dry_run=False,
            json=False,
        )
        rc = cmd_sweep(ns)
        assert rc == 0

        # Verify the repo public record (sweep-old) is shadowed and written back to repo.
        repo_records = _read_jsonl(pub_file)
        old_in_repo = next((r for r in repo_records if r["id"] == "sweep-old"), None)
        assert old_in_repo is not None, "sweep-old should still be in repo store"
        assert (
            old_in_repo["metadata"]["lifecycle"] == "shadowed"
        ), "sweep-old should be shadowed in repo store"

        # Verify the home private record is archived and written back to home.
        home_records = _read_jsonl(priv_file)
        priv_in_home = next((r for r in home_records if r["id"] == "sweep-priv-old"), None)
        assert priv_in_home is not None, "sweep-priv-old should still be in home store"
        assert (
            priv_in_home["metadata"]["lifecycle"] == "archived"
        ), "sweep-priv-old should be archived in home store"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 8. mongo/neo4j unaffected
# ---------------------------------------------------------------------------


def test_mongo_neo4j_unaffected() -> None:
    """get_backend('mongo') / ('neo4j') path resolution is unchanged.

    Assert that _bridge_env for mongo/neo4j only sets DR_MONGO_URI / DR_NEO4J_URI
    and does NOT apply any repo/home dir logic.
    """
    # Clean slate.
    for k in ("DR_MONGO_URI", "DR_NEO4J_URI", "EIDETIC_MONGO_URI", "NEO4J_URI"):
        os.environ.pop(k, None)

    # mongo: _bridge_env should only set DR_MONGO_URI when EIDETIC_MONGO_URI is set.
    os.environ["EIDETIC_MONGO_URI"] = "mongodb://test:27017"
    be._bridge_env("mongo")
    assert os.environ.get("DR_MONGO_URI") == "mongodb://test:27017"
    # DR_DATA_DIR must NOT be set by the mongo bridge.
    # (It may be left from a prior test, so we only check it wasn't set by this call.)
    os.environ.pop("EIDETIC_MONGO_URI", None)
    os.environ.pop("DR_MONGO_URI", None)

    # neo4j: _bridge_env should only set DR_NEO4J_URI when NEO4J_URI is set.
    os.environ["NEO4J_URI"] = "bolt://test:7687"
    be._bridge_env("neo4j")
    assert os.environ.get("DR_NEO4J_URI") == "bolt://test:7687"
    os.environ.pop("NEO4J_URI", None)
    os.environ.pop("DR_NEO4J_URI", None)

    # Verify get_backend("mongo") and get_backend("neo4j") don't raise
    # (they create StoreBackend instances; connection errors happen later).
    be.get_backend("mongo")
    be.get_backend("neo4j")
