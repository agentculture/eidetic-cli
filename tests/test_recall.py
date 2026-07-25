"""Tests for eidetic.cli._commands.recall — the recall verb.

Recall's ``--json`` payload is the **composite bundle** (t4): one object
``{query, mode, truncated, items}`` where every item is a full record
round-trip plus a ``tier`` (``primary`` for a search hit, ``traversal`` for a
record reached by the links/supersedes walk) and a ``depth`` (0 for primary,
hop distance for traversal). The old bare-list shape is gone.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
from typing import Any

import pytest

from eidetic.cli import main
from eidetic.cli._commands import recall
from eidetic.cli._errors import EXIT_USER_ERROR, CliError
from eidetic.memory.backend import Backend, get_backend
from eidetic.memory.record import Record
from eidetic.memory.scope import Scope


def _make_record(
    rid: str = "r1",
    text: str = "hello world",
    scope: Scope | None = None,
    metadata: dict | None = None,
    links: list[str] | None = None,
    supersedes: str | None = None,
) -> Record:
    return Record(
        id=rid,
        text=text,
        type="note",
        hash="",
        metadata=metadata or {},
        scope=scope or Scope(name="default", visibility="public"),
        links=links or [],
        supersedes=supersedes,
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> str:
    d = str(tmp_path / "memory")
    os.environ["EIDETIC_DATA_DIR"] = d
    return d


@pytest.fixture
def seeded(data_dir: str) -> None:
    """Store several records across scopes for recall tests."""
    backend = get_backend("files")
    backend.upsert(_make_record("a1", "alpha record", metadata={"tag": "alpha"}))
    backend.upsert(_make_record("b1", "beta record", metadata={"tag": "beta"}))
    backend.upsert(_make_record("c1", "gamma record", metadata={"tag": "gamma"}))
    backend.upsert(_make_record("d1", "delta record", metadata={"tag": "alpha"}))
    # Private record in a secret scope
    backend.upsert(
        _make_record(
            "secret1",
            "secret record",
            scope=Scope(name="secret", visibility="private"),
            metadata={"tag": "secret"},
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    recall.register(sub)
    return parser


def _recall_parser() -> argparse.ArgumentParser:
    """Return recall's OWN subparser (for surface introspection)."""
    parser = argparse.ArgumentParser(prog="eidetic-cli")
    sub = parser.add_subparsers(dest="command")
    recall.register(sub)
    return sub.choices["recall"]


def _bundle(capsys) -> dict[str, Any]:
    """Parse recall's ``--json`` composite bundle object off stdout."""
    return json.loads(capsys.readouterr().out)


def _items(capsys) -> list[dict[str, Any]]:
    """Parse the bundle and return its ``items`` list."""
    return _bundle(capsys)["items"]


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


# ---------------------------------------------------------------------------
# Existing recall behaviour (shape assertions migrated to the bundle)
# ---------------------------------------------------------------------------


