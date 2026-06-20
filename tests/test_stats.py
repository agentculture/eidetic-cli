"""Unit tests for eidetic.memory.stats — the pure store-stats aggregator.

These exercise the arithmetic only: no backend, no I/O, no clock. The CLI wiring
(graceful per-backend degradation, --store/--backend/--scope) is covered in
tests/test_overview_store.py.
"""

from __future__ import annotations

from eidetic.memory.record import Record
from eidetic.memory.scope import Scope
from eidetic.memory.stats import compute_stats, link_references


def _rec(
    rid: str,
    *,
    scope: Scope | None = None,
    lifecycle: str = "active",
    links: list[str] | None = None,
    supersedes: str | None = None,
) -> Record:
    return Record(
        id=rid,
        text=f"text-{rid}",
        type="note",
        hash="",
        metadata={},
        scope=scope or Scope(name="default", visibility="public"),
        lifecycle=lifecycle,
        links=links or [],
        supersedes=supersedes,
    )


def test_empty_store_is_all_zero() -> None:
    stats = compute_stats([])
    assert stats == {"total": 0, "scopes": [], "connections": 0}


def test_single_record_one_scope_no_connections() -> None:
    stats = compute_stats([_rec("a")])
    assert stats["total"] == 1
    assert stats["connections"] == 0
    assert stats["scopes"] == [
        {
            "name": "default",
            "visibility": "public",
            "total": 1,
            "active": 1,
            "shadowed": 0,
            "archived": 0,
            "contributors": [],
        }
    ]


def test_link_references_counts_links_plus_supersedes() -> None:
    # 2 links + a supersedes == 3 references for this one record.
    assert link_references(_rec("a", links=["b", "c"], supersedes="old")) == 3
    assert link_references(_rec("a", links=["b"])) == 1
    assert link_references(_rec("a", supersedes="old")) == 1
    assert link_references(_rec("a")) == 0


def test_connections_sum_across_records() -> None:
    records = [
        _rec("a", links=["b", "x"], supersedes="old1"),  # 3
        _rec("b"),  # 0
        _rec("c", links=["a"]),  # 1
    ]
    assert compute_stats(records)["connections"] == 4


def test_per_scope_lifecycle_breakdown() -> None:
    qq = Scope(name="qq", visibility="private")
    pub = Scope(name="default", visibility="public")
    records = [
        _rec("a", scope=qq, lifecycle="active"),
        _rec("b", scope=qq, lifecycle="shadowed"),
        _rec("c", scope=pub, lifecycle="archived"),
    ]
    stats = compute_stats(records)
    assert stats["total"] == 3
    by_name = {s["name"]: s for s in stats["scopes"]}
    assert by_name["qq"]["total"] == 2
    assert by_name["qq"]["active"] == 1
    assert by_name["qq"]["shadowed"] == 1
    assert by_name["default"]["archived"] == 1


def test_scopes_sorted_by_name_then_visibility() -> None:
    records = [
        _rec("a", scope=Scope(name="zeta", visibility="public")),
        _rec("b", scope=Scope(name="alpha", visibility="private")),
        _rec("c", scope=Scope(name="alpha", visibility="public")),
    ]
    order = [(s["name"], s["visibility"]) for s in compute_stats(records)["scopes"]]
    assert order == [("alpha", "private"), ("alpha", "public"), ("zeta", "public")]


def test_all_three_lifecycles_in_one_scope() -> None:
    # A single scope carrying one of each lifecycle: the per-bucket breakdown
    # must split cleanly and still sum to total. (Diverse-review edge case.)
    lab = Scope(name="lab", visibility="private")
    records = [
        _rec("a", scope=lab, lifecycle="active"),
        _rec("b", scope=lab, lifecycle="shadowed"),
        _rec("c", scope=lab, lifecycle="archived"),
    ]
    stats = compute_stats(records)
    assert len(stats["scopes"]) == 1
    entry = stats["scopes"][0]
    assert (entry["name"], entry["visibility"]) == ("lab", "private")
    assert entry["total"] == 3
    assert entry["active"] == 1 and entry["shadowed"] == 1 and entry["archived"] == 1


def test_same_name_different_visibility_are_two_entries() -> None:
    # Same scope name but different visibility => two distinct entries with
    # independent counts (the (name, visibility) tuple is the bucket key).
    pub = Scope(name="qq", visibility="public")
    priv = Scope(name="qq", visibility="private")
    stats = compute_stats(
        [
            _rec("a", scope=pub, lifecycle="active"),
            _rec("b", scope=pub, lifecycle="shadowed"),
            _rec("c", scope=priv, lifecycle="archived"),
        ]
    )
    assert [(s["name"], s["visibility"]) for s in stats["scopes"]] == [
        ("qq", "private"),
        ("qq", "public"),
    ]
    by_kv = {(s["name"], s["visibility"]): s for s in stats["scopes"]}
    assert by_kv[("qq", "public")]["total"] == 2
    assert by_kv[("qq", "public")]["shadowed"] == 1
    assert by_kv[("qq", "private")]["archived"] == 1


def test_unknown_lifecycle_is_bucketed_as_active() -> None:
    # A record carrying a lifecycle value outside the known set must not vanish
    # from the per-scope total; it is counted as active (the lenient default).
    stats = compute_stats([_rec("a", lifecycle="weird")])
    scope = stats["scopes"][0]
    assert scope["total"] == 1
    assert scope["active"] == 1
    assert scope["shadowed"] == 0 and scope["archived"] == 0


# ---------------------------------------------------------------------------
# t5: contributors per scope
# ---------------------------------------------------------------------------


