"""Boundary and non-goal guard tests (t7).

Lock the eidetic-cli boundary claims:
  - No hard-delete (source guard + behavioral)
  - No-leak (public/private scope isolation)
  - JSON contract intact (--json emits valid JSON + error shape on stderr)
  - No autonomous extraction / UI (import guard)

All tests are hermetic: use tmp_path + FilesBackend(base_dir=...),
never the real home store.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eidetic.cli import main
from eidetic.memory.backends.files import FilesBackend
from eidetic.memory.lifecycle import compute_transitions
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def tmp_store(tmp_path: pytest.Path) -> str:
    """Temporary memory store directory."""
    store_dir = str(tmp_path / "memory")
    Path(store_dir).mkdir(parents=True, exist_ok=True)
    return store_dir


@pytest.fixture
def backend(tmp_store: str) -> FilesBackend:
    """FilesBackend over a temporary directory."""
    return FilesBackend(base_dir=tmp_store)


def _make_record(
    rid: str = "r1",
    text: str = "hello world",
    scope: Scope | None = None,
    metadata: dict | None = None,
    lifecycle: str = "active",
) -> Record:
    """Factory for test records."""
    if scope is None:
        scope = Scope(name="default", visibility="public")
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata=metadata or {},
        scope=scope,
        lifecycle=lifecycle,
        created=datetime.now(timezone.utc).isoformat(),
    )


# ============================================================================
# TEST GROUP 1: NO HARD-DELETE (source guard)
# ============================================================================


def test_backend_protocol_has_no_delete_method() -> None:
    """The Backend protocol exposes no delete/remove method."""
    from eidetic.memory.backend import Backend

    # Backend is a Protocol; extract its methods using getattr + callable.
    protocol_methods = set()
    for attr in dir(Backend):
        if not attr.startswith("_"):
            val = getattr(Backend, attr, None)
            # Check if it's callable or an UnboundMethod stub
            if callable(val):
                protocol_methods.add(attr)

    assert "upsert" in protocol_methods
    assert "search" in protocol_methods
    assert "all" in protocol_methods
    # Must NOT have delete, remove, or any destructive method.
    destructive = {"delete", "remove", "delete_one", "delete_many", "drop"}
    assert not (
        destructive & protocol_methods
    ), f"Backend protocol must not expose {destructive & protocol_methods}"


def test_no_os_remove_in_memory_module() -> None:
    """grep over eidetic/memory/ finds no os.remove / os.unlink."""
    memory_dir = Path(__file__).parent.parent / "eidetic" / "memory"
    forbidden_patterns = [
        "os.remove(",
        "os.unlink(",
        "shutil.rmtree(",
    ]

    for py_file in memory_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert pattern not in content, (
                f"{py_file.relative_to(memory_dir)}: " f"found forbidden pattern {pattern!r}"
            )


def test_no_database_delete_in_memory_module() -> None:
    """grep over eidetic/memory/ finds no .delete_one / .delete_many / DETACH DELETE."""
    memory_dir = Path(__file__).parent.parent / "eidetic" / "memory"
    forbidden_patterns = [
        ".delete_one(",
        ".delete_many(",
        "DETACH DELETE",
        "DELETE FROM",
    ]

    for py_file in memory_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            # Exclude comments/docstrings by checking for code-pattern context
            # This is conservative: we look for literal substrings that are unlikely
            # to appear in documentation.
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip pure comments and docstrings
                if (
                    stripped.startswith("#")
                    or stripped.startswith('"""')
                    or stripped.startswith("'''")
                ):
                    continue
                if pattern in line:
                    assert False, (
                        f"{py_file.relative_to(memory_dir)}:{i}: "
                        f"found forbidden pattern {pattern!r}"
                    )


def test_no_destructive_ops_in_cli_commands() -> None:
    """grep over eidetic/cli/_commands/ finds no destructive operations."""
    commands_dir = Path(__file__).parent.parent / "eidetic" / "cli" / "_commands"
    forbidden_patterns = [
        "os.remove(",
        "os.unlink(",
        "shutil.rmtree(",
        ".delete_one(",
        ".delete_many(",
        "DETACH DELETE",
    ]

    for py_file in commands_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if pattern in line:
                    assert False, (
                        f"{py_file.relative_to(commands_dir)}:{i}: "
                        f"found forbidden pattern {pattern!r}"
                    )


# ============================================================================
# TEST GROUP 2: NO HARD-DELETE (behavioral guard)
# ============================================================================


