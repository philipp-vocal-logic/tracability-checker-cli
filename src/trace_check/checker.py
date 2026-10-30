"""Core data structures, git-friendly node discovery, and validation rules."""

from __future__ import annotations

import io
import re
import tokenize
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DEPRECATED_STATUS = "DEPRECATED"
TRACE_COMMENT_RE = re.compile(r"^\s*(?:#|//)\s*@trace\b")
TRACE_DIRECTIVE_RE = re.compile(
    r"^\s*(?:#|//)\s*@trace\s+(?P<id>[A-Za-z0-9_-]+)\s+(?P<status>[A-Za-z0-9_-]+)"
    r"(?:\s+\[(?P<links>[^\]]*)\])?\s*$"
)


@dataclass(frozen=True)
class Node:
    """A single traceability node in the project graph."""

    id: str
    type: str
    status: str
    links: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rule:
    """A declarative status and link coverage constraint."""

    id: str
    source_type: str
    when_status: str
    target_type: str
    target_status: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    """Loaded traceability schema, statuses, rules, and discovered nodes."""

    chain: tuple[str, ...]
    statuses: dict[str, frozenset[str]]
    rules: tuple[Rule, ...]
    nodes: dict[str, Node]
    deprecated_status: str = DEFAULT_DEPRECATED_STATUS


ALLOWED_CONFIG_KEYS = frozenset({"schema", "statuses", "locations", "rules"})
ALLOWED_SCHEMA_KEYS = frozenset({"chain", "deprecated_status"})
ALLOWED_RULE_KEYS = frozenset({"id", "source_type", "when_status", "target_type", "target_status"})
ALLOWED_NODE_KEYS = frozenset({"id", "type", "status", "title", "description", "links"})


