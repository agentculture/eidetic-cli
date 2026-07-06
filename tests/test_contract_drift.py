"""Drift test for the memory scope+visibility convention (v1, docs/contract.md).

Three surfaces state the SAME default visibility today: the convention
document, the two vendored skill wrapper scripts, and the plain `eidetic`
CLI's own argparse default. Nothing keeps them in sync automatically — this
test is that guard. It parses the machine-readable block in
``docs/contract.md``, then compares it against:

1. the literal default `--visibility` value each wrapper script injects
   when the caller passes neither `--scope` nor `--visibility` (a text
   check on the committed `.sh` files, not a subprocess run — this test
   fails fast even if bash/uv aren't on PATH), and
2. the CLI's ACTUAL argparse default for `--visibility` on both `remember`
   and `recall`, obtained by exercising the real `register()` + `parse_args`
   path (not by re-implementing argparse introspection) so a future refactor
   of the CLI's default is caught here even if nobody thinks to update this
   test.

A future edit that changes any ONE of these three without updating the
others fails this test.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from eidetic.cli._commands import recall as recall_cmd
from eidetic.cli._commands import remember as remember_cmd

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = REPO_ROOT / "docs" / "contract.md"
REMEMBER_SH = REPO_ROOT / ".claude" / "skills" / "remember" / "scripts" / "remember.sh"
RECALL_SH = REPO_ROOT / ".claude" / "skills" / "recall" / "scripts" / "recall.sh"


def _parse_contract_block(text: str) -> dict[str, str]:
    """Parse the fenced ```text machine-readable block in docs/contract.md.

    Deliberately NOT a YAML parse (eidetic keeps zero runtime deps and reads
    culture.yaml the same hand-rolled way, per
    ``eidetic/cli/_commands/whoami.py``'s ``read_agent_fields`` docstring) —
    a simple ``key: value`` line parse is all the block needs.
    """
    match = re.search(r"```text\n(.*?)\n```", text, re.DOTALL)
    assert match is not None, "docs/contract.md is missing its ```text machine-readable block"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


@pytest.fixture(scope="module")
def contract() -> dict[str, str]:
    text = CONTRACT.read_text(encoding="utf-8")
    return _parse_contract_block(text)


def _wrapper_default_visibility(script_path: Path) -> str:
    """Extract the literal default `--visibility` value a wrapper injects.

    Looks for the one `SCOPE_ARGS+=(--visibility <value>)` line inside the
    "no --scope, no --visibility passed, but a culture.yaml suffix resolved"
    branch — the site that decides what an agent gets when it doesn't pass
    either flag.
    """
    text = script_path.read_text(encoding="utf-8")
    match = re.search(r"SCOPE_ARGS\+=\(--visibility (public|private)\)", text)
    assert match is not None, f"{script_path} has no SCOPE_ARGS+=(--visibility ...) default line"
    return match.group(1)


def _cli_default_visibility(register) -> str:
    """Exercise the real argparse registration + parsing path to obtain the
    CLI's actual runtime default for --visibility (not a re-implementation of
    argparse's own introspection — the real parse_args() call)."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register(sub)
    # `remember` takes a positional record; `recall` takes a positional query.
    # Either accepts an arbitrary string in that slot without touching
    # --visibility, which is exactly what we want to leave at its default.
    verb = sub.choices and next(iter(sub.choices))
    ns = parser.parse_args([verb, "{}"])
    return ns.visibility


def test_contract_declares_public_default(contract: dict[str, str]) -> None:
    assert contract.get("default_visibility") == "public"
    assert contract.get("private_requires_explicit_flag") == "true"


def test_remember_wrapper_default_matches_contract(contract: dict[str, str]) -> None:
    assert _wrapper_default_visibility(REMEMBER_SH) == contract["default_visibility"]


def test_recall_wrapper_default_matches_contract(contract: dict[str, str]) -> None:
    assert _wrapper_default_visibility(RECALL_SH) == contract["default_visibility"]


def test_remember_cli_actual_default_matches_contract(contract: dict[str, str]) -> None:
    assert _cli_default_visibility(remember_cmd.register) == contract["default_visibility"]


def test_recall_cli_actual_default_matches_contract(contract: dict[str, str]) -> None:
    assert _cli_default_visibility(recall_cmd.register) == contract["default_visibility"]