def test_recall_json_returns_hits_with_provenance(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = _items(capsys)
    assert len(hits) > 0
    for hit in hits:
        assert "text" in hit
        assert "metadata" in hit
        assert "score" in hit
    primary = [h for h in hits if h["tier"] == "primary"]
    assert primary, "a matching query must yield primary hits"
    for hit in primary:
        assert isinstance(hit["score"], (int, float))


def test_recall_top_k_limits_results(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--top-k", "2", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = _items(capsys)
    assert len([h for h in hits if h["tier"] == "primary"]) <= 2


def test_recall_filter_narrows_results(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--filter", "tag=alpha", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = _items(capsys)
    assert len(hits) > 0
    for hit in hits:
        assert hit["metadata"].get("tag") == "alpha"


def test_recall_public_scope_never_returns_private_record(
    data_dir: str, seeded: None, capsys
) -> None:
    """A recall in the public 'default' scope must NOT return the private 'secret' record."""
    parser = _build_parser()
    args = parser.parse_args(
        [
            "recall",
            "secret",
            "--scope",
            "default",
            "--visibility",
            "public",
            "--json",
        ]
    )
    rc = args.func(args)
    assert rc == 0
    for hit in _items(capsys):
        assert hit["id"] != "secret1"


def test_recall_text_mode(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record"])
    rc = args.func(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "score:" in out
    assert "text:" in out


def test_recall_malformed_filter_raises_cli_error(data_dir: str, seeded: None) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--filter", "badfilter"])
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR


def test_recall_default_mode_is_hybrid() -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "anything"])
    assert args.mode == "hybrid"
    assert args.alpha == 0.5
    assert args.case_sensitive is False


def test_recall_exact_mode_matches_substring(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "alpha record", "--mode", "exact", "--json"])
    rc = args.func(args)
    assert rc == 0
    assert [h["id"] for h in _items(capsys)] == ["a1"]


def test_recall_keyword_mode_drops_non_matches(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "alpha", "--mode", "keyword", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = _items(capsys)
    # 'alpha' appears in a1's text only ("alpha record"); others have no overlap.
    assert {h["id"] for h in hits} == {"a1"}
    assert all(h["score"] > 0.0 for h in hits)


def test_recall_hybrid_mode_returns_scored_hits(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "hybrid", "--json"])
    rc = args.func(args)
    assert rc == 0
    hits = _items(capsys)
    assert len(hits) > 0
    for hit in hits:
        assert hit["score"] is not None


def test_recall_bad_alpha_raises_cli_error(data_dir: str, seeded: None) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", "--mode", "hybrid", "--alpha", "2.0"])
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# t4 — composite bundle: shape, tiers, and the traversal wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def linked(data_dir: str) -> None:
    """A small linked graph: only ``chain0`` matches the query lexically.

    ``chain0 --links--> chain1 --links--> chain2`` and ``chain0`` also
    supersedes ``older``. None of chain1/chain2/older share a token with the
    query, so they can ONLY enter the bundle through the traversal.
    """
    backend = get_backend("files")
    backend.upsert(
        _make_record(
            "chain0",
            "quokka sightings register",
            metadata={"source": "discord"},
            links=["chain1"],
            supersedes="older",
        )
    )
    backend.upsert(
        _make_record("chain1", "neighbour one text", metadata={"source": "docs"}, links=["chain2"])
    )
    backend.upsert(_make_record("chain2", "neighbour two text", metadata={"source": "docs"}))
    backend.upsert(_make_record("older", "superseded text", metadata={"source": "discord"}))


def _recall_bundle(capsys, *argv: str) -> dict[str, Any]:
    """Run ``recall`` with *argv* (``--json`` implied) and return the bundle."""
    parser = _build_parser()
    args = parser.parse_args(["recall", *argv, "--json"])
    assert args.func(args) == 0
    return _bundle(capsys)


def test_bundle_is_one_object_with_the_documented_keys(data_dir: str, linked: None, capsys) -> None:
    """``--json`` emits ONE object: {query, mode, truncated, items} — not a list."""
    bundle = _recall_bundle(capsys, "quokka", "--mode", "keyword")
    assert isinstance(bundle, dict)
    assert set(bundle) == {"query", "mode", "truncated", "items"}
    assert bundle["query"] == "quokka"
    assert bundle["mode"] == "keyword"
    assert isinstance(bundle["truncated"], bool)
    assert isinstance(bundle["items"], list)


def test_every_item_carries_tier_and_depth(data_dir: str, linked: None, capsys) -> None:
    """Tier attribution needs no heuristics: every item is labelled."""
    bundle = _recall_bundle(capsys, "quokka", "--mode", "keyword")
    items = bundle["items"]
    assert items
    for item in items:
        assert item["tier"] in {"primary", "traversal"}
        assert isinstance(item["depth"], int)
    by_id = _by_id(items)
    assert by_id["chain0"]["tier"] == "primary"
    assert by_id["chain0"]["depth"] == 0
    assert by_id["chain1"]["tier"] == "traversal"
    assert by_id["chain1"]["depth"] == 1


def test_every_item_keeps_the_full_record_shape(data_dir: str, linked: None, capsys) -> None:
    """Full metadata round-trip is a hard consumer requirement (issue #3)."""
    bundle = _recall_bundle(capsys, "quokka", "--mode", "keyword")
    record_fields = set(_make_record().to_dict())
    for item in bundle["items"]:
        assert record_fields <= set(item), f"item {item['id']} dropped record fields"
    by_id = _by_id(bundle["items"])
    assert by_id["chain0"]["metadata"] == {"source": "discord"}
    assert by_id["chain1"]["metadata"] == {"source": "docs"}
    assert by_id["chain1"]["scope"] == {"name": "default", "visibility": "public"}


def test_item_text_is_byte_identical_to_the_stored_text(
    data_dir: str, linked: None, capsys
) -> None:
    """No paraphrase, no truncation — the bundle carries verbatim stored text."""
    stored = {r.id: r.text for r in get_backend("files").all()}
    bundle = _recall_bundle(capsys, "quokka", "--mode", "keyword")
    for item in bundle["items"]:
        assert item["text"] == stored[item["id"]]


def test_traversal_items_appear_via_links_and_supersedes(
    data_dir: str, linked: None, capsys
) -> None:
    """The graph walk reaches records the search itself never matched."""
    bundle = _recall_bundle(capsys, "quokka", "--mode", "keyword")
    by_id = _by_id(bundle["items"])
    assert "chain1" in by_id, "a links neighbour must be discovered"
    assert "older" in by_id, "a supersedes neighbour must be discovered"
    assert by_id["older"]["tier"] == "traversal"
    assert by_id["older"]["depth"] == 1


def test_depth_zero_gives_a_flat_primary_only_bundle(data_dir: str, linked: None, capsys) -> None:
    """``--depth 0`` is the documented escape hatch: no traversal is attempted."""
    bundle = _recall_bundle(capsys, "quokka", "--mode", "keyword", "--depth", "0")
    assert [item["tier"] for item in bundle["items"]] == ["primary"]
    assert [item["depth"] for item in bundle["items"]] == [0]
    assert bundle["truncated"] is False, "opting out of traversal is not a cut"


def test_depth_two_reaches_the_second_hop(data_dir: str, linked: None, capsys) -> None:
    bundle = _recall_bundle(capsys, "quokka", "--mode", "keyword", "--depth", "2")
    by_id = _by_id(bundle["items"])
    assert by_id["chain2"]["tier"] == "traversal"
    assert by_id["chain2"]["depth"] == 2
    assert bundle["truncated"] is False, "the whole graph fits inside depth 2"


def test_depth_bound_truncation_is_reported(data_dir: str, linked: None, capsys) -> None:
    """The default depth stops one hop short of chain2 — and says so."""
    bundle = _recall_bundle(capsys, "quokka", "--mode", "keyword")
    assert "chain2" not in _by_id(bundle["items"])
    assert bundle["truncated"] is True, "a depth-bounded cut must be visible"


def test_max_nodes_bound_truncation_is_reported(data_dir: str, linked: None, capsys) -> None:
    bundle = _recall_bundle(
        capsys, "quokka", "--mode", "keyword", "--depth", "2", "--max-nodes", "1"
    )
    traversal = [item for item in bundle["items"] if item["tier"] == "traversal"]
    assert len(traversal) == 1
    assert bundle["truncated"] is True, "a node-bounded cut must be visible"


def test_bundle_bound_defaults(data_dir: str) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "anything"])
    assert args.depth == 1
    assert args.max_nodes == 20
    assert args.source is None


