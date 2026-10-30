"""Atomic tests for static @trace discovery: qualified ids, import resolution, and encodings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_check import trace
from trace_check.checker import load_config
from trace_check.cli import main
from trace_check.model import Node
from trace_check.source import load_source_nodes

CONFIG_TOML = (
    '[schema]\nchain = ["Requirement", "TestCase"]\n'
    '[statuses]\nRequirement = ["APPROVED", "DEPRECATED"]\n'
    'TestCase = ["APPROVED", "DEPRECATED"]\n'
    '[locations]\nRequirement = "reqs/*.toml"\nTestCase = "tests/**/test_*.py"\n'
    '[[rules]]\nid = "REQ-TEST"\nsource_type = "Requirement"\n'
    'when_status = "APPROVED"\ntarget_type = "TestCase"\ntarget_status = ["APPROVED"]\n'
)


def make_project(root: Path, sources: dict[str, str | bytes]) -> Path:
    """Write a minimal project with one approved requirement and the given test sources."""
    (root / "reqs").mkdir()
    (root / "reqs" / "REQ-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    for rel_path, content in sources.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    cfg = root / "traceability.toml"
    cfg.write_text(CONFIG_TOML, encoding="utf-8")
    return cfg


def source_nodes(tmp_path: Path, source: str) -> list[Node]:
    """Discover TestCase nodes from a single Python source string."""
    path = tmp_path / "test_sample.py"
    path.write_text(source, encoding="utf-8")
    return load_source_nodes(path, "TestCase", "tests/test_sample.py")


@trace("REQ-SCHEMA")
def test_same_function_name_in_different_files_and_classes_gets_qualified_ids(
    tmp_path: Path,
) -> None:
    body = "from trace_check import trace\n@trace('REQ-1')\ndef test_load(): ...\n"
    nested = (
        "from trace_check import trace\n"
        "class TestA:\n    @trace('REQ-1')\n    def test_load(self): ...\n"
        "class TestB:\n    @trace('REQ-1')\n    def test_load(self): ...\n"
    )
    cfg = make_project(
        tmp_path,
        {"tests/test_a.py": body, "tests/sub/test_b.py": body, "tests/test_c.py": nested},
    )
    assert sorted(n for n in load_config(cfg).nodes if "::" in n) == [
        "tests/sub/test_b.py::test_load",
        "tests/test_a.py::test_load",
        "tests/test_c.py::TestA::test_load",
        "tests/test_c.py::TestB::test_load",
    ]


@trace("REQ-SCHEMA")
def test_explicit_id_overrides_qualified_default_and_is_validated(tmp_path: Path) -> None:
    source = "from trace_check import trace\n@trace('REQ-1', id='TC-1')\ndef test_a(): ...\n"
    assert [n.id for n in source_nodes(tmp_path, source)] == ["TC-1"]
    bad = "from trace_check import trace\n@trace('REQ-1', id='a::b')\ndef test_a(): ...\n"
    with pytest.raises(ValueError, match="invalid node id 'a::b'"):
        source_nodes(tmp_path, bad)


@trace("REQ-SCHEMA")
@pytest.mark.parametrize(
    ("imports", "decorator"),
    [
        pytest.param("from trace_check import trace", "trace", id="from_package"),
        pytest.param("from trace_check.checker import trace", "trace", id="from_checker"),
        pytest.param("from trace_check import trace as tr", "tr", id="from_alias"),
        pytest.param("from trace_check import *", "trace", id="star_import"),
        pytest.param("import trace_check", "trace_check.trace", id="module_attr"),
        pytest.param("import trace_check as tc", "tc.trace", id="module_alias_attr"),
        pytest.param(
            "import trace_check.checker", "trace_check.checker.trace", id="submodule_attr"
        ),
        pytest.param("from trace_check import checker as ck", "ck.trace", id="submodule_alias"),
    ],
)
def test_trace_decorator_is_resolved_through_imports(
    tmp_path: Path, imports: str, decorator: str
) -> None:
    source = f"{imports}\n@{decorator}('REQ-1')\ndef test_a(): ...\n"
    assert source_nodes(tmp_path, source) == [
        Node(
            "tests/test_sample.py::test_a",
            "TestCase",
            "APPROVED",
            ("REQ-1",),
            "tests/test_sample.py:2",
        )
    ]


@trace("REQ-SCHEMA")
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import pytest\n@pytest.mark.trace\ndef test_a(): ...\n", id="pytest_mark_trace"
        ),
        pytest.param(
            "from opentelemetry import trace\n@trace.get_tracer('x')\ndef helper(): ...\n",
            id="foreign_trace_module",
        ),
        pytest.param("@trace('REQ-1')\ndef sample_fn(): ...\n", id="unimported_trace_name"),
        pytest.param(
            "from other_lib import trace\n@trace('REQ-1')\nclass Suite: ...\n",
            id="foreign_trace_function",
        ),
    ],
)
def test_foreign_trace_decorators_are_ignored(tmp_path: Path, source: str) -> None:
    assert source_nodes(tmp_path, source) == []


@trace("REQ-ZERO-NOISE")
def test_utf8_bom_source_file_is_parsed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = "from trace_check import trace\n@trace('REQ-1')\ndef test_a(): ...\n"
    cfg = make_project(tmp_path, {"tests/test_bom.py": b"\xef\xbb\xbf" + source.encode()})
    assert main(["--format", "json", str(cfg)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "violations": []}


@trace("REQ-ZERO-NOISE")
def test_duplicate_node_id_reports_first_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = "from trace_check import trace\n@trace('REQ-1', id='TC-1')\ndef test_a(): ...\n"
    cfg = make_project(tmp_path, {"tests/test_a.py": source, "tests/test_b.py": source})
    assert main([str(cfg)]) == 2
    assert (
        "tests/test_b.py: duplicate node id 'TC-1' (first defined at tests/test_a.py:2)"
        in capsys.readouterr().err
    )
