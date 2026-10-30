"""Core traceability data structures and shared node validation."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_ACTIVE_STATUS = "APPROVED"
DEFAULT_DEPRECATED_STATUS = "DEPRECATED"
NODE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Node:
    """A single traceability node in the project graph."""

    id: str
    type: str
    status: str
    links: tuple[str, ...] = ()
    location: str = ""


@dataclass(frozen=True)
class Rule:
    """A declarative status and link coverage constraint."""

    id: str
    source_type: str
    when_status: str
    target_type: str
    target_status: tuple[str, ...]


@dataclass(frozen=True)
class Violation:
    """A single traceability check violation."""

    code: str
    message: str
    node_id: str
    location: str = ""
    rule_id: str | None = None

    def format(self) -> str:
        """Format violation as a single-line diagnostic string."""
        prefix = f"{self.location}: {self.node_id}" if self.location else self.node_id
        rule_suffix = f" ({self.rule_id})" if self.rule_id else ""
        return f"{prefix} [{self.code}]{rule_suffix}: {self.message}"

    def to_dict(self) -> dict[str, str | None]:
        """Serialize violation for JSON output."""
        keys = ("location", "node_id", "code", "rule_id", "message")
        return {k: getattr(self, k) for k in keys}


@dataclass(frozen=True)
class Config:
    """Loaded traceability schema, statuses, rules, and discovered nodes."""

    chain: tuple[str, ...]
    statuses: dict[str, frozenset[str]]
    rules: tuple[Rule, ...]
    nodes: dict[str, Node]
    deprecated_status: str = DEFAULT_DEPRECATED_STATUS


def validate_node_id(node_id: str, scope: str) -> str:
    """Return node_id if it is a valid explicit node id, else raise ValueError."""
    if not NODE_ID_RE.match(node_id):
        raise ValueError(f"{scope}: invalid node id '{node_id}'")
    return node_id


def dedup_links(links: tuple[str, ...], scope: str) -> tuple[str, ...]:
    """Strip link targets and reject empty or duplicate entries."""
    cleaned = tuple(link.strip() for link in links)
    if any(not link for link in cleaned):
        raise ValueError(f"{scope}: empty link target in {list(links)}")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{scope}: duplicate link targets in {list(cleaned)}")
    return cleaned
