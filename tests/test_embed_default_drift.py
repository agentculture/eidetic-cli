"""Drift test for the embedder endpoint default (docs/contract.md §5).

Three surfaces state the SAME embedder default today: `eidetic/memory/embed.py`'s
`_DEFAULT_BASE_URL`/`_DEFAULT_MODEL` code constants, the `docs/contract.md`
machine-readable block, and the vendored `recall.sh`/`remember.sh` skill
wrapper scripts' `:=` exports. Before eidetic-cli#28 (colleague#293) the code
constants silently disagreed with the other two (a stale
`http://localhost:8101/v1` + `text-embedding-3-small` pair) — this test is the
guard that keeps all three in sync going forward. A future edit that changes
any ONE of these three without updating the others fails this test.
"""

from __future__ import annotations

import re
from pathlib import Path

from eidetic.memory import embed

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = REPO_ROOT / "docs" / "contract.md"
REMEMBER_SH = REPO_ROOT / ".claude" / "skills" / "remember" / "scripts" / "remember.sh"
RECALL_SH = REPO_ROOT / ".claude" / "skills" / "recall" / "scripts" / "recall.sh"


def _parse_embed_block(text: str) -> dict[str, str]:
    """Parse the ```text machine-readable block that carries `embed_default_*`
    keys in docs/contract.md.

    docs/contract.md has more than one ```text fenced block (the scope+
    visibility convention's own block comes first), so this scans every block
    and returns the first one containing an `embed_default_url` key — rather
    than assuming block order — following the same hand-rolled `key: value`
    line parse `tests/test_contract_drift.py` uses (deliberately not YAML;
    eidetic keeps zero runtime deps).
    """
    for match in re.finditer(r"```text\n(.*?)\n```", text, re.DOTALL):
        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        if "embed_default_url" in fields:
            return fields
    raise AssertionError("docs/contract.md is missing its embed_default_url machine-readable block")


def _wrapper_default(script_path: Path, var_name: str) -> str:
    """Extract the literal `:=` default a wrapper exports for *var_name*."""
    text = script_path.read_text(encoding="utf-8")
    match = re.search(r':\s*"\$\{' + re.escape(var_name) + r':=([^}]+)\}"', text)
    assert match is not None, f"{script_path} has no {var_name}:=... default line"
    return match.group(1)


def test_contract_declares_embed_defaults() -> None:
    fields = _parse_embed_block(CONTRACT.read_text(encoding="utf-8"))
    assert fields["embed_default_url"] == "http://localhost:8002/v1"
    assert fields["embed_default_model"] == "Qwen/Qwen3-Embedding-0.6B"


def test_embed_py_code_default_matches_contract() -> None:
    fields = _parse_embed_block(CONTRACT.read_text(encoding="utf-8"))
    assert embed._DEFAULT_BASE_URL == fields["embed_default_url"]
    assert embed._DEFAULT_MODEL == fields["embed_default_model"]


def test_recall_wrapper_default_matches_contract() -> None:
    fields = _parse_embed_block(CONTRACT.read_text(encoding="utf-8"))
    assert _wrapper_default(RECALL_SH, "EIDETIC_EMBED_URL") == fields["embed_default_url"]
    assert _wrapper_default(RECALL_SH, "EIDETIC_EMBED_MODEL") == fields["embed_default_model"]


def test_remember_wrapper_default_matches_contract() -> None:
    fields = _parse_embed_block(CONTRACT.read_text(encoding="utf-8"))
    assert _wrapper_default(REMEMBER_SH, "EIDETIC_EMBED_URL") == fields["embed_default_url"]
    assert _wrapper_default(REMEMBER_SH, "EIDETIC_EMBED_MODEL") == fields["embed_default_model"]