def _require_table(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise ValueError(f"'{name}' must be a table")


def _check_allowed_keys(table: dict[str, Any], allowed: frozenset[str], scope: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown keys in {scope}: {unknown}")


def _require_str(table: dict[str, Any], field: str, scope: str) -> str:
    value = table.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{scope}: missing or invalid '{field}'")


def _as_str_tuple(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value):
        return tuple(value)
    raise ValueError(f"'{field}' must be a list of strings")


def _parse_chain(schema: dict[str, Any]) -> tuple[str, ...]:
    _check_allowed_keys(schema, ALLOWED_SCHEMA_KEYS, "schema")
    chain = _as_str_tuple(schema.get("chain", ()), "schema.chain")
    if len(chain) < 2:
        raise ValueError("schema.chain must contain at least 2 node types")
    if len(set(chain)) != len(chain):
        raise ValueError("schema.chain must not contain duplicate node types")
    return chain


def _parse_deprecated_status(schema: dict[str, Any], statuses: dict[str, frozenset[str]]) -> str:
    if "deprecated_status" not in schema:
        return DEFAULT_DEPRECATED_STATUS
    depr = _require_str(schema, "deprecated_status", "schema")
    if not any(depr in allowed for allowed in statuses.values()):
        raise ValueError(f"deprecated_status '{depr}' not found in any configured statuses")
    return depr


def allowed_edges(chain: tuple[str, ...]) -> set[frozenset[str]]:
    """Build set of allowed adjacent bidirectional type pairs from schema chain."""
    return {frozenset((chain[i], chain[i + 1])) for i in range(len(chain) - 1)}


def _parse_statuses(
    chain: tuple[str, ...], raw_statuses: dict[str, Any]
) -> dict[str, frozenset[str]]:
    unknown = sorted(set(raw_statuses) - set(chain))
    if unknown:
        raise ValueError(f"statuses defined for unknown node types: {unknown}")
    statuses = {
        t: frozenset(_as_str_tuple(raw_statuses.get(t, ()), f"statuses.{t}")) for t in chain
    }
    missing = [t for t, allowed in statuses.items() if not allowed]
    if missing:
        raise ValueError(f"missing statuses configuration for node types: {missing}")
    return statuses


def _validate_location_pattern(node_type: str, raw_pattern: Any) -> str:
    if not isinstance(raw_pattern, str) or not raw_pattern.strip():
        return ""
    pattern = raw_pattern.strip()
    if Path(pattern).is_absolute():
        raise ValueError(f"locations.{node_type} must be a relative path pattern: '{pattern}'")
    return pattern


def _parse_locations(chain: tuple[str, ...], raw_locations: dict[str, Any]) -> dict[str, str]:
    unknown = sorted(set(raw_locations) - set(chain))
    if unknown:
        raise ValueError(f"locations defined for unknown node types: {unknown}")
    locations = {t: _validate_location_pattern(t, raw_locations.get(t)) for t in chain}
    missing = [t for t, pattern in locations.items() if not pattern]
    if missing:
        raise ValueError(f"missing locations configuration for node types: {missing}")
    return locations


def _validate_rule(
    rule: Rule,
    statuses: dict[str, frozenset[str]],
    edges: set[frozenset[str]],
    deprecated_status: str = DEFAULT_DEPRECATED_STATUS,
) -> None:
    if frozenset((rule.source_type, rule.target_type)) not in edges:
        raise ValueError(
            f"rule '{rule.id}': '{rule.source_type}' <-> '{rule.target_type}' not in schema.chain"
        )
    if rule.when_status not in statuses[rule.source_type] or rule.when_status == deprecated_status:
        raise ValueError(
            f"rule '{rule.id}': when_status '{rule.when_status}' not in {rule.source_type} statuses"
        )
    invalid = [
        s
        for s in rule.target_status
        if s not in statuses[rule.target_type] or s == deprecated_status
    ]
    if not rule.target_status or invalid:
        raise ValueError(
            f"rule '{rule.id}': invalid target_status {list(rule.target_status)} "
            f"for {rule.target_type}"
        )


def _parse_rule(raw: Any) -> Rule:
    table = _require_table(raw, "rule")
    _check_allowed_keys(table, ALLOWED_RULE_KEYS, "rule")
    rule_id = _require_str(table, "id", "rule")
    return Rule(
        id=rule_id,
        source_type=_require_str(table, "source_type", f"rule '{rule_id}'"),
        when_status=_require_str(table, "when_status", f"rule '{rule_id}'"),
        target_type=_require_str(table, "target_type", f"rule '{rule_id}'"),
        target_status=_as_str_tuple(table.get("target_status"), f"rule '{rule_id}' target_status"),
    )


def _parse_rules(
    raw_rules: Any,
    statuses: dict[str, frozenset[str]],
    edges: set[frozenset[str]],
    deprecated_status: str,
) -> tuple[Rule, ...]:
    if not isinstance(raw_rules, (list, tuple)):
        raise ValueError("'rules' must be a list of tables")
    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for raw in raw_rules:
        rule = _parse_rule(raw)
        if rule.id in seen_ids:
            raise ValueError(f"duplicate rule id '{rule.id}'")
        seen_ids.add(rule.id)
        _validate_rule(rule, statuses, edges, deprecated_status)
        rules.append(rule)
    return tuple(rules)


def _load_toml_node(path: Path, expected_type: str) -> Node:
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: {error}") from error
    _check_allowed_keys(raw, ALLOWED_NODE_KEYS, str(path))
    node_id = _require_str(raw, "id", str(path)) if "id" in raw else path.stem
    if node_id != path.stem:
        raise ValueError(f"{path}: id '{node_id}' does not match filename '{path.stem}'")
    node_type = _require_str(raw, "type", str(path)) if "type" in raw else expected_type
    if node_type != expected_type:
        raise ValueError(
            f"{path}: type '{node_type}' does not match location type '{expected_type}'"
        )
    status = _require_str(raw, "status", str(path))
    links = _as_str_tuple(raw.get("links", ()), f"{path}: links")
    return Node(id=node_id, type=node_type, status=status, links=links)


def _parse_trace_links(raw_links: str | None) -> tuple[str, ...]:
    if not raw_links:
        return ()
    parts = (part.strip().strip("\"'") for part in raw_links.split(","))
    return tuple(p for p in parts if p)


def _iter_comment_lines(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix != ".py":
            return list(enumerate(text.splitlines(), start=1))
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return [(tok.start[0], tok.string) for tok in tokens if tok.type == tokenize.COMMENT]
    except (UnicodeDecodeError, tokenize.TokenError, IndentationError, SyntaxError) as error:
        raise ValueError(f"{path}: {error}") from error


def _load_source_nodes(path: Path, node_type: str) -> list[Node]:
    nodes: list[Node] = []
    for lineno, line in _iter_comment_lines(path):
        if not TRACE_COMMENT_RE.match(line):
            continue
        match = TRACE_DIRECTIVE_RE.match(line)
        if match is None:
            raise ValueError(f"{path}:{lineno}: malformed @trace directive: {line.strip()}")
        nodes.append(
            Node(
                id=match.group("id"),
                type=node_type,
                status=match.group("status"),
                links=_parse_trace_links(match.group("links")),
            )
        )
    return nodes


def _nodes_from_file(path: Path, node_type: str) -> list[Node]:
    if path.suffix == ".toml":
        return [_load_toml_node(path, node_type)]
    return _load_source_nodes(path, node_type)


def _iter_location_files(base_dir: Path, pattern: str) -> list[Path]:
    try:
        return [p for p in sorted(base_dir.glob(pattern)) if p.is_file()]
    except (NotImplementedError, ValueError) as error:
        raise ValueError(f"invalid location pattern '{pattern}': {error}") from error


def _discover_nodes(base_dir: Path, locations: dict[str, str]) -> dict[str, Node]:
    discovered: dict[str, Node] = {}
    for node_type, pattern in locations.items():
        for file_path in _iter_location_files(base_dir, pattern):
            for node in _nodes_from_file(file_path, node_type):
                if node.id in discovered:
                    raise ValueError(f"duplicate node id '{node.id}' found in {file_path}")
                discovered[node.id] = node
    if not discovered:
        raise ValueError("no traceability nodes discovered for configured locations")
    return discovered


def load_config(path: Path) -> Config:
    """Load and self-validate a TOML traceability configuration and its nodes."""
    with path.open("rb") as f:
        raw_config = tomllib.load(f)

    _check_allowed_keys(raw_config, ALLOWED_CONFIG_KEYS, "config")
    schema = _require_table(raw_config.get("schema", {}), "schema")
    chain = _parse_chain(schema)
    statuses = _parse_statuses(chain, _require_table(raw_config.get("statuses", {}), "statuses"))
    locations = _parse_locations(
        chain, _require_table(raw_config.get("locations", {}), "locations")
    )
    edges = allowed_edges(chain)
    deprecated_status = _parse_deprecated_status(schema, statuses)
    rules = _parse_rules(raw_config.get("rules", ()), statuses, edges, deprecated_status)
    nodes = _discover_nodes(path.parent, locations)
    return Config(
        chain=chain,
        statuses=statuses,
        rules=rules,
        nodes=nodes,
        deprecated_status=deprecated_status,
    )


def build_adjacency(
    nodes: dict[str, Node], edges: set[frozenset[str]] | None = None
) -> dict[str, set[str]]:
    """Normalize declared links into an undirected adjacency map of valid existing nodes."""
    adj: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for node in nodes.values():
        for target_id in node.links:
            target = nodes.get(target_id)
            if target and (edges is None or frozenset((node.type, target.type)) in edges):
                adj[node.id].add(target_id)
                adj[target_id].add(node.id)
    return adj


def _check_node_schema(node: Node, statuses: dict[str, frozenset[str]]) -> list[str]:
    allowed = statuses.get(node.type)
    if allowed is None:
        return [f"{node.id} [ERR_UNKNOWN_TYPE]: unknown type '{node.type}'"]
    if node.status not in allowed:
        return [f"{node.id} [ERR_INVALID_STATUS]: invalid status '{node.status}' for {node.type}"]
    return []


def _check_declared_links(
    node: Node, nodes: dict[str, Node], edges: set[frozenset[str]], deprecated_status: str
) -> list[str]:
    if node.status == deprecated_status:
        return []
    errors: list[str] = []
    for target_id in node.links:
        target = nodes.get(target_id)
        if target is None:
            errors.append(f"{node.id} [ERR_DANGLING_LINK]: target '{target_id}' does not exist")
        elif frozenset((node.type, target.type)) not in edges:
            msg = f"cannot link {node.type} to {target.type} ('{target_id}')"
            errors.append(f"{node.id} [ERR_INVALID_EDGE]: {msg}")
        elif target.status == deprecated_status:
            msg = f"active {node.type} links to {deprecated_status} {target.type} '{target_id}'"
            errors.append(f"{node.id} [ERR_DEPRECATED_LINK]: {msg}")
    return errors


def _check_rule_for_node(
    node: Node,
    rule: Rule,
    neighbors: set[str],
    nodes: dict[str, Node],
    deprecated_status: str,
) -> list[str]:
    if node.status == deprecated_status or (node.type, node.status) != (
        rule.source_type,
        rule.when_status,
    ):
        return []
    targets = [
        nodes[tid]
        for tid in sorted(neighbors)
        if nodes[tid].type == rule.target_type and nodes[tid].status != deprecated_status
    ]
    if not targets:
        return [
            f"{node.id} [ERR_MISSING_LINK] ({rule.id}): requires >= 1 active {rule.target_type}"
        ]
    expected = list(rule.target_status)
    return [
        f"{node.id} [ERR_TARGET_STATUS] ({rule.id}): "
        f"linked {t.type} '{t.id}' is '{t.status}', expected {expected}"
        for t in targets
        if t.status not in rule.target_status
    ]


def check_config(config: Config) -> list[str]:
    """Validate all nodes, links, deprecations, and rules; return sorted violation lines."""
    edges = allowed_edges(config.chain)
    adj = build_adjacency(config.nodes, edges)
    depr = config.deprecated_status
    violations: list[str] = []

    for node_id in sorted(config.nodes):
        node = config.nodes[node_id]
        schema_errors = _check_node_schema(node, config.statuses)
        violations.extend(schema_errors)
        if schema_errors:
            continue
        violations.extend(_check_declared_links(node, config.nodes, edges, depr))
        for rule in config.rules:
            violations.extend(_check_rule_for_node(node, rule, adj[node_id], config.nodes, depr))

    return violations
