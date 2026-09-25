"""Canonical MCP scope parsing and tool visibility rules."""

from __future__ import annotations

DEFAULT_MCP_SCOPES: tuple[str, ...] = (
    "status",
    "pipeline",
    "device",
    "schedule",
    "confirmation",
)
ALL_MCP_SCOPES: tuple[str, ...] = DEFAULT_MCP_SCOPES + ("raw", "resource", "ops")


def normalize_mcp_scopes(value: str | None) -> tuple[str, ...]:
    """Parse a comma-separated selection in registered group order.

    An absent query parameter selects the safe default set. Explicitly supplying
    an empty set is an error so a typo or malformed URL cannot silently widen a
    client's visible tools back to the default set.
    """
    if value is None:
        return DEFAULT_MCP_SCOPES
    if not isinstance(value, str):
        raise ValueError("MCP scopes must be a comma-separated string")

    selected = {part.strip() for part in value.split(",") if part.strip()}
    if not selected:
        raise ValueError("MCP scopes must select at least one tool group")
    unknown = selected.difference(ALL_MCP_SCOPES)
    if unknown:
        raise ValueError(f"Unknown MCP scope group: {', '.join(sorted(unknown))}")
    return tuple(group for group in ALL_MCP_SCOPES if group in selected)


def is_tool_visible(group: str, scopes: tuple[str, ...]) -> bool:
    """Whether a registered tool group is selected for this request."""
    return group in scopes
