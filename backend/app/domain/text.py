"""Small helpers for user-facing text."""

from __future__ import annotations


def plural(count: int, noun: str, suffix: str = "s") -> str:
    """plural(1, "node") -> "1 node"; plural(3, "node") -> "3 nodes"."""
    return f"{count} {noun}{'' if count == 1 else suffix}"
