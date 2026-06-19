"""Tests for eidetic.memory.backend — registry resolution."""

from __future__ import annotations

import pytest

from eidetic.cli._errors import CliError
from eidetic.memory.backend import get_backend


def test_default_backend_resolves() -> None:
    """get_backend() with no args returns the 'files' backend."""
    backend = get_backend()
    assert backend is not None


def test_named_files_backend_resolves() -> None:
    """get_backend('files') returns a backend instance."""
    backend = get_backend("files")
    assert backend is not None


def test_unknown_backend_raises_cli_error() -> None:
    """get_backend with an unknown name raises CliError (env error)."""
    with pytest.raises(CliError) as exc_info:
        get_backend("nonexistent")
    err = exc_info.value
    assert err.code == 2  # EXIT_ENV_ERROR
    assert "nonexistent" in err.message
    assert err.remediation
