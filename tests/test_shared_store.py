"""Shared-store integration tests (t8): prove one shared store serves all agents.

Covers c2/h5 contract: the shared memory store is accessible over the
subprocess/JSON boundary (no per-agent fork). All tests use hermetic EIDETIC_DATA_DIR
pointing at a tmp_path subdir for isolation.

Three test cases:
1. Cross-invocation round-trip: one invocation writes; a separate invocation reads.
2. No per-agent fork: inspect the tmp dir tree; the single EIDETIC_DATA_DIR is used.
3. Two distinct agents share: simulate different callers writing + a third reading.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _cli(
    args: list[str], stdin: str | None = None, data_dir: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m eidetic <args>`` as a subprocess with optional data_dir env."""
    env = {**os.environ}
    if data_dir is not None:
        env["EIDETIC_DATA_DIR"] = data_dir
    return subprocess.run(
        [sys.executable, "-m", "eidetic", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


# ------------------------------------------------------------------
# 1. Cross-invocation round-trip: write in one subprocess, read in another
# ------------------------------------------------------------------


def test_cross_invocation_roundtrip_text_metadata_score(tmp_path: Path) -> None:
    """Remember in subprocess A, recall in subprocess B; prove text+metadata+score round-trip.

    Invocation A: ``eidetic remember '<json>' --json`` (env: EIDETIC_DATA_DIR=<tmp>)
    Invocation B: ``eidetic recall "<query>" --json`` (env: EIDETIC_DATA_DIR=<tmp>)

    Assert the record comes back with text + full metadata (provenance) + numeric score.
    """
    data_dir = str(tmp_path / "shared_store")

    # Record with rich metadata (author, timestamp, channel, source, permalink).
    record = json.dumps(
        {
            "id": "t8-crossinvoke-1",
            "text": "important team decision: adopt rust for performance",
            "type": "discord",
            "metadata": {
                "source": "discord",
                "channel": "eng-team",
                "author": "alice",
                "timestamp": "2024-06-15T09:30:00Z",
                "permalink": "https://discord.com/channels/123/general/456",
            },
        }
    )

    # Subprocess A: ingest
    result = _cli(["remember", "--json"], stdin=record, data_dir=data_dir)
    assert result.returncode == 0, f"remember failed: {result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["upserted"] == 1
    assert "t8-crossinvoke-1" in payload["ids"]

    # Subprocess B: recall (different invocation, same data_dir)
    result = _cli(
        ["recall", "rust performance", "--top-k", "5", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0, f"recall failed: {result.stderr}"
    hits = json.loads(result.stdout)

    # Find the remembered record in the hits
    hit = next((h for h in hits if h["id"] == "t8-crossinvoke-1"), None)
    assert hit is not None, "remembered record not found in recall results"

    # Verify text round-trips verbatim
    assert hit["text"] == "important team decision: adopt rust for performance"

    # Verify full metadata round-trips (provenance is mandatory)
    assert isinstance(hit["metadata"], dict), "hit must carry a metadata dict"
    assert hit["metadata"]["source"] == "discord"
    assert hit["metadata"]["channel"] == "eng-team"
    assert hit["metadata"]["author"] == "alice"
    assert hit["metadata"]["timestamp"] == "2024-06-15T09:30:00Z"
    assert hit["metadata"]["permalink"] == "https://discord.com/channels/123/general/456"

    # Verify numeric score is present (required for provenance)
    assert isinstance(hit["score"], (int, float)), "hit must carry a numeric score"
    assert hit["score"] >= 0.0, "score must be non-negative"


def test_cross_invocation_multiple_records_scoped(tmp_path: Path) -> None:
    """Ingest multiple records; recall within a scope; verify scope isolation holds.

    Two records in the default public scope; one record in a scoped private scope.
    Public recall must return only the public records.
    """
    data_dir = str(tmp_path / "scoped_store")

    # Record 1: public, default scope
    record1 = json.dumps(
        {
            "id": "t8-scope-public-1",
            "text": "kubernetes cluster configuration guide",
            "type": "docs",
            "metadata": {"source": "docs", "page": "k8s-setup"},
        }
    )

    # Record 2: public, default scope
    record2 = json.dumps(
        {
            "id": "t8-scope-public-2",
            "text": "docker image best practices and optimization",
            "type": "docs",
            "metadata": {"source": "docs", "page": "docker-guide"},
        }
    )

    # Record 3: private, scoped scope (should not appear in public recall)
    record3 = json.dumps(
        {
            "id": "t8-scope-private-1",
            "text": "secret internal api key rotation schedule",
            "type": "internal-secret",
            "metadata": {"source": "internal", "security": "high"},
        }
    )

    # Ingest all three
    for record in [record1, record2]:
        result = _cli(["remember", "--json"], stdin=record, data_dir=data_dir)
        assert result.returncode == 0

    # Ingest private record in a scoped visibility
    result = _cli(
        ["remember", "--scope", "ops:internal", "--visibility", "private", "--json"],
        stdin=record3,
        data_dir=data_dir,
    )
    assert result.returncode == 0

    # Public recall: should return the two public records, not the private one
    # Use keyword search mode (BM25) for more predictable matching of docs/guides
    result = _cli(
        ["recall", "kubernetes docker", "--mode", "keyword", "--top-k", "10", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0
    hits = json.loads(result.stdout)

    hit_ids = {h["id"] for h in hits}
    assert "t8-scope-public-1" in hit_ids
    assert "t8-scope-public-2" in hit_ids
    assert "t8-scope-private-1" not in hit_ids, "private record must not leak to public recall"

    # Private-scoped recall: should return only the private record
    result = _cli(
        [
            "recall",
            "secret api key",
            "--scope",
            "ops:internal",
            "--visibility",
            "private",
            "--json",
        ],
        data_dir=data_dir,
    )
    assert result.returncode == 0
    private_hits = json.loads(result.stdout)

    private_hit_ids = {h["id"] for h in private_hits}
    assert "t8-scope-private-1" in private_hit_ids, "private recall must return private record"


# ------------------------------------------------------------------
# 2. No per-agent fork: single shared store directory
# ------------------------------------------------------------------


def test_no_per_agent_fork_single_shared_dir(tmp_path: Path) -> None:
    """After multiple invocations, verify the store tree is flat (no per-pid/per-scope subdir).

    The shared store must use exactly the one EIDETIC_DATA_DIR configured; no spawned
    per-agent, per-pid, or per-caller subdirectories.  All records live directly under
    the configured path as .jsonl files.
    """
    data_dir = str(tmp_path / "flat_store")

    # Ingest 3 records via separate invocations (simulating different callers/pids)
    for i in range(1, 4):
        record = json.dumps(
            {
                "id": f"t8-flat-{i}",
                "text": f"record number {i} from independent invocation",
                "type": "note",
            }
        )
        result = _cli(["remember", "--json"], stdin=record, data_dir=data_dir)
        assert result.returncode == 0

    # Recall in yet another invocation
    result = _cli(
        ["recall", "record", "--top-k", "10", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0

    # Inspect the directory tree: should be flat, no subdirs for pid/agent/scope-name
    store_path = Path(data_dir)
    assert store_path.exists(), f"data_dir {data_dir} does not exist"

    # List contents: should be .jsonl files only
    contents = list(store_path.iterdir())
    assert len(contents) > 0, "store directory must contain at least one file"

    for item in contents:
        # Must be .jsonl files (scope files), not directories
        assert item.is_file(), f"expected file, found directory: {item}"
        assert item.name.endswith(".jsonl"), f"expected .jsonl file, found: {item.name}"

    # Verify no subdirectories were created (prove no per-agent/pid fork)
    subdirs = [item for item in contents if item.is_dir()]
    assert len(subdirs) == 0, f"no subdirectories should exist; found: {subdirs}"


def test_no_per_agent_fork_records_all_in_single_file(tmp_path: Path) -> None:
    """Ingest 5 records to the default scope; verify all are in one default__public.jsonl file."""
    data_dir = str(tmp_path / "single_file_store")

    # Ingest 5 records, each in a separate subprocess invocation
    for i in range(1, 6):
        record = json.dumps(
            {
                "id": f"t8-single-{i}",
                "text": f"memory item {i}",
                "type": "note",
            }
        )
        result = _cli(["remember", "--json"], stdin=record, data_dir=data_dir)
        assert result.returncode == 0

    # Inspect the directory: should have exactly one .jsonl file (default__public.jsonl)
    store_path = Path(data_dir)
    jsonl_files = list(store_path.glob("*.jsonl"))

    assert len(jsonl_files) == 1, f"expected 1 .jsonl file, found {len(jsonl_files)}: {jsonl_files}"
    assert jsonl_files[0].name == "default__public.jsonl"

    # Read the file and verify it contains all 5 records
    records = []
    for line in jsonl_files[0].read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))

    assert len(records) == 5, f"expected 5 records in file, found {len(records)}"
    record_ids = {r["id"] for r in records}
    for i in range(1, 6):
        assert f"t8-single-{i}" in record_ids


# ------------------------------------------------------------------
# 3. Two distinct "agents" share one store
# ------------------------------------------------------------------


def test_two_agents_share_same_store(tmp_path: Path) -> None:
    """Two different agents write to the same store; a third agent recalls both.

    Agent A: ingest 2 "discord" records
    Agent B: ingest 1 "docs" record
    Agent C: recall and verify all 3 records are visible

    This proves the store is shared, not siloed per caller.
    """
    data_dir = str(tmp_path / "shared_multi_agent")

    # Agent A: ingest 2 discord records
    discord_records = [
        {
            "id": "agent-a-discord-1",
            "text": "team synced on Q3 roadmap priorities",
            "type": "discord",
            "metadata": {"source": "discord", "channel": "planning", "author": "alice"},
        },
        {
            "id": "agent-a-discord-2",
            "text": "performance benchmarks show 3x improvement with optimizations",
            "type": "discord",
            "metadata": {"source": "discord", "channel": "performance", "author": "bob"},
        },
    ]
    for record in discord_records:
        result = _cli(["remember", "--json"], stdin=json.dumps(record), data_dir=data_dir)
        assert result.returncode == 0

    # Agent B: ingest 1 docs record
    docs_record = {
        "id": "agent-b-docs-1",
        "text": "deployment guide covers blue-green and canary strategies",
        "type": "docs",
        "metadata": {"source": "docs", "page": "deployment"},
    }
    result = _cli(["remember", "--json"], stdin=json.dumps(docs_record), data_dir=data_dir)
    assert result.returncode == 0

    # Agent C: recall all records using keyword search for more predictable matching
    result = _cli(
        [
            "recall",
            "roadmap deployment performance",
            "--mode",
            "keyword",
            "--top-k",
            "10",
            "--json",
        ],
        data_dir=data_dir,
    )
    assert result.returncode == 0
    hits = json.loads(result.stdout)

    hit_ids = {h["id"] for h in hits}

    # All three records must be visible to the third invocation
    assert "agent-a-discord-1" in hit_ids, "agent C must recall agent A's first record"
    assert "agent-a-discord-2" in hit_ids, "agent C must recall agent A's second record"
    assert "agent-b-docs-1" in hit_ids, "agent C must recall agent B's record"

    # Verify metadata round-trips for each
    for hit in hits:
        if hit["id"] == "agent-a-discord-1":
            assert hit["metadata"]["channel"] == "planning"
            assert hit["metadata"]["author"] == "alice"
        elif hit["id"] == "agent-a-discord-2":
            assert hit["metadata"]["channel"] == "performance"
            assert hit["metadata"]["author"] == "bob"
        elif hit["id"] == "agent-b-docs-1":
            assert hit["metadata"]["page"] == "deployment"


def test_two_agents_metadata_isolation_within_shared_store(tmp_path: Path) -> None:
    """Agent A and B write records with non-overlapping metadata keys; verify round-trip.

    Proves that the shared store preserves all metadata without siloing by caller.
    """
    data_dir = str(tmp_path / "shared_metadata")

    # Agent A: record with custom metadata keys (source, channel, author, timestamp, permalink)
    agent_a_record = json.dumps(
        {
            "id": "agent-a-custom-1",
            "text": "alert: disk usage at 85% on production server prod-1",
            "type": "alert",
            "metadata": {
                "source": "monitoring",
                "severity": "warning",
                "server": "prod-1",
                "metric": "disk_usage",
            },
        }
    )
    result = _cli(["remember", "--json"], stdin=agent_a_record, data_dir=data_dir)
    assert result.returncode == 0

    # Agent B: record with different custom metadata keys
    agent_b_record = json.dumps(
        {
            "id": "agent-b-custom-1",
            "text": "paper abstract: efficient transformers for long sequences",
            "type": "paper",
            "metadata": {
                "source": "arxiv",
                "arxiv_id": "2401.12345",
                "authors": "Smith et al.",
                "topic": "transformers",
            },
        }
    )
    result = _cli(["remember", "--json"], stdin=agent_b_record, data_dir=data_dir)
    assert result.returncode == 0

    # Recall: verify both records with their distinct metadata round-trip
    # Use keyword search for predictable matching
    result = _cli(
        ["recall", "alert paper transformers", "--mode", "keyword", "--top-k", "10", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0
    hits = json.loads(result.stdout)

    # Find each record
    a_hit = next((h for h in hits if h["id"] == "agent-a-custom-1"), None)
    b_hit = next((h for h in hits if h["id"] == "agent-b-custom-1"), None)

    assert a_hit is not None
    assert b_hit is not None

    # Verify A's metadata
    assert a_hit["metadata"]["source"] == "monitoring"
    assert a_hit["metadata"]["severity"] == "warning"
    assert a_hit["metadata"]["server"] == "prod-1"
    assert a_hit["metadata"]["metric"] == "disk_usage"

    # Verify B's metadata
    assert b_hit["metadata"]["source"] == "arxiv"
    assert b_hit["metadata"]["arxiv_id"] == "2401.12345"
    assert b_hit["metadata"]["authors"] == "Smith et al."
    assert b_hit["metadata"]["topic"] == "transformers"