def test_lifecycle_transitions_never_remove_records(backend: FilesBackend) -> None:
    """Running lifecycle transitions does not delete records; only marks lifecycle."""
    # Create N records with DISTINCT text to avoid hash-dedup.
    # (Backend upsert deduplicates by hash, so same text = one record in store.)
    records = [
        _make_record(rid="r1", text="first distinct text", lifecycle="active"),
        _make_record(rid="r2", text="second distinct text", lifecycle="active"),
        _make_record(
            rid="r3",
            text="protected record text",
            lifecycle="active",
            metadata={"protected": True},  # protected, exempt from archival
        ),
        _make_record(rid="r4", text="another distinct text", lifecycle="active"),
    ]

    # Insert all N into the backend.
    for rec in records:
        backend.upsert(rec)

    # Verify N records are stored.
    all_before = backend.all()
    n_before = len(all_before)
    assert n_before == 4, f"Expected 4 records, got {n_before}"

    # Run lifecycle engine.
    now = datetime.now(timezone.utc)
    result = compute_transitions(all_before, now)

    # Persist transitions (marking lifecycle, never deleting).
    for rec in result.changed:
        backend.upsert(rec)

    # Verify exactly N records are still in the store (none removed).
    all_after = backend.all()
    n_after = len(all_after)
    assert n_after == n_before, f"Lifecycle transitions removed records: {n_before} → {n_after}"

    # Verify the changed records have updated lifecycle markers.
    by_id = {r.id: r for r in all_after}
    for rec in result.changed:
        assert by_id[rec.id].lifecycle == rec.lifecycle, f"Record {rec.id} lifecycle not persisted"


# ============================================================================
# TEST GROUP 3: NO-LEAK (public/private scope isolation)
# ============================================================================