def _rec_with_author(
    rid: str,
    *,
    scope: Scope | None = None,
    added_by: str | None = None,
    metadata_author: str | None = None,
    lifecycle: str = "active",
) -> Record:
    """Helper to build records with contributor fields set."""
    meta: dict = {}
    if metadata_author is not None:
        meta["author"] = metadata_author
    return Record(
        id=rid,
        text=f"text-{rid}",
        type="note",
        hash="",
        metadata=meta,
        scope=scope or Scope(name="default", visibility="public"),
        lifecycle=lifecycle,
        added_by=added_by,
    )


def test_contributors_union_of_added_by_and_metadata_author() -> None:
    """Per-scope contributors is the union of added_by and metadata.author."""
    pub = Scope(name="default", visibility="public")
    records = [
        _rec_with_author("a", scope=pub, added_by="alice"),
        _rec_with_author("b", scope=pub, metadata_author="bob"),
        _rec_with_author("c", scope=pub, added_by="carol", metadata_author="alice"),
    ]
    stats = compute_stats(records)
    scope_entry = stats["scopes"][0]
    assert scope_entry["contributors"] == ["alice", "bob", "carol"]


def test_contributors_none_and_empty_values_ignored() -> None:
    """None and empty string contributor values must be excluded."""
    pub = Scope(name="default", visibility="public")
    records = [
        _rec_with_author("a", scope=pub, added_by=None, metadata_author=None),
        _rec_with_author("b", scope=pub, added_by="", metadata_author=""),
        _rec_with_author("c", scope=pub, added_by="alice"),
    ]
    stats = compute_stats(records)
    scope_entry = stats["scopes"][0]
    assert scope_entry["contributors"] == ["alice"]


def test_contributors_non_string_author_does_not_crash() -> None:
    """A non-string metadata.author must not crash the always-on overview.

    Regression (colleague review, PR #10): the contributor set mixed added_by
    (str) with metadata.author (arbitrary type); sorted() then raised TypeError
    on mixed-type comparison. Non-string authors are out of contract and are
    excluded; valid string contributors still surface.
    """
    pub = Scope(name="default", visibility="public")
    records = [
        Record(
            id="a",
            text="int author",
            type="note",
            hash="",
            metadata={"author": 123},
            scope=pub,
            added_by="alice",
        ),
        Record(
            id="b",
            text="list author",
            type="note",
            hash="",
            metadata={"author": ["x", "y"]},
            scope=pub,
            added_by=None,
        ),
    ]
    stats = compute_stats(records)  # must not raise
    assert stats["scopes"][0]["contributors"] == ["alice"]


def test_contributors_distinct_per_scope() -> None:
    """Two scopes maintain independent contributor sets."""
    qq = Scope(name="qq", visibility="private")
    pub = Scope(name="default", visibility="public")
    records = [
        _rec_with_author("a", scope=qq, added_by="alice"),
        _rec_with_author("b", scope=qq, metadata_author="bob"),
        _rec_with_author("c", scope=pub, added_by="charlie"),
    ]
    stats = compute_stats(records)
    by_name = {s["name"]: s for s in stats["scopes"]}
    assert by_name["qq"]["contributors"] == ["alice", "bob"]
    assert by_name["default"]["contributors"] == ["charlie"]


def test_contributors_same_name_different_visibility_are_independent() -> None:
    """Same scope name but different visibility keeps independent contributor sets."""
    pub = Scope(name="qq", visibility="public")
    priv = Scope(name="qq", visibility="private")
    records = [
        _rec_with_author("a", scope=pub, added_by="alice"),
        _rec_with_author("b", scope=priv, added_by="bob"),
    ]
    stats = compute_stats(records)
    by_kv = {(s["name"], s["visibility"]): s for s in stats["scopes"]}
    assert by_kv[("qq", "public")]["contributors"] == ["alice"]
    assert by_kv[("qq", "private")]["contributors"] == ["bob"]


def test_existing_fields_unchanged_with_contributors() -> None:
    """Adding contributors must not change total/active/shadowed/archived/connections."""
    qq = Scope(name="qq", visibility="private")
    records = [
        Record(
            id="a",
            text="text-a",
            type="note",
            hash="",
            metadata={},
            scope=qq,
            lifecycle="active",
            links=["b", "x"],
            supersedes="old1",
            added_by="alice",
        ),
        Record(
            id="b",
            text="text-b",
            type="note",
            hash="",
            metadata={"author": "bob"},
            scope=qq,
            lifecycle="shadowed",
        ),
    ]
    stats = compute_stats(records)
    assert stats["total"] == 2
    assert stats["connections"] == 3  # 2 links + 1 supersedes from record "a"
    scope_entry = stats["scopes"][0]
    assert scope_entry["total"] == 2
    assert scope_entry["active"] == 1
    assert scope_entry["shadowed"] == 1
    assert scope_entry["archived"] == 0
    # contributors set must also be present and correct
    assert scope_entry["contributors"] == ["alice", "bob"]


def test_contributors_sorted_alphabetically() -> None:
    """Contributors list must be stable-sorted for deterministic output."""
    pub = Scope(name="default", visibility="public")
    records = [
        _rec_with_author("a", scope=pub, added_by="zara"),
        _rec_with_author("b", scope=pub, metadata_author="alice"),
        _rec_with_author("c", scope=pub, added_by="mike"),
    ]
    stats = compute_stats(records)
    scope_entry = stats["scopes"][0]
    assert scope_entry["contributors"] == ["alice", "mike", "zara"]


def test_empty_store_contributors_empty_list() -> None:
    """Empty store yields empty scopes list (no contributor sets to check)."""
    stats = compute_stats([])
    assert stats["scopes"] == []
