"""End-to-end / multi-consumer contract tests for eidetic's memory surface.

Exercises the real CLI as a subprocess (the #3-consumer path), not in-process
calls.  All state is isolated via EIDETIC_DATA_DIR pointing at a pytest
tmp_path.
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
    """Run ``python -m eidetic <args>`` as a subprocess."""
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
# 1. Batch ingest then recall
# ------------------------------------------------------------------


def test_batch_ingest_and_recall(tmp_path: Path) -> None:
    """Pipe NDJSON into remember, then recall and verify hit shape."""
    data_dir = str(tmp_path / "memory")

    records = [
        {"id": "e2e-1", "text": "the quick brown fox jumps over the lazy dog"},
        {"id": "e2e-2", "text": "the lazy dog sleeps under the brown fox"},
        {"id": "e2e-3", "text": "a quick fox and a lazy dog share the same den"},
    ]
    ndjson = "\n".join(json.dumps(r) for r in records)

    # Ingest
    result = _cli(["remember", "--json"], stdin=ndjson, data_dir=data_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["upserted"] == 3

    # Recall
    result = _cli(
        ["recall", "fox", "--top-k", "5", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0
    hits = json.loads(result.stdout)
    assert isinstance(hits, list)
    assert len(hits) >= 1
    for hit in hits:
        assert hit["text"], "hit must carry non-empty text"
        assert isinstance(hit["metadata"], dict), "hit must carry a metadata dict"
        assert isinstance(hit["score"], (int, float)), "hit must carry a numeric score"


# ------------------------------------------------------------------
# 2. Idempotent re-ingest
# ------------------------------------------------------------------


def test_idempotent_reingest(tmp_path: Path) -> None:
    """Remember the same record id twice; recall must show exactly one hit."""
    data_dir = str(tmp_path / "memory")

    record = json.dumps({"id": "idempotent-1", "text": "idempotent test record"})

    # First ingest
    result = _cli(["remember", "--json"], stdin=record, data_dir=data_dir)
    assert result.returncode == 0

    # Second ingest (same id)
    result = _cli(["remember", "--json"], stdin=record, data_dir=data_dir)
    assert result.returncode == 0

    # Recall and verify count
    result = _cli(
        ["recall", "idempotent", "--top-k", "5", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0
    hits = json.loads(result.stdout)
    ids = [h["id"] for h in hits if h["id"] == "idempotent-1"]
    assert len(ids) == 1, "the id must appear exactly once after re-ingest"


# ------------------------------------------------------------------
# 3. Multi-consumer round-trip (no per-consumer code)
# ------------------------------------------------------------------


def test_multi_consumer_roundtrip(tmp_path: Path) -> None:
    """Three different record types through the same remember/recall interface.

    * A discord-style record (public)
    * A research-style record (public)
    * A private claude-memory record

    Public recall returns the two public records with full metadata and NEVER
    the private record.  A scoped private recall DOES return the private
    record.
    """
    data_dir = str(tmp_path / "memory")

    # --- Discord record ---
    discord_record = json.dumps(
        {
            "id": "discord-1",
            "text": "team standup notes for today",
            "type": "discord",
            "metadata": {
                "source": "discord",
                "channel": "general",
                "author": "alice",
                "timestamp": "2024-01-15T10:00:00Z",
                "permalink": "https://discord.com/channels/123/456/789",
            },
        }
    )
    result = _cli(["remember", "--json"], stdin=discord_record, data_dir=data_dir)
    assert result.returncode == 0

    # --- Research record ---
    research_record = json.dumps(
        {
            "id": "research-1",
            "text": "transformer attention mechanism paper",
            "type": "ClaimMemory",
            "metadata": {
                "paper": "Attention Is All You Need",
                "producer": "research-team",
            },
        }
    )
    result = _cli(["remember", "--json"], stdin=research_record, data_dir=data_dir)
    assert result.returncode == 0

    # --- Private claude-memory record ---
    private_record = json.dumps(
        {
            "id": "claude-private-1",
            "text": "my identity fact: I am an AI assistant",
            "type": "claude-memory",
        }
    )
    result = _cli(
        ["remember", "--scope", "claude:test", "--visibility", "private", "--json"],
        stdin=private_record,
        data_dir=data_dir,
    )
    assert result.returncode == 0

    # --- Public recall: must return discord + research, NEVER private ---
    result = _cli(
        ["recall", "notes", "--top-k", "10", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0
    public_hits = json.loads(result.stdout)
    public_ids = {h["id"] for h in public_hits}

    assert "discord-1" in public_ids, "public recall must return the discord record"
    assert "research-1" in public_ids, "public recall must return the research record"
    assert "claude-private-1" not in public_ids, "public recall must NOT return the private record"

    # Verify metadata round-trips verbatim
    discord_hit = next(h for h in public_hits if h["id"] == "discord-1")
    assert discord_hit["metadata"]["source"] == "discord"
    assert discord_hit["metadata"]["channel"] == "general"
    assert discord_hit["metadata"]["author"] == "alice"
    assert discord_hit["metadata"]["timestamp"] == "2024-01-15T10:00:00Z"
    assert discord_hit["metadata"]["permalink"] == "https://discord.com/channels/123/456/789"

    research_hit = next(h for h in public_hits if h["id"] == "research-1")
    assert research_hit["metadata"]["paper"] == "Attention Is All You Need"
    assert research_hit["metadata"]["producer"] == "research-team"

    # --- Private scoped recall: MUST return the private record ---
    result = _cli(
        ["recall", "identity", "--scope", "claude:test", "--visibility", "private", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0
    private_hits = json.loads(result.stdout)
    private_ids = {h["id"] for h in private_hits}
    assert "claude-private-1" in private_ids, "private scoped recall must return the private record"