# ---------------------------------------------------------------------------
# t4 — --source filtering across BOTH tiers
# ---------------------------------------------------------------------------


def test_source_filters_the_primary_tier(data_dir: str, capsys) -> None:
    backend = get_backend("files")
    backend.upsert(
        _make_record("s-discord", "kestrel report from chat", metadata={"source": "discord"})
    )
    backend.upsert(
        _make_record("s-docs", "kestrel report from the manual", metadata={"source": "docs"})
    )

    bundle = _recall_bundle(capsys, "kestrel", "--mode", "keyword", "--source", "discord")
    assert {item["id"] for item in bundle["items"]} == {"s-discord"}


def test_source_filters_the_traversal_tier_too(data_dir: str, linked: None, capsys) -> None:
    """``--source`` applies uniformly to every tier, primary and traversal alike."""
    without = _recall_bundle(capsys, "quokka", "--mode", "keyword", "--depth", "2")
    assert "chain1" in _by_id(without["items"]), "chain1 (source=docs) is reachable by default"

    bundle = _recall_bundle(
        capsys, "quokka", "--mode", "keyword", "--depth", "2", "--source", "discord"
    )
    ids = _by_id(bundle["items"])
    assert "chain0" in ids and "older" in ids, "both source=discord records survive"
    assert "chain1" not in ids, "--source must exclude a source=docs traversal discovery"
    assert "chain2" not in ids, "a filtered-out neighbour is a dead end, not a transit node"
    for item in bundle["items"]:
        assert item["metadata"]["source"] == "discord"


def test_source_conflicting_with_filter_raises_cli_error(data_dir: str, seeded: None) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["recall", "record", "--source", "discord", "--filter", "source=docs", "--json"]
    )
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR
    assert exc_info.value.remediation


# ---------------------------------------------------------------------------
# t4 — per-hop no-leak (the traversal is a brand-new leak path)
# ---------------------------------------------------------------------------


@pytest.fixture
def linked_private(data_dir: str) -> None:
    """A PUBLIC record whose ``links`` point at a PRIVATE record in another scope.

    The private record shares no token with the query, so the only way it can
    reach a bundle at all is through the traversal — which is exactly the leak
    path under test.
    """
    backend = get_backend("files")
    backend.upsert(
        _make_record(
            "pub-seed",
            "narwhal briefing",
            metadata={"source": "discord"},
            links=["priv-linked"],
        )
    )
    backend.upsert(
        _make_record(
            "priv-linked",
            "classified payload dossier",
            scope=Scope(name="secret", visibility="private"),
            metadata={"source": "discord"},
        )
    )


