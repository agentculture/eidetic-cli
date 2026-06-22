"""Tests for eidetic.memory.backend — registry resolution."""

from __future__ import annotations

import pytest

from eidetic.cli._errors import CliError
from eidetic.memory.backend import BACKEND_CHOICES, get_backend


def test_default_backend_resolves() -> None:
    """get_backend() with no args returns the 'files' backend."""
    backend = get_backend()
    assert backend is not None


def test_named_files_backend_resolves() -> None:
    """get_backend('files') returns a backend instance."""
    backend = get_backend("files")
    assert backend is not None


def test_backend_choices_are_uniform_token_set() -> None:
    """The CLI-facing token set is files/mongo/neo4j/graph (issue #12): one list,
    shared by every verb, so a single --backend token works everywhere."""
    assert set(BACKEND_CHOICES) == {"files", "mongo", "neo4j", "graph"}


def test_graph_alias_resolves_to_neo4j_backend() -> None:
    """'graph' is a CLI alias for the neo4j store (issue #12): both resolve to the
    same backend, so get_backend('graph') succeeds and targets neo4j."""
    backend = get_backend("graph")
    assert backend is not None
    # The alias is resolved before the backend is constructed, so the StoreBackend
    # targets neo4j (data-refinery has no notion of a 'graph' backend).
    assert getattr(backend, "_name", None) == "neo4j"


def test_unknown_backend_raises_cli_error() -> None:
    """get_backend with an unknown name raises CliError (env error)."""
    with pytest.raises(CliError) as exc_info:
        get_backend("nonexistent")
    err = exc_info.value
    assert err.code == 2  # EXIT_ENV_ERROR
    assert "nonexistent" in err.message
    assert err.remediation