def test_private_record_not_leaked_in_public_recall(
    backend: FilesBackend, tmp_store: str, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """A private-scope record is NOT returned when querying from public scope."""
    monkeypatch.setenv("EIDETIC_DATA_DIR", tmp_store)

    # Upsert one private and one public record with overlapping text.
    private_scope = Scope(name="personal", visibility="private")
    public_scope = Scope(name="default", visibility="public")

    private_rec = _make_record(
        rid="p1",
        text="secret information",
        scope=private_scope,
    )
    public_rec = _make_record(
        rid="p2",
        text="public information",
        scope=public_scope,
    )

    backend.upsert(private_rec)
    backend.upsert(public_rec)

    # Query from public scope.
    public_results = backend.search(
        "information",
        top_k=10,
        scope=public_scope,
        filters=None,
    )

    # Private record must NOT appear.
    result_ids = [r.id for r in public_results]
    assert "p1" not in result_ids, "Private record leaked to public scope!"
    assert "p2" in result_ids or len(public_results) > 0, "Public record not found"


def test_private_record_visible_to_same_scope(backend: FilesBackend) -> None:
    """A private-scope record IS returned when querying from the same scope."""
    private_scope = Scope(name="personal", visibility="private")

    rec = _make_record(
        rid="p1",
        text="secret info",
        scope=private_scope,
    )
    backend.upsert(rec)

    # Query from the SAME private scope.
    results = backend.search(
        "secret",
        top_k=10,
        scope=private_scope,
        filters=None,
    )

    result_ids = [r.id for r in results]
    assert "p1" in result_ids, "Private record not found in same scope"


def test_public_record_visible_to_any_scope(backend: FilesBackend) -> None:
    """A public-scope record is returned regardless of query scope."""
    public_scope = Scope(name="default", visibility="public")
    private_scope = Scope(name="personal", visibility="private")

    rec = _make_record(
        rid="pub1",
        text="public data",
        scope=public_scope,
    )
    backend.upsert(rec)

    # Query from a private scope.
    results = backend.search(
        "public",
        top_k=10,
        scope=private_scope,
        filters=None,
    )

    result_ids = [r.id for r in results]
    assert "pub1" in result_ids, "Public record should be visible to private scope query"


# ============================================================================
# TEST GROUP 4: JSON CONTRACT (--json emits valid JSON)
# ============================================================================


def test_remember_json_output_is_valid(tmp_store: str, monkeypatch, capsys) -> None:
    """eidetic remember --json emits valid JSON to stdout."""
    monkeypatch.setenv("EIDETIC_DATA_DIR", tmp_store)

    rc = main(
        [
            "remember",
            '{"id": "t1", "text": "hello", "type": "note"}',
            "--json",
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)  # Must parse without error.
    assert payload["upserted"] == 1
    assert "t1" in payload["ids"]


def test_recall_json_output_is_valid(tmp_store: str, monkeypatch, capsys) -> None:
    """eidetic recall --json emits valid JSON list to stdout."""
    monkeypatch.setenv("EIDETIC_DATA_DIR", tmp_store)

    # First, remember a record (text mode is fine for ingest).
    main(
        [
            "remember",
            '{"id": "r1", "text": "hello world", "type": "note"}',
            "--backend",
            "files",
        ]
    )
    capsys.readouterr()  # Clear the remember output.

    # Then recall with --json.
    rc = main(
        [
            "recall",
            "hello",
            "--json",
            "--backend",
            "files",
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)  # Must be a valid JSON list.
    assert isinstance(payload, list)
    if payload:
        assert "id" in payload[0]
        assert "text" in payload[0]
        assert "score" in payload[0]


def test_error_json_shape_on_stderr(tmp_store: str, monkeypatch, capsys) -> None:
    """A --json error emits {code, message, remediation} to stderr (not stdout)."""
    monkeypatch.setenv("EIDETIC_DATA_DIR", tmp_store)

    # Trigger a user error: remember with malformed JSON.
    rc = main(
        [
            "remember",
            "not-valid-json",
            "--json",
        ]
    )

    assert rc != 0
    err_output = capsys.readouterr().err
    payload = json.loads(err_output)  # Must be valid JSON on stderr.
    assert payload["code"] in [1, 2]  # User or env error.
    assert "message" in payload
    assert "remediation" in payload


def test_error_json_not_mixed_with_stdout(tmp_store: str, monkeypatch, capsys) -> None:
    """Error JSON goes only to stderr, not mixed with stdout."""
    monkeypatch.setenv("EIDETIC_DATA_DIR", tmp_store)

    # Trigger an error.
    rc = main(
        [
            "remember",
            "bad-json",
            "--json",
        ]
    )

    assert rc != 0
    captured = capsys.readouterr()
    assert len(captured.out.strip()) == 0, "Error output leaked to stdout"
    assert len(captured.err.strip()) > 0, "Error output missing from stderr"

    # stderr must be valid JSON.
    err_payload = json.loads(captured.err)
    assert "code" in err_payload


def test_text_error_has_error_and_hint_lines(tmp_store: str, monkeypatch, capsys) -> None:
    """A text-mode error emits 'error: <msg>' and 'hint: <remediation>' lines to stderr."""
    monkeypatch.setenv("EIDETIC_DATA_DIR", tmp_store)

    # Trigger a user error in text mode (no --json).
    rc = main(
        [
            "remember",
            "not-json-at-all",
        ]
    )

    assert rc != 0
    err_output = capsys.readouterr().err
    assert "error:" in err_output, "Text error missing 'error:' line"
    assert "hint:" in err_output, "Text error missing 'hint:' line (required by rubric)"


# ============================================================================
# TEST GROUP 5: NO AUTONOMOUS EXTRACTION / UI (import guard)
# ============================================================================


def test_no_flask_imports() -> None:
    """The eidetic package does not import flask."""
    eidetic_dir = Path(__file__).parent.parent / "eidetic"
    forbidden = ["import flask", "from flask"]

    for py_file in eidetic_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in forbidden:
                assert pattern not in line, (
                    f"{py_file.relative_to(eidetic_dir)}:{i}: " f"found {pattern!r} (not allowed)"
                )


def test_no_streamlit_imports() -> None:
    """The eidetic package does not import streamlit."""
    eidetic_dir = Path(__file__).parent.parent / "eidetic"
    forbidden = ["import streamlit", "from streamlit"]

    for py_file in eidetic_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in forbidden:
                assert pattern not in line, (
                    f"{py_file.relative_to(eidetic_dir)}:{i}: " f"found {pattern!r} (not allowed)"
                )


def test_no_fastapi_imports() -> None:
    """The eidetic package does not import fastapi."""
    eidetic_dir = Path(__file__).parent.parent / "eidetic"
    forbidden = ["import fastapi", "from fastapi"]

    for py_file in eidetic_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in forbidden:
                assert pattern not in line, (
                    f"{py_file.relative_to(eidetic_dir)}:{i}: " f"found {pattern!r} (not allowed)"
                )


def test_no_gradio_imports() -> None:
    """The eidetic package does not import gradio."""
    eidetic_dir = Path(__file__).parent.parent / "eidetic"
    forbidden = ["import gradio", "from gradio"]

    for py_file in eidetic_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in forbidden:
                assert pattern not in line, (
                    f"{py_file.relative_to(eidetic_dir)}:{i}: " f"found {pattern!r} (not allowed)"
                )


def test_no_openai_imports() -> None:
    """The eidetic package does not import openai."""
    eidetic_dir = Path(__file__).parent.parent / "eidetic"
    forbidden = ["import openai", "from openai"]

    for py_file in eidetic_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in forbidden:
                assert pattern not in line, (
                    f"{py_file.relative_to(eidetic_dir)}:{i}: " f"found {pattern!r} (not allowed)"
                )


def test_no_anthropic_imports() -> None:
    """The eidetic package does not import anthropic."""
    eidetic_dir = Path(__file__).parent.parent / "eidetic"
    forbidden = ["import anthropic", "from anthropic"]

    for py_file in eidetic_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in forbidden:
                assert pattern not in line, (
                    f"{py_file.relative_to(eidetic_dir)}:{i}: " f"found {pattern!r} (not allowed)"
                )