def test_private_linked_record_never_enters_a_public_bundle(
    data_dir: str, linked_private: None, capsys
) -> None:
    """A private record reachable via links from a public hit must never appear."""
    bundle = _recall_bundle(capsys, "narwhal", "--mode", "keyword", "--depth", "3")
    ids = {item["id"] for item in bundle["items"]}
    assert "pub-seed" in ids
    assert "priv-linked" not in ids, "private record leaked into a public bundle"


def test_private_linked_record_is_visible_to_its_own_scope(
    data_dir: str, linked_private: None, capsys
) -> None:
    """The no-leak guard is scope-correct, not a blanket drop: the owning scope sees it."""
    bundle = _recall_bundle(
        capsys,
        "narwhal",
        "--mode",
        "keyword",
        "--scope",
        "secret",
        "--visibility",
        "private",
        "--depth",
        "3",
    )
    by_id = _by_id(bundle["items"])
    assert "priv-linked" in by_id
    assert by_id["priv-linked"]["tier"] == "traversal"


class _UnfilteredBackend:
    """A backend whose ``get_many`` deliberately IGNORES scope visibility.

    ``StoreBackend.get_many`` already applies ``can_serve`` per store dir, so a
    leak test against the real backend passes even when recall forgets to hand
    a ``can_serve`` predicate to the traversal — the store would have filtered
    the record out anyway, making the assertion vacuous. This stand-in hands
    back every id it knows, so the ONLY thing that can keep a private record
    out of the bundle is the per-hop predicate recall injects into
    ``discover``. Drop that predicate and the leak test below fails.
    """

    def __init__(self, inner: Backend, records: list[Record]) -> None:
        self._inner = inner
        self._records = {r.id: r for r in records}

    def upsert(self, record: Record) -> None:
        self._inner.upsert(record)

    def all(self) -> list[Record]:
        return self._inner.all()

    def search(self, *args: Any, **kwargs: Any) -> list[Record]:
        return self._inner.search(*args, **kwargs)

    def get_many(self, ids: list[str], scope: Scope) -> dict[str, Record]:
        return {rid: self._records[rid] for rid in ids if rid in self._records}


def test_traversal_leak_guard_holds_even_when_the_store_does_not_filter(
    data_dir: str, linked_private: None, capsys, monkeypatch
) -> None:
    """Discriminating no-leak regression: the predicate is what stops the leak.

    The store stand-in resolves ``priv-linked`` for a PUBLIC query (asserted
    below, so the test can never go vacuous). Only the ``can_serve``-bound
    predicate recall passes into ``discover`` keeps it out of the bundle.
    """
    inner = get_backend("files")
    unfiltered = _UnfilteredBackend(inner, inner.all())
    public = Scope(name="default", visibility="public")

    # The stand-in really does leak by construction — this is what makes the
    # assertion below discriminating rather than vacuous.
    assert "priv-linked" in unfiltered.get_many(["priv-linked"], public)

    monkeypatch.setattr(recall, "get_backend", lambda *_a, **_kw: unfiltered)
    bundle = _recall_bundle(capsys, "narwhal", "--mode", "keyword", "--depth", "3")

    ids = {item["id"] for item in bundle["items"]}
    assert "pub-seed" in ids, "the public seed must still be a primary hit"
    assert "priv-linked" not in ids, "per-hop can_serve must reject the private neighbour"


def test_traversal_skips_shadowed_and_archived_neighbours(data_dir: str, capsys) -> None:
    """Lifecycle policy is uniform: a hidden neighbour stays hidden in the bundle."""
    backend = get_backend("files")
    backend.upsert(
        _make_record("lc-seed", "ibex census", links=["lc-shadowed", "lc-archived", "lc-active"])
    )
    backend.upsert(_make_record("lc-active", "plain neighbour"))
    shadowed = _make_record("lc-shadowed", "shadowed neighbour")
    shadowed.lifecycle = "shadowed"
    backend.upsert(shadowed)
    archived = _make_record("lc-archived", "archived neighbour")
    archived.lifecycle = "archived"
    backend.upsert(archived)

    bundle = _recall_bundle(capsys, "ibex", "--mode", "keyword")
    ids = {item["id"] for item in bundle["items"]}
    assert ids == {"lc-seed", "lc-active"}

    with_shadowed = _recall_bundle(capsys, "ibex", "--mode", "keyword", "--include-shadowed")
    assert "lc-shadowed" in {item["id"] for item in with_shadowed["items"]}


