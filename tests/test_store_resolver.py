"""Tests for visibility-aware store-path resolver helpers in eidetic.memory.backend."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from eidetic.memory.backend import (
    _candidate_read_dirs,
    _git_toplevel,
    _home_store_dir,
    _override_dir,
    _resolve_write_dir,
)


@pytest.fixture(autouse=True)
def _resolver_isolation(monkeypatch) -> None:
    """Ensure EIDETIC_DATA_DIR is unset and _GIT_CACHE is clean for every test."""
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)
    from eidetic.memory import backend as be

    be._GIT_CACHE.clear()


# ---------------------------------------------------------------------------
# _home_store_dir
# ---------------------------------------------------------------------------


def test_home_store_dir(tmp_path) -> None:
    expected = str(tmp_path / ".eidetic" / "memory")
    with patch("eidetic.memory.backend.Path.home", return_value=tmp_path):
        assert _home_store_dir() == expected


# ---------------------------------------------------------------------------
# _override_dir
# ---------------------------------------------------------------------------


def test_override_dir_default() -> None:
    # When EIDETIC_DATA_DIR is not set, return None
    assert _override_dir() is None


def test_override_dir_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(tmp_path))
    assert _override_dir() == str(tmp_path)


# ---------------------------------------------------------------------------
# _git_toplevel
# ---------------------------------------------------------------------------


def test_git_toplevel_in_repo(tmp_path) -> None:
    """Inside a git repo, _git_toplevel returns the repo root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        result = _git_toplevel()
        assert result == str(repo)
    finally:
        os.chdir(old_cwd)


def test_git_toplevel_in_subdir(tmp_path) -> None:
    """In a subdir of a repo, _git_toplevel still returns the repo root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subdir = repo / "sub" / "deep"
    subdir.mkdir(parents=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(subdir))
        result = _git_toplevel()
        assert result == str(repo)
    finally:
        os.chdir(old_cwd)


def test_git_toplevel_outside_repo(tmp_path) -> None:
    """Outside a git repo, _git_toplevel returns None."""
    not_repo = tmp_path / "not_repo"
    not_repo.mkdir()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(not_repo))
        result = _git_toplevel()
        assert result is None
    finally:
        os.chdir(old_cwd)


def test_git_toplevel_git_absent(tmp_path, monkeypatch) -> None:
    """When git is not installed (FileNotFoundError), _git_toplevel returns None."""
    not_repo = tmp_path / "not_repo"
    not_repo.mkdir()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(not_repo))

        def raise_file_not_found(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(subprocess, "run", raise_file_not_found)
        result = _git_toplevel()
        assert result is None
    finally:
        os.chdir(old_cwd)


def test_git_toplevel_caching(tmp_path) -> None:
    """Calling _git_toplevel() twice in the same cwd invokes git at most once."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))

        call_count = 0

        original_run = subprocess.run

        def counting_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_run(*args, **kwargs)

        with patch("subprocess.run", side_effect=counting_run):
            _git_toplevel()
            _git_toplevel()

        assert call_count == 1
    finally:
        os.chdir(old_cwd)


def test_git_toplevel_cache_keyed_by_cwd(tmp_path) -> None:
    """Changing cwd invalidates the cache entry (fresh git call for new cwd)."""
    repo1 = tmp_path / "repo1"
    repo1.mkdir()
    subprocess.run(["git", "init", str(repo1)], capture_output=True, check=True)

    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    subprocess.run(["git", "init", str(repo2)], capture_output=True, check=True)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo1))
        result1 = _git_toplevel()
        assert result1 == str(repo1)

        os.chdir(str(repo2))
        result2 = _git_toplevel()
        assert result2 == str(repo2)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# _resolve_write_dir
# ---------------------------------------------------------------------------


def test_resolve_write_dir_public_in_repo(tmp_path) -> None:
    """Public record inside a repo resolves to repo/.eidetic/memory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        result = _resolve_write_dir("public")
        assert result == str(repo / ".eidetic" / "memory")
    finally:
        os.chdir(old_cwd)


def test_resolve_write_dir_private_in_repo(tmp_path) -> None:
    """Private record inside a repo resolves to home."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        result = _resolve_write_dir("private")
        # Private always goes to home
        assert result == str(Path.home() / ".eidetic" / "memory")
    finally:
        os.chdir(old_cwd)


def test_resolve_write_dir_public_outside_repo(tmp_path) -> None:
    """Public record outside a repo resolves to home."""
    not_repo = tmp_path / "not_repo"
    not_repo.mkdir()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(not_repo))
        result = _resolve_write_dir("public")
        assert result == str(Path.home() / ".eidetic" / "memory")
    finally:
        os.chdir(old_cwd)


def test_resolve_write_dir_override(tmp_path, monkeypatch) -> None:
    """When EIDETIC_DATA_DIR is set, _resolve_write_dir returns it regardless of visibility."""
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(override))
    assert _resolve_write_dir("public") == str(override)
    assert _resolve_write_dir("private") == str(override)


# ---------------------------------------------------------------------------
# _candidate_read_dirs
# ---------------------------------------------------------------------------


def test_candidate_read_dirs_override(tmp_path, monkeypatch) -> None:
    """When EIDETIC_DATA_DIR is set, return exactly [override]."""
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("EIDETIC_DATA_DIR", str(override))
    result = _candidate_read_dirs()
    assert result == [str(override)]


def test_candidate_read_dirs_no_duplicate(tmp_path) -> None:
    """When repo store == home store, _candidate_read_dirs has no duplicate."""
    # Force the home store dir to be the same as the repo store dir
    # by making the repo root be the home dir
    with patch("eidetic.memory.backend.Path.home", return_value=tmp_path):
        # Create a git repo at tmp_path (which is also "home")
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            result = _candidate_read_dirs()
            # Should have exactly one entry (no duplicate)
            assert len(result) == 1
            assert result[0] == str(tmp_path / ".eidetic" / "memory")
        finally:
            os.chdir(old_cwd)


def test_candidate_read_dirs_in_repo(tmp_path) -> None:
    """In a repo, _candidate_read_dirs returns [home, repo] (two distinct dirs)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        result = _candidate_read_dirs()
        home_dir = str(Path.home() / ".eidetic" / "memory")
        repo_dir = str(repo / ".eidetic" / "memory")
        assert home_dir in result
        assert repo_dir in result
        assert len(result) == 2
    finally:
        os.chdir(old_cwd)


def test_candidate_read_dirs_outside_repo(tmp_path) -> None:
    """Outside a repo, _candidate_read_dirs returns [home]."""
    not_repo = tmp_path / "not_repo"
    not_repo.mkdir()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(not_repo))
        result = _candidate_read_dirs()
        assert result == [str(Path.home() / ".eidetic" / "memory")]
    finally:
        os.chdir(old_cwd)
