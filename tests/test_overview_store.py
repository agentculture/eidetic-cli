"""Tests for `overview --store` — the live store-stats surface.

Covers the honesty conditions from the converged spec
(docs/specs/2026-06-20-eidetic-overview-now-reports-live-store-numbers-re.md):
bare overview stays store-free and byte-identical (h7/h9), counts come from
backend.all() (h1/h3), --backend/--scope narrowing (h4), reachable-but-empty vs
unavailable (h8), a down backend degrades to exit 0 (h5), and the connections
figure counts link-references not edges (h6).

Records are seeded directly through the files backend (not the `remember` CLI) to
keep the store deterministic and independent of the ingest path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eidetic.cli import main
from eidetic.cli._commands import overview as ov
from eidetic.cli._errors import EXIT_ENV_ERROR, CliError
from eidetic.memory.backend import get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


@pytest.fixture
def data_dir(tmp_path: Path) -> str:
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


def _seed(scope: Scope, rid: str, **kw: object) -> None:
    get_backend("files").upsert(
        Record(id=rid, text=f"text-{rid}", type="note", hash="", metadata={}, scope=scope, **kw)
    )


# --- bare overview is untouched (h7/h9) -----------------------------------


def test_bare_overview_has_no_store_section(
    data_dir: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["overview"]) == 0
    out = capsys.readouterr().out
    assert "## Store" not in out
    assert "Identity" in out  # the existing content is still there


def test_bare_overview_json_has_no_store_key(
    data_dir: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["overview", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "store" not in payload
    assert payload["subject"] == "eidetic-cli"


# --- --store adds the section, counts come from the backend (h1/h3) -------


def test_store_flag_reports_live_files_counts(
    data_dir: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(Scope("qq", "private"), "a", links=["b", "x"], supersedes="old1")
    _seed(Scope("qq", "private"), "b")
    _seed(Scope("default", "public"), "c", lifecycle="shadowed")

    assert main(["overview", "--backend", "files"]) == 0
    out = capsys.readouterr().out
    assert "## Store" in out
    assert "files — live: 3 record(s), 2 scope(s), 3 link-connection(s)" in out
    assert "qq/private: 2 (active 2)" in out
    assert "default/public: 1 (shadowed 1)" in out


def test_store_json_payload_shape(data_dir: str, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(Scope("qq", "private"), "a", links=["b", "x"], supersedes="old1")
    assert main(["overview", "--backend", "files", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "eidetic-cli"
    store = payload["store"]
    assert store["scope_filter"] is None
    files = store["backends"][0]
    assert files == {
        "backend": "files",
        "status": "live",
        "total": 1,
        "scopes": [
            {
                "name": "qq",
                "visibility": "private",
                "total": 1,
                "active": 1,
                "shadowed": 0,
                "archived": 0,
            }
        ],
        "connections": 3,
    }


def test_backend_flag_implies_store(data_dir: str, capsys: pytest.CaptureFixture[str]) -> None:
    # --backend (and --scope) imply --store: no explicit --store needed.
    assert main(["overview", "--backend", "files"]) == 0
    assert "## Store" in capsys.readouterr().out


# --- narrowing by scope (h4) ----------------------------------------------


def test_scope_filter_narrows_counts(data_dir: str, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(Scope("qq", "private"), "a")
    _seed(Scope("qq", "private"), "b")
    _seed(Scope("default", "public"), "c")

    assert main(["overview", "--backend", "files", "--scope", "qq", "--json"]) == 0
    store = json.loads(capsys.readouterr().out)["store"]
    assert store["scope_filter"] == "qq"
    files = store["backends"][0]
    assert files["total"] == 2
    assert [s["name"] for s in files["scopes"]] == ["qq"]


def test_unknown_scope_is_zero_not_error(data_dir: str, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(Scope("qq", "private"), "a")
    # An unknown scope yields an explicit zero-count section, exit 0 — not a crash.
    assert main(["overview", "--backend", "files", "--scope", "nonesuch", "--json"]) == 0
    files = json.loads(capsys.readouterr().out)["store"]["backends"][0]
    assert files["status"] == "live"
    assert files["total"] == 0
    assert files["scopes"] == []


# --- reachable-but-empty vs unavailable (h8) ------------------------------


def test_empty_backend_is_live_with_zero(data_dir: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["overview", "--backend", "files", "--json"]) == 0
    files = json.loads(capsys.readouterr().out)["store"]["backends"][0]
    assert files["status"] == "live"
    assert files["total"] == 0


# --- a down backend degrades, exit 0 (h5) ---------------------------------


def test_down_backend_degrades_to_unavailable_exit0(
    data_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = get_backend

    def fake(name: str) -> object:
        if name == "files":
            return real("files")
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="failed to connect: connection refused",
            remediation="start the service",
        )

    monkeypatch.setattr(ov, "get_backend", fake)
    assert main(["overview", "--store", "--json"]) == 0  # default probes all three
    backends = {b["backend"]: b for b in json.loads(capsys.readouterr().out)["store"]["backends"]}
    assert backends["files"]["status"] == "live"
    assert backends["mongo"]["status"] == "unavailable"
    assert "connection refused" in backends["mongo"]["reason"]
    assert backends["graph"]["status"] == "unavailable"


def test_unwrapped_driver_exception_is_swallowed(
    data_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # mongo's lazy find() raises a raw (non-CliError) ServerSelectionTimeoutError;
    # the probe must catch any Exception, not just CliError.
    class _Boom:
        def all(self) -> list[Record]:
            raise RuntimeError("server selection timeout\nverbose second line")

    monkeypatch.setattr(ov, "get_backend", lambda name: _Boom())
    assert main(["overview", "--backend", "mongo", "--json"]) == 0
    backend = json.loads(capsys.readouterr().out)["store"]["backends"][0]
    assert backend["status"] == "unavailable"
    # reason is collapsed to a single trimmed line
    assert backend["reason"] == "server selection timeout"


def test_close_is_called_on_probed_backend(data_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class _Closable:
        def all(self) -> list[Record]:
            return []

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(ov, "get_backend", lambda name: _Closable())
    assert main(["overview", "--backend", "mongo"]) == 0
    assert closed == [True]