# ---------------------------------------------------------------------------
# t4 — reinforcement stays exactly as it was (graded bumps are t5's scope)
# ---------------------------------------------------------------------------


def test_primary_hits_still_reinforce_and_traversal_items_do_not_yet(
    data_dir: str, linked: None, capsys
) -> None:
    """t4 boundary: primary hits bump as they always did; traversal items do not.

    Graded (fractional, depth-decayed) reinforcement of traversal discoveries
    is task t5 — this assertion pins the untouched t4 behaviour so the change
    is visible when t5 lands.
    """
    _recall_bundle(capsys, "quokka", "--mode", "keyword")
    stored = {r.id: r for r in get_backend("files").all()}
    assert stored["chain0"].recall_count == 1
    assert stored["chain0"].last_recall is not None
    assert stored["chain1"].recall_count == 0
    assert stored["chain1"].last_recall is None


# ---------------------------------------------------------------------------
# t4 — text mode
# ---------------------------------------------------------------------------


def test_text_mode_renders_tiers_readably(data_dir: str, linked: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "quokka", "--mode", "keyword"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "primary" in out
    assert "traversal" in out
    assert "score:" in out
    assert "text:" in out
    assert "truncated:" in out
    assert "quokka sightings register" in out
    assert "neighbour one text" in out


def test_text_mode_reports_no_results(data_dir: str, seeded: None, capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "zzzznomatch", "--mode", "keyword"])
    assert args.func(args) == 0
    assert "(no results)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# t4 — error contract on the new flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag,value", [("--depth", "-1"), ("--max-nodes", "-5")])
def test_negative_bounds_raise_cli_error(
    data_dir: str, seeded: None, flag: str, value: str
) -> None:
    parser = _build_parser()
    args = parser.parse_args(["recall", "record", flag, value, "--json"])
    with pytest.raises(CliError) as exc_info:
        args.func(args)
    assert exc_info.value.code == EXIT_USER_ERROR
    assert exc_info.value.remediation, "the error must carry a remediation hint"


def test_bad_depth_exits_one_with_a_hint_line(data_dir: str, seeded: None, capsys) -> None:
    """Through main(): exit 1, structured stderr with the rubric's ``hint:`` line."""
    rc = main(["recall", "record", "--depth", "-1"])
    captured = capsys.readouterr()
    assert rc == EXIT_USER_ERROR
    assert captured.out == ""
    assert "error:" in captured.err
    assert "hint:" in captured.err


def test_non_integer_depth_exits_one_with_a_hint_line(data_dir: str, seeded: None, capsys) -> None:
    """Argparse's own rejection routes through the same structured contract."""
    with pytest.raises(SystemExit) as exc_info:
        main(["recall", "record", "--depth", "deep"])
    captured = capsys.readouterr()
    assert exc_info.value.code == EXIT_USER_ERROR
    assert "hint:" in captured.err


# ---------------------------------------------------------------------------
# t4 — boundaries: no synthesis on the path, no ingest parameter
# ---------------------------------------------------------------------------


def _imported_roots(path: Path) -> set[str]:
    """Return the set of top-level module names imported by the file at *path*."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_bundle_path_makes_no_generative_or_extra_network_call() -> None:
    """Source guard: the bundle path assembles raw records — it never generates text.

    The only network call on the path stays the existing embeddings endpoint
    (reached through ``scoring.rank`` -> ``EmbedClient``). Neither the recall
    command nor the traversal engine may name a chat/completion client or reach
    an endpoint of its own, and both may import only the stdlib plus eidetic.
    """
    import eidetic.memory.traverse as traverse_mod

    allowed_roots = {
        "__future__",
        "argparse",
        "collections",
        "copy",
        "dataclasses",
        "datetime",
        "eidetic",
        "typing",
    }
    forbidden = ("openai", "anthropic", "chat.completion", "http://", "https://")
    for module in (recall, traverse_mod):
        path = Path(module.__file__)
        source = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in source, f"{module.__name__} must not reference {token!r}"
        extra = _imported_roots(path) - allowed_roots
        assert not extra, f"{module.__name__} imports unexpected modules: {sorted(extra)}"


def test_bundle_surface_exposes_no_ingest_parameter() -> None:
    """recall accepts no caller-supplied content to persist — it is fetch-only."""
    help_text = _recall_parser().format_help()
    for ingesting in ("--record", "--text ", "--content", "--metadata", "--added-by"):
        assert ingesting not in help_text
