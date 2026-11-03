"""Configuration loading, node discovery, and traceability validation rules."""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from trace_check.model import (
    DEFAULT_ACTIVE_STATUS,
    DEFAULT_DEPRECATED_STATUS,
    Config,
    Node,
    Rule,
    Violation,
    dedup_links,
    validate_node_id,
)
from trace_check.source import load_source_nodes

ALLOWED_CONFIG_KEYS = frozenset({"schema", "statuses", "locations", "rules"})
ALLOWED_SCHEMA_KEYS = frozenset({"chain", "deprecated_status"})
ALLOWED_RULE_KEYS = frozenset({"id", "source_type", "when_status", "target_type", "target_status"})
ALLOWED_NODE_KEYS = frozenset({"id", "type", "status", "title", "description", "links"})


def trace(
    *targets: str,
    id: str | None = None,
    status: str = DEFAULT_ACTIVE_STATUS,
    links: Sequence[str] = (),
) -> Callable[[Any], Any]:
    """Annotate a test function with traceability links and optional node id or status."""
    return lambda func: func


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


def _parse_statuses(chain: tuple[str, ...], raw: dict[str, Any]) -> dict[str, frozenset[str]]:
    unknown = sorted(set(raw) - set(chain))
    if unknown:
        raise ValueError(f"statuses defined for unknown node types: {unknown}")
    statuses = {t: frozenset(_as_str_tuple(raw.get(t, ()), f"statuses.{t}")) for t in chain}
    missing = [t for t, allowed in statuses.items() if not allowed]
    if missing:
        raise ValueError(f"missing statuses configuration for node types: {missing}")
    return statuses


def _validate_location_pattern(node_type: str, raw_pattern: Any) -> str:
    if not isinstance(raw_pattern, str) or not raw_pattern.strip():
        raise ValueError(f"locations.{node_type} must be a non-empty string")
    pattern = raw_pattern.strip()
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        raise ValueError(f"locations.{node_type} must be a relative path pattern: '{pattern}'")
    return pattern


def _parse_locations(chain: tuple[str, ...], raw: dict[str, Any]) -> dict[str, str]:
    unknown = sorted(set(raw) - set(chain))
    if unknown:
        raise ValueError(f"locations defined for unknown node types: {unknown}")
    missing = [t for t in chain if t not in raw]
    if missing:
        raise ValueError(f"missing locations configuration for node types: {missing}")
    return {t: _validate_location_pattern(t, raw[t]) for t in chain}


def _validate_rule(
    r: Rule, statuses: dict[str, frozenset[str]], edges: set[frozenset[str]], depr: str
) -> None:
    s = f"rule '{r.id}'"
    if frozenset((r.source_type, r.target_type)) not in edges:
        raise ValueError(f"{s}: '{r.source_type}' <-> '{r.target_type}' not in schema.chain")
    if r.when_status not in statuses[r.source_type] or r.when_status == depr:
        raise ValueError(f"{s}: when_status '{r.when_status}' not in {r.source_type} statuses")
    invalid = [x for x in r.target_status if x not in statuses[r.target_type] or x == depr]
    if not r.target_status or invalid:
        raise ValueError(f"{s}: invalid target_status {list(r.target_status)} for {r.target_type}")


def _parse_rule(raw: Any) -> Rule:
    t = _require_table(raw, "rule")
    _check_allowed_keys(t, ALLOWED_RULE_KEYS, "rule")
    rid = _require_str(t, "id", "rule")
    s = f"rule '{rid}'"
    fields = (_require_str(t, k, s) for k in ("source_type", "when_status", "target_type"))
    return Rule(rid, *fields, _as_str_tuple(t.get("target_status"), f"{s} target_status"))


