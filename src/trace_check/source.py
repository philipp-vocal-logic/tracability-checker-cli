"""Static discovery of @trace-annotated test functions in Python, C, C++, and Rust files."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trace_check.model import DEFAULT_ACTIVE_STATUS, Node, dedup_links, validate_node_id

TRACE_MODULES = frozenset({"trace_check", "trace_check.checker"})
COMMENT_EXTS = frozenset({".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".rs"})
COMMENT_TRACE_RE = re.compile(r"(?://|/\*+)\s*@(trace\b[^\n*]*?)(?:\*/|\s*$)", re.MULTILINE)
TEST_DECL_RE = re.compile(
    r"\b(?:TEST(?:_F|_P)?\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)"
    r"|fn\s+([A-Za-z_]\w*)\s*\("
    r"|(?:void|int|bool)\s+(test_[A-Za-z0-9_]*)\s*\()"
)

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


def _parse_comment_node(
    match: re.Match[str], next_start: int, text: str, node_type: str, rel_path: str
) -> tuple[str, Node]:
    lineno = text.count("\n", 0, match.start()) + 1
    scope = f"{rel_path}:{lineno}"
    decl = TEST_DECL_RE.search(text, match.end(), next_start)
    if decl is None:
        raise ValueError(f"{scope}: @trace must precede a test function")
    name = f"{decl.group(1)}_{decl.group(2)}" if decl.group(1) else decl.group(3) or decl.group(4)
    try:
        call = ast.parse(match.group(1).strip(), mode="eval").body
    except SyntaxError as error:
        raise ValueError(f"{scope}: malformed @trace decorator: {error.msg}") from error
    call.lineno = lineno
    return name, _parse_trace_call(call, f"{rel_path}::{name}", node_type, rel_path)


def _load_comment_nodes(text: str, node_type: str, rel_path: str) -> list[Node]:
    matches = list(COMMENT_TRACE_RE.finditer(text))
    nodes, errors, seen = [], [], set()
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        try:
            name, node = _parse_comment_node(match, next_start, text, node_type, rel_path)
            if name in seen:
                raise ValueError(f"{node.location}: duplicate @trace decorator on '{name}'")
            seen.add(name)
            nodes.append(node)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("; ".join(errors))
    return nodes


def _load_python_nodes(text: str, node_type: str, rel_path: str) -> list[Node]:
    tree = ast.parse(text, filename=rel_path)
    names, nodes, errors = collect_trace_names(tree), [], []
    for stmt, qualname in _iter_decorated(tree, ()):
        try:
            if node := _check_decorated_stmt(stmt, qualname, names, node_type, rel_path):
                nodes.append(node)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("; ".join(errors))
    return nodes


def load_source_nodes(path: Path, node_type: str, rel_path: str) -> list[Node]:
    """Discover @trace-annotated test functions in a source file as traceability nodes."""
    try:
        text = path.read_text(encoding="utf-8-sig")
        loader = _load_comment_nodes if path.suffix.lower() in COMMENT_EXTS else _load_python_nodes
        return loader(text, node_type, rel_path)
    except (UnicodeDecodeError, SyntaxError) as error:
        raise ValueError(f"{rel_path}: {error}") from error
