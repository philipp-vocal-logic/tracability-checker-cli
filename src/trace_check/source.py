"""Static AST discovery of @trace-decorated test functions in Python source files."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trace_check.model import DEFAULT_ACTIVE_STATUS, Node, dedup_links, validate_node_id

TRACE_MODULES = frozenset({"trace_check", "trace_check.checker"})

DecoratedStmt = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)
class TraceNames:
    """Local names under which a module references the trace_check.trace decorator."""

    direct: frozenset[str]
    prefixes: frozenset[str]

    def matches(self, expr: ast.expr) -> bool:
        """Return True if a decorator expression resolves to trace_check.trace."""
        target = expr.func if isinstance(expr, ast.Call) else expr
        if isinstance(target, ast.Name):
            return target.id in self.direct
        if isinstance(target, ast.Attribute) and target.attr == "trace":
            return _dotted_name(target.value) in self.prefixes
        return False


def _dotted_name(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted_name(expr.value)
        return f"{base}.{expr.attr}" if base else None
    return None


def _import_names(stmt: ast.Import) -> Iterator[tuple[str, str]]:
    for alias in stmt.names:
        if alias.name not in TRACE_MODULES:
            continue
        yield "prefix", alias.asname or alias.name
        if alias.asname is None:
            yield "prefix", "trace_check"


def _import_from_names(stmt: ast.ImportFrom) -> Iterator[tuple[str, str]]:
    if stmt.level or stmt.module not in TRACE_MODULES:
        return
    for alias in stmt.names:
        if alias.name in {"trace", "*"}:
            yield "direct", alias.asname or "trace"
        elif stmt.module == "trace_check" and alias.name == "checker":
            yield "prefix", alias.asname or "checker"


def collect_trace_names(tree: ast.Module) -> TraceNames:
    """Resolve the names bound to trace_check.trace by the module's import statements."""
    pairs: list[tuple[str, str]] = []
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            pairs.extend(_import_names(stmt))
        elif isinstance(stmt, ast.ImportFrom):
            pairs.extend(_import_from_names(stmt))
    return TraceNames(
        frozenset(name for kind, name in pairs if kind == "direct"),
        frozenset(name for kind, name in pairs if kind == "prefix"),
    )


def _iter_decorated(
    parent: ast.AST, scope: tuple[str, ...]
) -> Iterator[tuple[DecoratedStmt, tuple[str, ...]]]:
    for child in ast.iter_child_nodes(parent):
        child_scope = scope
        if isinstance(child, DecoratedStmt):
            child_scope = (*scope, child.name)
            yield child, child_scope
        yield from _iter_decorated(child, child_scope)


def _parse_str_constant(expr: ast.expr, scope: str, label: str) -> str:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and expr.value.strip():
        return expr.value.strip()
    raise ValueError(f"{scope}: malformed @trace decorator: {label} must be a non-empty string")


def _parse_trace_kw_value(kw: ast.keyword, scope: str) -> Any:
    if kw.arg == "links":
        if not isinstance(kw.value, (ast.List, ast.Tuple)):
            raise ValueError(f"{scope}: malformed @trace decorator: 'links' must be a list")
        return tuple(_parse_str_constant(elt, scope, "link") for elt in kw.value.elts)
    if kw.arg in {"id", "status"}:
        return _parse_str_constant(kw.value, scope, f"'{kw.arg}'")
    raise ValueError(f"{scope}: malformed @trace decorator: unknown keyword '{kw.arg}'")


def _parse_trace_call(call: ast.expr, default_id: str, node_type: str, rel_path: str) -> Node:
    scope = f"{rel_path}:{call.lineno}"
    if not isinstance(call, ast.Call):
        raise ValueError(f"{scope}: malformed @trace decorator: expected @trace(...)")
    arg_links = tuple(_parse_str_constant(arg, scope, "link") for arg in call.args)
    kw_map = {kw.arg: _parse_trace_kw_value(kw, scope) for kw in call.keywords}
    if arg_links and "links" in kw_map:
        raise ValueError(f"{scope}: malformed @trace decorator: duplicate links specification")
    raw_links = arg_links or kw_map.get("links", ())
    node_id = validate_node_id(kw_map["id"], scope) if "id" in kw_map else default_id
    status = kw_map.get("status", DEFAULT_ACTIVE_STATUS)
    return Node(node_id, node_type, status, dedup_links(raw_links, scope), scope)


def _check_decorated_stmt(
    stmt: DecoratedStmt, qualname: tuple[str, ...], names: TraceNames, node_type: str, rel_path: str
) -> Node | None:
    trace_decs = list(filter(names.matches, stmt.decorator_list))
    if not trace_decs:
        return None
    scope = f"{rel_path}:{trace_decs[0].lineno}"
    if isinstance(stmt, ast.ClassDef) or not stmt.name.startswith("test_"):
        raise ValueError(f"{scope}: @trace must decorate a test_* function, got '{stmt.name}'")
    if len(trace_decs) > 1:
        raise ValueError(f"{scope}: duplicate @trace decorator on '{stmt.name}'")
    default_id = "::".join((rel_path, *qualname))
    return _parse_trace_call(trace_decs[0], default_id, node_type, rel_path)


def load_source_nodes(path: Path, node_type: str, rel_path: str) -> list[Node]:
    """Discover @trace-decorated test functions in a Python file as traceability nodes."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel_path)
    except (UnicodeDecodeError, SyntaxError) as error:
        raise ValueError(f"{rel_path}: {error}") from error
    names = collect_trace_names(tree)
    nodes: list[Node] = []
    errors: list[str] = []
    for stmt, qualname in _iter_decorated(tree, ()):
        try:
            if node := _check_decorated_stmt(stmt, qualname, names, node_type, rel_path):
                nodes.append(node)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("; ".join(errors))
    return nodes
