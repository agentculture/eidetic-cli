"""``eidetic-cli learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse

from eidetic import __version__
from eidetic.cli._output import emit_result

_TEXT = """\
eidetic-cli — a clonable template for AgentCulture mesh agents.

Purpose
-------
Scaffold for a new Culture mesh agent: an agent-first CLI (cited from the teken
`python-cli` reference), an identity (culture.yaml + CLAUDE.md), the canonical
guildmaster skill kit under .claude/skills/, and a deploy/CI baseline. Clone it,
rename the package, and edit culture.yaml to mint a new agent.

Commands
--------
  eidetic-cli whoami             Identity from culture.yaml.
  eidetic-cli learn              This self-teaching prompt.
  eidetic-cli explain <path>...  Markdown docs for any noun/verb path.
  eidetic-cli overview           Descriptive snapshot of the agent.
  eidetic-cli doctor             Check the agent-identity invariants.
  eidetic-cli remember           Ingest memory records (JSON or NDJSON).
  eidetic-cli recall             Search the memory store.
  eidetic-cli migrate qq         Import legacy QQ memory (files/mongo/neo4j).
  eidetic-cli cli overview       Describe the CLI surface itself.

Machine-readable output
-----------------------
Every command supports --json. Errors in JSON mode emit
{"code", "message", "remediation"} to stderr. Stdout and stderr never mix.

Exit-code policy
----------------
  0 success
  1 user-input error (bad flag, bad path, missing arg)
  2 environment / setup error
  3+ reserved

More detail
-----------
  eidetic-cli explain eidetic-cli
"""


def _as_json_payload() -> dict[str, object]:
    return {
        "tool": "eidetic-cli",
        "version": __version__,
        "purpose": "Clonable scaffold for a new AgentCulture mesh agent.",
        "commands": [
            {"path": ["whoami"], "summary": "Identity probe from culture.yaml."},
            {"path": ["learn"], "summary": "Self-teaching prompt."},
            {"path": ["explain"], "summary": "Markdown docs by path."},
            {"path": ["overview"], "summary": "Descriptive snapshot of the agent."},
            {"path": ["doctor"], "summary": "Check the agent-identity invariants."},
            {"path": ["remember"], "summary": "Ingest memory records (JSON or NDJSON)."},
            {"path": ["recall"], "summary": "Search the memory store."},
            {"path": ["migrate", "qq"], "summary": "Import legacy QQ memory."},
            {"path": ["cli", "overview"], "summary": "Describe the CLI surface."},
        ],
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "eidetic-cli explain <path>",
    }


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        emit_result(_as_json_payload(), json_mode=True)
    else:
        emit_result(_TEXT, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)
