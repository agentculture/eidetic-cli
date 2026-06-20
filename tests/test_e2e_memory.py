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
        {"id": "e2e-1", "text": "the quick brown fox jumps over the lazy dog", "type": "note"},
        {"id": "e2e-2", "text": "the lazy dog sleeps under the brown fox", "type": "note"},
        {"id": "e2e-3", "text": "a quick fox and a lazy dog share the same den", "type": "note"},
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

    record = json.dumps({"id": "idempotent-1", "text": "idempotent test record", "type": "note"})

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

    # --- Public recall: each public record is recallable by a matching query,
    #     metadata round-trips verbatim, and the private record NEVER leaks.
    #     (hybrid drops non-matches, so each query targets the record whose
    #     provenance it verifies.) ---
    result = _cli(["recall", "standup notes", "--top-k", "10", "--json"], data_dir=data_dir)
    assert result.returncode == 0
    discord_hits = json.loads(result.stdout)
    discord_ids = {h["id"] for h in discord_hits}
    assert "discord-1" in discord_ids, "public recall must return the discord record"
    assert "claude-private-1" not in discord_ids, "public recall must NOT return the private record"
    discord_hit = next(h for h in discord_hits if h["id"] == "discord-1")
    assert discord_hit["metadata"]["source"] == "discord"
    assert discord_hit["metadata"]["channel"] == "general"
    assert discord_hit["metadata"]["author"] == "alice"
    assert discord_hit["metadata"]["timestamp"] == "2024-01-15T10:00:00Z"
    assert discord_hit["metadata"]["permalink"] == "https://discord.com/channels/123/456/789"

    result = _cli(["recall", "transformer paper", "--top-k", "10", "--json"], data_dir=data_dir)
    assert result.returncode == 0
    research_hits = json.loads(result.stdout)
    research_ids = {h["id"] for h in research_hits}
    assert "research-1" in research_ids, "public recall must return the research record"
    assert (
        "claude-private-1" not in research_ids
    ), "public recall must NOT return the private record"
    research_hit = next(h for h in research_hits if h["id"] == "research-1")
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


# ==================================================================
# Three-layer coherence + spec success-signal suite (t9)
#
# These assert the L1 (migrate) / L2 (freshness signal) / L3 (no-delete
# lifecycle) layers form ONE coherent surface against the REAL CLI, and that
# every spec success-signal is a runnable, passing check.  Behaviours below
# were verified by hand against the current tree before being asserted.
# ==================================================================


def _recall_ids(proc: subprocess.CompletedProcess[str]) -> list[str]:
    """Parse a ``recall --json`` result into the ordered list of hit ids."""
    assert proc.returncode == 0, proc.stderr
    return [h["id"] for h in json.loads(proc.stdout)]


def test_migrated_record_recalls_with_provenance_and_signal(tmp_path: Path) -> None:
    """L1->L2 coherence: a migrated QQ fact ranks immediately, carrying
    provenance (full metadata), a date signature, and a freshness signal."""
    data_dir = str(tmp_path / "memory")
    qq = tmp_path / "core.md"
    qq.write_text(
        "# Core\n\n## Iceland geography\n\nReykjavik is the capital of Iceland.\n",
        encoding="utf-8",
    )

    # One-shot import into the default private `qq` scope.
    result = _cli(["migrate", "qq", "--file", str(qq), "--json"], data_dir=data_dir)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["counts"]["qq-files"] == 1
    assert report["total"] >= 1

    # The migrated fact is recallable in the qq/private scope, with provenance.
    result = _cli(
        [
            "recall",
            "Reykjavik Iceland",
            "--mode",
            "keyword",
            "--scope",
            "qq",
            "--visibility",
            "private",
            "--json",
        ],
        data_dir=data_dir,
    )
    assert result.returncode == 0, result.stderr
    hits = json.loads(result.stdout)
    assert hits, "migrated topic must be recallable in its scope"
    hit = hits[0]
    # Provenance round-trips verbatim.
    assert hit["metadata"]["source"] == "qq-files"
    assert hit["metadata"]["section"] == "Iceland geography"
    assert "path" in hit["metadata"]
    # A date signature and a freshness signal ride along (ranks immediately).
    assert hit["created"] and hit["created"] != "date-unknown"
    assert isinstance(hit["signal"], (int, float))

    # And the personal `qq` data never leaks into a public recall (no-leak).
    public = _cli(
        ["recall", "Reykjavik Iceland", "--mode", "keyword", "--json"],
        data_dir=data_dir,
    )
    assert hit["id"] not in _recall_ids(public), "private qq record must not leak to public recall"


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    """Re-running the migration upserts in place: the same id never duplicates."""
    data_dir = str(tmp_path / "memory")
    qq = tmp_path / "core.md"
    qq.write_text("# Core\n\n## Norway facts\n\nOslo is the capital of Norway.\n", encoding="utf-8")

    first = _cli(["migrate", "qq", "--file", str(qq), "--json"], data_dir=data_dir)
    assert first.returncode == 0
    second = _cli(["migrate", "qq", "--file", str(qq), "--json"], data_dir=data_dir)
    assert second.returncode == 0

    result = _cli(
        [
            "recall",
            "Oslo Norway",
            "--mode",
            "keyword",
            "--scope",
            "qq",
            "--visibility",
            "private",
            "--json",
        ],
        data_dir=data_dir,
    )
    ids = _recall_ids(result)
    qq_ids = [i for i in ids if i.startswith("qq-file:")]
    assert len(qq_ids) == 1, "re-migration must not duplicate the record"


