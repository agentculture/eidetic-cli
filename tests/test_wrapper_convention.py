"""Integration test for the memory scope+visibility convention (v1).

See ``docs/contract.md``. The convention says the DEFAULT visibility for a
no-flag `remember`/`recall` is `public` — matching the plain `eidetic`
CLI's own argparse default AND colleague's runtime (`colleague/memory.py`
hardcodes `--visibility public`).

Before this convention was pinned, the vendored `remember.sh` wrapper
silently overrode that default to `private` (injecting `--scope <suffix>
--visibility private` whenever the caller passed neither flag), while the
plain `eidetic remember` CLI defaulted to `public` — so an agent going
through the wrapper and an agent (or the colleague backend) calling the raw
CLI directly stored MUTUALLY INVISIBLE records under the same repo, purely
by accident of which surface they happened to use.

This test shells both surfaces — the vendored wrapper script and the raw
`eidetic` CLI (via ``python -m eidetic``, the same interpreter running
pytest so no extra `uv run` subprocess/network is needed) — with no
`--visibility` flag, points `EIDETIC_DATA_DIR` at a throwaway directory so
nothing touches a real store, and asserts both land a record with the SAME
visibility (`public`). It fails before the wrapper fix (wrapper writes
`private`, raw CLI writes `public`) and passes after it. A follow-up
assertion proves the practical consequence: a plain no-flag `eidetic
recall` (the CLI's OWN default, scope=default/visibility=public) finds
both records — i.e. the two surfaces are mutually visible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from eidetic.memory.backend import get_backend
from eidetic.memory.record import Record

REPO_ROOT = Path(__file__).resolve().parents[1]
REMEMBER_WRAPPER = REPO_ROOT / ".claude" / "skills" / "remember" / "scripts" / "remember.sh"
RECALL_WRAPPER = REPO_ROOT / ".claude" / "skills" / "recall" / "scripts" / "recall.sh"

pytestmark = pytest.mark.skipif(
    not (REMEMBER_WRAPPER.exists() and RECALL_WRAPPER.exists()),
    reason="vendored recall/remember wrappers not present in this checkout",
)


def _run(argv: list[str], *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _hits(proc: subprocess.CompletedProcess[str]) -> list[dict]:
    """Return the items of a ``recall --json`` composite bundle.

    ``recall --json`` emits ONE object — ``{query, mode, truncated, items}`` —
    where each item is a full record plus its ``tier``/``depth`` labels.
    """
    return json.loads(proc.stdout)["items"]


def _pin_eidetic_on_path(env: dict) -> dict:
    """Pin the wrapper's ``command -v eidetic`` resolution to THIS checkout's
    console script (the same code as ``python -m eidetic``), by prepending the
    directory of the running interpreter — under ``uv run`` the venv's
    ``eidetic`` console script lives alongside its ``python``. Without this, a
    globally-installed ``eidetic`` earlier on PATH would make the wrapper
    subprocess and the raw-CLI comparison run DIFFERENT versions (Qodo PR #29
    finding 2). Mutates and returns *env*."""
    venv_bin = str(Path(sys.executable).parent)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    return env


def _base_env(data_dir: Path) -> dict:
    """A minimal, isolated environment: EIDETIC_DATA_DIR pinned to a throwaway
    dir so this test never touches a real store, and no live embed server
    dependency (ingest never needs one; keyword recall doesn't either)."""
    env = dict(os.environ)
    env["EIDETIC_DATA_DIR"] = str(data_dir)
    env.pop("EIDETIC_EMBED_URL", None)
    env.pop("EIDETIC_EMBED_MODEL", None)
    return _pin_eidetic_on_path(env)


def _raw_eidetic(*args: str) -> list[str]:
    """Invoke the raw `eidetic` CLI via the current interpreter (no wrapper,
    no scope/visibility injection) — the "unaware direct consumer" surface."""
    return [sys.executable, "-m", "eidetic", *args]


def _all_records(data_dir: Path) -> list[Record]:
    """Enumerate every record under *data_dir* via the in-process backend API
    (``get_backend("files").all()``) instead of scraping ``*.jsonl`` directly —
    so the test doesn't couple to data_refinery's on-disk file-per-scope layout
    (Qodo PR #29 finding 3). ``EIDETIC_DATA_DIR`` is set as an override (matching
    what the subprocess writes were given via ``_base_env``), then restored."""
    prior = os.environ.get("EIDETIC_DATA_DIR")
    os.environ["EIDETIC_DATA_DIR"] = str(data_dir)
    try:
        return get_backend("files").all()
    finally:
        if prior is None:
            os.environ.pop("EIDETIC_DATA_DIR", None)
        else:
            os.environ["EIDETIC_DATA_DIR"] = prior


def _visibility_of(records: list[Record], record_id: str) -> str | None:
    for r in records:
        if r.id == record_id:
            return r.scope.visibility
    return None


def test_wrapper_and_raw_cli_default_to_the_same_visibility(tmp_path: Path) -> None:
    data_dir = tmp_path / "store"
    env = _base_env(data_dir)

    wrapper_record = {
        "id": "wrap-conv-1",
        "text": "wrapper convention record",
        "type": "note",
    }
    raw_record = {
        "id": "raw-conv-1",
        "text": "raw cli convention record",
        "type": "note",
    }

    # The vendored wrapper: no --scope, no --visibility. It resolves the
    # culture.yaml suffix itself (walking up from the script's own location),
    # so it doesn't matter that we run from the repo root — that just matches
    # how an agent invokes it in practice.
    wrapper_result = _run(
        ["bash", str(REMEMBER_WRAPPER), json.dumps(wrapper_record), "--json"],
        cwd=REPO_ROOT,
        env=env,
    )
    assert wrapper_result.returncode == 0, wrapper_result.stderr

    # The raw CLI, exactly as an unaware direct consumer would call it: no
    # --scope, no --visibility.
    raw_result = _run(
        _raw_eidetic("remember", json.dumps(raw_record), "--json"),
        cwd=REPO_ROOT,
        env=env,
    )
    assert raw_result.returncode == 0, raw_result.stderr

    records = _all_records(data_dir)
    wrapper_visibility = _visibility_of(records, "wrap-conv-1")
    raw_visibility = _visibility_of(records, "raw-conv-1")

    assert wrapper_visibility is not None, "wrapper record was not written"
    assert raw_visibility is not None, "raw CLI record was not written"

    # THE CONVENTION (docs/contract.md): both default to the SAME visibility.
    # This assertion fails before the wrapper fix (wrapper writes "private",
    # raw CLI writes "public") and passes after it.
    assert wrapper_visibility == raw_visibility == "public"

    # Prove the practical consequence: a plain no-flag `eidetic recall` (the
    # CLI's OWN default — scope=default, visibility=public) finds BOTH
    # records, i.e. the wrapper's record is no longer invisible to a caller
    # that doesn't know about the wrapper's scope-injection at all.
    recall_result = _run(
        _raw_eidetic("recall", "convention record", "--mode", "keyword", "--top-k", "10", "--json"),
        cwd=REPO_ROOT,
        env=env,
    )
    assert recall_result.returncode == 0, recall_result.stderr
    hits = _hits(recall_result)
    ids = {h["id"] for h in hits}
    assert "wrap-conv-1" in ids, "wrapper's record is invisible to a plain public recall"
    assert "raw-conv-1" in ids


def test_recall_wrapper_sees_remember_wrapper_records_with_no_flags(tmp_path: Path) -> None:
    """The two wrapper scripts must round-trip with no flags on either side:
    a no-flag `remember.sh` write must be visible to a no-flag `recall.sh`
    query. Both inject the same culture.yaml-suffix scope with the same
    (now public) default visibility, so this holds regardless of whether the
    query matches the write's scope name exactly (public records are visible
    to any scope)."""
    data_dir = tmp_path / "store"
    env = _base_env(data_dir)

    record = {
        "id": "wrap-roundtrip-1",
        "text": "roundtrip through both wrappers",
        "type": "note",
    }

    remember_result = _run(
        ["bash", str(REMEMBER_WRAPPER), json.dumps(record), "--json"],
        cwd=REPO_ROOT,
        env=env,
    )
    assert remember_result.returncode == 0, remember_result.stderr

    recall_result = _run(
        [
            "bash",
            str(RECALL_WRAPPER),
            "roundtrip through both wrappers",
            "--mode",
            "keyword",
            "--top-k",
            "5",
            "--json",
        ],
        cwd=REPO_ROOT,
        env=env,
    )
    assert recall_result.returncode == 0, recall_result.stderr
    hits = _hits(recall_result)
    ids = {h["id"] for h in hits}
    assert "wrap-roundtrip-1" in ids


def test_wrapper_and_raw_cli_resolve_the_same_default_store_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove wrapper and raw CLI resolve the SAME store directory under the
    REAL default-resolution algorithm (``eidetic/memory/backend.py``
    ``_resolve_write_dir``) — not merely identically via an ``EIDETIC_DATA_DIR``
    override that short-circuits it (Qodo PR #29 finding 1 / compliance ID
    1345474).

    Hermetic: runs inside a throwaway ``git init`` repo under ``tmp_path`` with
    ``HOME`` also redirected under ``tmp_path``, so BOTH the public-write
    repo-root branch and the private/HOME fallback branch land under
    ``tmp_path`` — never touching this checkout's real ``.eidetic/memory`` or
    the developer's real ``$HOME``. Safe because store-directory resolution is
    CWD-driven (``_git_toplevel()`` shells ``git rev-parse --show-toplevel``
    against ``os.getcwd()``) — a SEPARATE axis from the wrapper's own
    scope/visibility resolution, which walks up from the wrapper SCRIPT's own
    location to find ``culture.yaml``. Redirecting CWD therefore doesn't disturb
    which scope the wrapper injects; it only redirects where records land.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)

    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)

    from eidetic.memory import backend as be

    be._GIT_CACHE.clear()

    env = dict(os.environ)
    env.pop("EIDETIC_EMBED_URL", None)
    env.pop("EIDETIC_EMBED_MODEL", None)
    _pin_eidetic_on_path(env)

    wrapper_record = {"id": "wrap-loc-1", "text": "wrapper location record", "type": "note"}
    raw_record = {"id": "raw-loc-1", "text": "raw location record", "type": "note"}

    # No --scope, no --visibility on either surface: exercise the true default
    # path (wrapper injects its resolved scope + public; raw CLI uses its own
    # argparse defaults). Both are public, so both route to <repo>/.eidetic/memory.
    wrapper_result = _run(
        ["bash", str(REMEMBER_WRAPPER), json.dumps(wrapper_record), "--json"],
        cwd=repo,
        env=env,
    )
    assert wrapper_result.returncode == 0, wrapper_result.stderr

    raw_result = _run(
        _raw_eidetic("remember", json.dumps(raw_record), "--json"),
        cwd=repo,
        env=env,
    )
    assert raw_result.returncode == 0, raw_result.stderr

    # LOCATION (a filesystem-location claim, so a thin file check): both landed
    # under <repo>/.eidetic/memory and NOT under the redirected HOME store.
    repo_store = repo / ".eidetic" / "memory"
    home_store = tmp_path / "home" / ".eidetic" / "memory"
    assert list(repo_store.glob("*.jsonl")), "expected records in the repo-local store"
    if home_store.exists():
        assert not list(
            home_store.glob("*.jsonl")
        ), "a public write leaked into the HOME store instead of the repo store"

    # VISIBILITY via the in-process API (not file-scraping).
    records = {r.id: r for r in get_backend("files").all()}
    assert "wrap-loc-1" in records, "wrapper record did not resolve to the repo store"
    assert "raw-loc-1" in records, "raw CLI record did not resolve to the repo store"
    assert records["wrap-loc-1"].scope.visibility == "public"
    assert records["raw-loc-1"].scope.visibility == "public"
