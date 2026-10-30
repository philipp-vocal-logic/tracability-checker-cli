"""Self-checks verifying repository compliance with glossary, symbol, and architecture rules."""

from __future__ import annotations

from pathlib import Path

import archcheck
from glossary_checker_cli.checker import check
from glossary_checker_cli.core import load_glossary
from symbol_checker.checker import check_project
from symbol_checker.schema import load_schema

from trace_check import trace

REPO_ROOT = Path(__file__).resolve().parent.parent


@trace("REQ-SCHEMA")
def test_project_adheres_to_own_glossary() -> None:
    glossary = load_glossary(REPO_ROOT / "glossary.toml")
    assert check(glossary) == []


@trace("REQ-SCHEMA")
def test_project_adheres_to_own_symbols() -> None:
    schema = load_schema(REPO_ROOT / "symbols.toml")
    assert check_project(schema, root=REPO_ROOT) == []


@trace("REQ-SCHEMA")
def test_project_adheres_to_own_architecture() -> None:
    assert archcheck.main(["--config", str(REPO_ROOT / "arch.toml")]) == 0