def test_fresh_fact_outranks_year_old_equal_match(tmp_path: Path) -> None:
    """L2 ranking: with equal lexical match, the fresher fact ranks above the
    year-old one because its freshness signal is higher."""
    data_dir = str(tmp_path / "memory")
    # Same structure, distinct trailing token (distinct hash -> both persist),
    # both match the query equally.
    fresh = json.dumps(
        {"id": "fresh", "text": "helsinki is the capital of finland alpha", "type": "note"}
    )
    stale = json.dumps(
        {
            "id": "stale",
            "text": "helsinki is the capital of finland beta",
            "type": "note",
            "created": "2025-05-01T00:00:00+00:00",  # ~> 1y before the 2026 run
        }
    )
    assert _cli(["remember"], stdin=fresh, data_dir=data_dir).returncode == 0
    assert _cli(["remember"], stdin=stale, data_dir=data_dir).returncode == 0

    result = _cli(
        ["recall", "helsinki capital finland", "--mode", "keyword", "--json"],
        data_dir=data_dir,
    )
    assert result.returncode == 0, result.stderr
    hits = json.loads(result.stdout)
    by_id = {h["id"]: h for h in hits}
    assert {"fresh", "stale"} <= set(by_id), "both equally-matching records must be present"
    # The deterministic L2 guarantee: the fresh fact carries a stronger signal...
    assert by_id["fresh"]["signal"] > by_id["stale"]["signal"]
    # ...and therefore ranks ahead of the year-old one.
    order = [h["id"] for h in hits]
    assert order.index("fresh") < order.index("stale"), "fresh must outrank the year-old fact"


def test_supersede_then_sweep_shadows_old_but_recoverable(tmp_path: Path) -> None:
    """L3 (the signal L2 computes is what L3 acts on): an explicit supersedes
    link shadows the older same-scope record on sweep; it drops from default
    recall yet --include-shadowed still returns it (never deleted)."""
    data_dir = str(tmp_path / "memory")
    old = json.dumps({"id": "moon-old", "text": "the earth has one moon", "type": "fact"})
    new = json.dumps(
        {
            "id": "moon-new",
            "text": "the earth has exactly one natural moon named luna",
            "type": "fact",
            "supersedes": "moon-old",
        }
    )
    assert _cli(["remember"], stdin=old, data_dir=data_dir).returncode == 0
    assert _cli(["remember"], stdin=new, data_dir=data_dir).returncode == 0

    sweep = _cli(["sweep", "--json"], data_dir=data_dir)
    assert sweep.returncode == 0, sweep.stderr
    report = json.loads(sweep.stdout)
    assert report["shadowed"] >= 1
    changed = {c["id"]: c["lifecycle"] for c in report["changed"]}
    assert changed.get("moon-old") == "shadowed"

    # Default recall hides the shadowed record but keeps its successor.
    default_ids = _recall_ids(
        _cli(["recall", "moon", "--mode", "keyword", "--json"], data_dir=data_dir)
    )
    assert "moon-old" not in default_ids
    assert "moon-new" in default_ids

    # The explicit flag still returns it — nothing was destroyed.
    shadowed = _cli(
        ["recall", "moon", "--mode", "keyword", "--include-shadowed", "--json"],
        data_dir=data_dir,
    )
    by_id = {h["id"]: h for h in json.loads(shadowed.stdout)}
    assert "moon-old" in by_id, "shadowed record must remain retrievable"
    assert by_id["moon-old"]["lifecycle"] == "shadowed"


def test_year_old_record_archived_but_recoverable(tmp_path: Path) -> None:
    """L3 archival: a >1yr record is archived (not deleted) on sweep — gone from
    default recall, returned under --include-archived, with a low signal that is
    exactly what L3 thresholded on."""
    data_dir = str(tmp_path / "memory")
    ancient = json.dumps(
        {
            "id": "ancient",
            "text": "the dinosaurs went extinct a very long time ago",
            "type": "fact",
            "created": "2024-01-01T00:00:00+00:00",
        }
    )
    assert _cli(["remember"], stdin=ancient, data_dir=data_dir).returncode == 0

    sweep = _cli(["sweep", "--json"], data_dir=data_dir)
    assert sweep.returncode == 0, sweep.stderr
    report = json.loads(sweep.stdout)
    assert report["archived"] >= 1
    changed = {c["id"]: c["lifecycle"] for c in report["changed"]}
    assert changed.get("ancient") == "archived"

    default_ids = _recall_ids(
        _cli(["recall", "dinosaurs", "--mode", "keyword", "--json"], data_dir=data_dir)
    )
    assert "ancient" not in default_ids, "archived record must drop from default recall"

    archived = _cli(
        ["recall", "dinosaurs", "--mode", "keyword", "--include-archived", "--json"],
        data_dir=data_dir,
    )
    by_id = {h["id"]: h for h in json.loads(archived.stdout)}
    assert "ancient" in by_id, "archived record must remain retrievable"
    assert by_id["ancient"]["lifecycle"] == "archived"
    # The L2 signal is what L3 thresholds on: the archived fact's signal is weak.
    assert by_id["ancient"]["signal"] < 0.5


def test_recall_exposes_freshness_signal_distinct_from_score(tmp_path: Path) -> None:
    """Observable after-state: recall surfaces a freshness signal alongside the
    lexical score — the two are independent fields, both present on every hit."""
    data_dir = str(tmp_path / "memory")
    rec = json.dumps({"id": "sig-1", "text": "a fact about freshness signal", "type": "note"})
    assert _cli(["remember"], stdin=rec, data_dir=data_dir).returncode == 0

    result = _cli(["recall", "freshness signal", "--mode", "keyword", "--json"], data_dir=data_dir)
    assert result.returncode == 0
    hit = json.loads(result.stdout)[0]
    assert isinstance(hit["score"], (int, float))
    assert isinstance(hit["signal"], (int, float))
    assert "signal" in hit and "score" in hit
