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


def _base_env(data_dir: Path) -> dict:
    """A minimal, isolated environment: EIDETIC_DATA_DIR pinned to a throwaway
    dir so this test never touches a real store, and no live embed server
    dependency (ingest never needs one; keyword recall doesn't either)."""
    env = dict(os.environ)
    env["EIDETIC_DATA_DIR"] = str(data_dir)
    env.pop("EIDETIC_EMBED_URL", None)
    env.pop("EIDETIC_EMBED_MODEL", None)
    return env


def _raw_eidetic(*args: str) -> list[str]:
    """Invoke the raw `eidetic` CLI via the current interpreter (no wrapper,
    no scope/visibility injection) — the "unaware direct consumer" surface."""
    return [sys.executable, "-m", "eidetic", *args]


def _all_records(data_dir: Path) -> list[dict]:
    """Read every record from every *.jsonl file under data_dir (any scope)."""
    records: list[dict] = []
    if not data_dir.exists():
        return records
    for f in data_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _visibility_of(records: list[dict], record_id: str) -> str | None:
    for r in records:
        if r["id"] == record_id:
            return r["scope"]["visibility"]
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
    hits = json.loads(recall_result.stdout)
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
    hits = json.loads(recall_result.stdout)
    ids = {h["id"] for h in hits}
    assert "wrap-roundtrip-1" in ids
