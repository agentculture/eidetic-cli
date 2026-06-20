"""Tests for the always-on `overview` Store section — live store stats.

Per the converged spec (+ Ori's amendment: bare overview covers ALL stores by
default, with --backend/--scope as narrowing flags): counts come from
backend.all() (h1/h3), --backend/--scope narrow (h4), reachable-but-empty vs
unavailable (h8), a down backend degrades to exit 0 (h5), and the connections
figure counts link-references not edges (h6).

Records are seeded directly through the files backend (not the `remember` CLI) to
keep the store deterministic and independent of the ingest path. The conftest
pins a low store-probe timeout so the suite stays fast.
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


# --- bare overview covers ALL stores by default ---------------------------


def _patch_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """files -> real; mongo/graph -> down. Deterministic regardless of host DBs."""
    real = get_backend

    def fake(name: str, **kw: object) -> object:
        if name == "files":
            return real("files", **kw)
        raise CliError(code=EXIT_ENV_ERROR, message="connection refused", remediation="start it")

    monkeypatch.setattr(ov, "get_backend", fake)


def test_bare_overview_includes_all_stores(
    data_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_all_three(monkeypatch)
    _seed(Scope("default", "public"), "a")
    assert main(["overview"]) == 0
    out = capsys.readouterr().out
    assert "Identity" in out  # the existing content is still there
    assert "## Store" in out  # ...plus the always-on store section
    assert "files — live: 1 record(s)" in out
    assert "mongo — unavailable" in out
    assert "graph — unavailable" in out


def test_bare_overview_json_has_store_key(
    data_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_all_three(monkeypatch)
    assert main(["overview", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "eidetic-cli"
    labels = [b["backend"] for b in payload["store"]["backends"]]
    assert labels == ["files", "mongo", "graph"]


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


def test_backend_flag_narrows_to_one_store(
    data_dir: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # --backend narrows the always-on Store section to a single backend.
    assert main(["overview", "--backend", "files", "--json"]) == 0
    labels = [b["backend"] for b in json.loads(capsys.readouterr().out)["store"]["backends"]]
    assert labels == ["files"]


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


def test_scope_filter_is_name_only_across_visibilities(
    data_dir: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # --scope filters on NAME only, so a name shared across visibilities keeps
    # BOTH records (and surfaces as two scope entries). Documents the semantics.
    _seed(Scope("qq", "public"), "a")
    _seed(Scope("qq", "private"), "b")
    _seed(Scope("other", "public"), "c")

    assert main(["overview", "--backend", "files", "--scope", "qq", "--json"]) == 0
    files = json.loads(capsys.readouterr().out)["store"]["backends"][0]
    assert files["total"] == 2
    assert {(s["name"], s["visibility"]) for s in files["scopes"]} == {
        ("qq", "public"),
        ("qq", "private"),
    }


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

    def fake(name: str, **kw: object) -> object:
        if name == "files":
            return real("files", **kw)
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="failed to connect: connection refused",
            remediation="start the service",
        )

    monkeypatch.setattr(ov, "get_backend", fake)
    assert main(["overview", "--json"]) == 0  # bare overview probes all three
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

    monkeypatch.setattr(ov, "get_backend", lambda name, **kw: _Boom())
    assert main(["overview", "--backend", "mongo", "--json"]) == 0
    backend = json.loads(capsys.readouterr().out)["store"]["backends"][0]
    assert backend["status"] == "unavailable"
    # reason is collapsed to a single trimmed line
    assert backend["reason"] == "server selection timeout"


def test_malformed_record_degrades_not_raises(
    data_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A corrupt store whose all() yields a record with a None scope must degrade
    # to 'unavailable' (aggregation runs inside the try), never raise past exit-0.
    class _Corrupt:
        def all(self) -> list[Record]:
            bad = Record(
                id="x", text="t", type="note", hash="", metadata={}, scope=Scope("s", "public")
            )
            bad.scope = None  # type: ignore[assignment]  # corrupt: trips compute_stats
            return [bad]

    monkeypatch.setattr(ov, "get_backend", lambda name, **kw: _Corrupt())
    assert main(["overview", "--backend", "mongo", "--json"]) == 0
    backend = json.loads(capsys.readouterr().out)["store"]["backends"][0]
    assert backend["status"] == "unavailable"


def test_probe_passes_fast_timeout_to_backend(
    data_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The probe must forward a short timeout_ms to get_backend so a down backend
    # fails fast instead of blocking on the default 5s server-selection timeout.
    seen: dict[str, object] = {}

    class _Empty:
        def all(self) -> list[Record]:
            return []

    def fake(name: str, **kw: object) -> object:
        seen.update(kw)
        return _Empty()

    monkeypatch.setenv("EIDETIC_STORE_PROBE_TIMEOUT_MS", "250")
    monkeypatch.setattr(ov, "get_backend", fake)
    assert main(["overview", "--backend", "mongo"]) == 0
    assert seen.get("timeout_ms") == 250


def test_probe_timeout_falls_back_on_bad_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIDETIC_STORE_PROBE_TIMEOUT_MS", "not-a-number")
    assert ov._probe_timeout_ms() == ov._DEFAULT_PROBE_TIMEOUT_MS


def test_close_is_called_on_probed_backend(data_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class _Closable:
        def all(self) -> list[Record]:
            return []

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(ov, "get_backend", lambda name, **kw: _Closable())
    assert main(["overview", "--backend", "mongo"]) == 0
    assert closed == [True]