def _parse_rules(
    raw_rules: Any, statuses: dict[str, frozenset[str]], edges: set[frozenset[str]], depr: str
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
        _validate_rule(rule, statuses, edges, depr)
        rules.append(rule)
    return tuple(rules)


def _load_toml_node(path: Path, expected_type: str, rel_path: str) -> Node:
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{rel_path}: {error}") from error
    _check_allowed_keys(raw, ALLOWED_NODE_KEYS, rel_path)
    raw_id = _require_str(raw, "id", rel_path) if "id" in raw else path.stem
    if raw_id != path.stem:
        raise ValueError(f"{rel_path}: id '{raw_id}' does not match filename '{path.stem}'")
    node_id = validate_node_id(raw_id, rel_path)
    node_type = _require_str(raw, "type", rel_path) if "type" in raw else expected_type
    if node_type != expected_type:
        msg = f"{rel_path}: type '{node_type}' does not match location type '{expected_type}'"
        raise ValueError(msg)
    status = _require_str(raw, "status", rel_path)
    links_scope = f"{rel_path}: links"
    links = dedup_links(_as_str_tuple(raw.get("links", ()), links_scope), links_scope)
    return Node(node_id, node_type, status, links, rel_path)


def _discover_file_nodes(
    path: Path, node_type: str, base_dir: Path, discovered: dict[str, Node], errors: list[str]
) -> None:
    rel_path = path.relative_to(base_dir).as_posix()
    try:
        file_nodes = (
            [_load_toml_node(path, node_type, rel_path)]
            if path.suffix == ".toml"
            else load_source_nodes(path, node_type, rel_path)
        )
    except ValueError as error:
        errors.append(str(error))
        return
    for node in file_nodes:
        if node.id in discovered:
            first = discovered[node.id].location
            errors.append(f"{rel_path}: duplicate node id '{node.id}' (first defined at {first})")
        else:
            discovered[node.id] = node


def _discover_nodes(base_dir: Path, locations: dict[str, str]) -> dict[str, Node]:
    discovered: dict[str, Node] = {}
    errors: list[str] = []
    for node_type, pattern in locations.items():
        for file_path in sorted(base_dir.glob(pattern)):
            if file_path.is_file():
                _discover_file_nodes(file_path, node_type, base_dir, discovered, errors)
    if errors:
        raise ValueError("; ".join(errors))
    if not discovered:
        raise ValueError("no traceability nodes discovered for configured locations")
    return discovered


def _resolve_base_dir(path: Path) -> Path:
    parent = path.parent.resolve()
    return (
        parent.parent
        if parent.name == ".policy"  # glossary: ignore[policy]
        else path.parent
    )


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
    depr = _parse_deprecated_status(schema, statuses)
    rules = _parse_rules(raw_config.get("rules", ()), statuses, edges, depr)
    return Config(chain, statuses, rules, _discover_nodes(_resolve_base_dir(path), locations), depr)


def build_adjacency(nodes: dict[str, Node], edges: set[frozenset[str]]) -> dict[str, set[str]]:
    """Normalize declared links into an undirected adjacency map of valid existing nodes."""
    adj: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for node in nodes.values():
        for target_id in node.links:
            target = nodes.get(target_id)
            if target and frozenset((node.type, target.type)) in edges:
                adj[node.id].add(target_id)
                adj[target_id].add(node.id)
    return adj


def _check_node_schema(node: Node, statuses: dict[str, frozenset[str]]) -> list[Violation]:
    allowed = statuses.get(node.type)
    if allowed is None:
        msg = f"unknown type '{node.type}'"
        return [Violation("ERR_UNKNOWN_TYPE", msg, node.id, node.location)]
    if node.status not in allowed:
        msg = f"invalid status '{node.status}' for {node.type}"
        return [Violation("ERR_INVALID_STATUS", msg, node.id, node.location)]
    return []


def _check_declared_links(
    node: Node, nodes: dict[str, Node], edges: set[frozenset[str]], depr: str
) -> list[Violation]:
    violations: list[Violation] = []
    for tid in node.links:
        target = nodes.get(tid)
        if target is None:
            msg = f"target '{tid}' does not exist"
            violations.append(Violation("ERR_DANGLING_LINK", msg, node.id, node.location))
        elif frozenset((node.type, target.type)) not in edges:
            msg = f"cannot link {node.type} to {target.type} ('{tid}')"
            violations.append(Violation("ERR_INVALID_EDGE", msg, node.id, node.location))
        elif node.status != depr and target.status == depr:
            msg = f"active {node.type} links to {depr} {target.type} '{tid}'"
            violations.append(Violation("ERR_DEPRECATED_LINK", msg, node.id, node.location))
    return violations


def _check_rule_for_node(
    node: Node, rule: Rule, neighbors: set[str], nodes: dict[str, Node], depr: str
) -> list[Violation]:
    if node.status == depr or (node.type, node.status) != (rule.source_type, rule.when_status):
        return []
    targets = [
        nodes[tid]
        for tid in sorted(neighbors)
        if nodes[tid].type == rule.target_type and nodes[tid].status != depr
    ]
    if not targets:
        msg = f"requires >= 1 active {rule.target_type}"
        return [Violation("ERR_MISSING_LINK", msg, node.id, node.location, rule.id)]
    expected = list(rule.target_status)
    return [
        Violation(
            "ERR_TARGET_STATUS",
            f"linked {t.type} '{t.id}' is '{t.status}', expected {expected}",
            node.id,
            node.location,
            rule.id,
        )
        for t in targets
        if t.status not in rule.target_status
    ]


def check_violations(config: Config) -> list[Violation]:
    """Validate all nodes, links, deprecations, and rules; return sorted Violation objects."""
    edges = allowed_edges(config.chain)
    adj = build_adjacency(config.nodes, edges)
    depr = config.deprecated_status
    violations: list[Violation] = []

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


def check_config(config: Config) -> list[str]:
    """Validate all nodes, links, deprecations, and rules; return sorted violation lines."""
    return [violation.format() for violation in check_violations(config)]
