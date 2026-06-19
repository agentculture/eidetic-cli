"""Tests for eidetic.memory.scope — isolation guard behaviour."""

from __future__ import annotations

from eidetic.memory.scope import DEFAULT, Scope, can_serve


def test_public_record_served_to_public_scope() -> None:
    """A public record is served to any public query scope."""
    query = Scope(name="default", visibility="public")
    record = Scope(name="default", visibility="public")
    assert can_serve(query, record) is True


def test_public_record_served_to_private_scope() -> None:
    """A public record is served to a private query scope."""
    query = Scope(name="private-a", visibility="private")
    record = Scope(name="default", visibility="public")
    assert can_serve(query, record) is True


def test_private_record_served_to_same_scope() -> None:
    """A private record IS served when the query scope matches."""
    query = Scope(name="private-a", visibility="private")
    record = Scope(name="private-a", visibility="private")
    assert can_serve(query, record) is True


def test_private_record_not_served_to_other_private_scope() -> None:
    """A private record is NOT served to a different private scope."""
    query = Scope(name="private-b", visibility="private")
    record = Scope(name="private-a", visibility="private")
    assert can_serve(query, record) is False


def test_private_record_not_served_to_public_scope() -> None:
    """A private record is NOT served to a public scope."""
    query = Scope(name="default", visibility="public")
    record = Scope(name="private-a", visibility="private")
    assert can_serve(query, record) is False


def test_default_scope_is_public() -> None:
    """DEFAULT scope constant is public."""
    assert DEFAULT.visibility == "public"
    assert DEFAULT.name == "default"
