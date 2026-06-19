"""Tests that remember and recall verbs are registered and explainable."""

from __future__ import annotations

import pytest

from eidetic.cli import main


@pytest.mark.parametrize("verb", ["remember", "recall"])
def test_help_exits_zero(verb: str) -> None:
    """main(['<verb>', '--help']) must resolve and exit 0."""
    with pytest.raises(SystemExit) as exc:
        main([verb, "--help"])
    assert exc.value.code == 0


@pytest.mark.parametrize("verb", ["remember", "recall"])
def test_explain_returns_markdown(verb: str) -> None:
    """explain <verb> must return non-empty markdown."""
    import io
    import sys

    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        rc = main(["explain", verb])
    finally:
        sys.stdout = old_stdout
    assert rc == 0
    output = captured.getvalue()
    assert len(output) > 0
    assert "#" in output
