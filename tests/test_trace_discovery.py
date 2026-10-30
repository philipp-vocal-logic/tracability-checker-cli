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


@trace("REQ-SCHEMA")
@pytest.mark.parametrize(
    ("filename", "source", "expected_id", "expected_status", "expected_links", "line"),
    [
        pytest.param(
            "test_calc.c",
            '/* @trace("REQ-1") */\nvoid test_multiply(void) {\n}\n',
            "tests/test_calc.c::test_multiply",
            "APPROVED",
            ("REQ-1",),
            1,
            id="ansi_c_block_comment_good",
        ),
        pytest.param(
            "test_calc.c",
            '/** @trace("REQ-1", id="TC-C89", status="DRAFT") */\n'
            "int test_add(void) { return 0; }\n",
            "TC-C89",
            "DRAFT",
            ("REQ-1",),
            1,
            id="ansi_c_doc_comment_explicit_id_and_status",
        ),
        pytest.param(
            "test_calc.cpp",
            '// @trace("REQ-1")\nTEST(CalcSuite, MultiplyCase) {\n}\n',
            "tests/test_calc.cpp::CalcSuite_MultiplyCase",
            "APPROVED",
            ("REQ-1",),
            1,
            id="cpp_gtest_macro_good",
        ),
        pytest.param(
            "test_calc.hpp",
            '/* @trace(links=["REQ-1", "REQ-2"]) */\nTEST_F(FixtureSuite, SubCase) {}\n',
            "tests/test_calc.hpp::FixtureSuite_SubCase",
            "APPROVED",
            ("REQ-1", "REQ-2"),
            1,
            id="cpp_gtest_fixture_links_kw",
        ),
        pytest.param(
            "test_calc.rs",
            '// @trace("REQ-1")\n#[test]\nfn multiply_works() {\n}\n',
            "tests/test_calc.rs::multiply_works",
            "APPROVED",
            ("REQ-1",),
            1,
            id="rust_test_attribute_good",
        ),
        pytest.param(
            "test_calc.rs",
            '/* @trace("REQ-1") */\n#[tokio::test]\nasync fn test_async_op() {}\n',
            "tests/test_calc.rs::test_async_op",
            "APPROVED",
            ("REQ-1",),
            1,
            id="rust_async_block_comment_edge",
        ),
    ],
)
def test_c_cpp_rust_comment_trace_discovery_good_and_edge_cases(
    tmp_path: Path,
    filename: str,
    source: str,
    expected_id: str,
    expected_status: str,
    expected_links: tuple[str, ...],
    line: int,
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    rel_path = f"tests/{filename}"
    assert load_source_nodes(path, "TestCase", rel_path) == [
        Node(expected_id, "TestCase", expected_status, expected_links, f"{rel_path}:{line}")
    ]


@trace("REQ-SCHEMA")
@pytest.mark.parametrize(
    ("filename", "source", "expected_error"),
    [
        pytest.param(
            "test_bad.c",
            '/* @trace("REQ-1") */\nvoid helper_fn(void) {}\n',
            "tests/test_bad.c:1: @trace must precede a test function",
            id="c_non_test_function_failure",
        ),
        pytest.param(
            "test_bad.rs",
            '// @trace("REQ-1")\nstruct OnlyStruct;\n',
            "tests/test_bad.rs:1: @trace must precede a test function",
            id="rust_missing_function_failure",
        ),
        pytest.param(
            "test_bad.c",
            '/* @trace("REQ-1") */\n/* @trace("REQ-1") */\nvoid test_dup(void) {}\n',
            "tests/test_bad.c:1: @trace must precede a test function",
            id="c_consecutive_trace_without_fn_failure",
        ),
        pytest.param(
            "test_bad.cpp",
            '// @trace("REQ-1"\nTEST(Suite, Case) {}\n',
            "tests/test_bad.cpp:1: malformed @trace decorator",
            id="cpp_unclosed_syntax_failure",
        ),
        pytest.param(
            "test_bad.rs",
            "// @trace\n#[test]\nfn sample() {}\n",
            "tests/test_bad.rs:1: malformed @trace decorator: expected @trace(...)",
            id="rust_bare_trace_without_call_failure",
        ),
        pytest.param(
            "test_bad.c",
            "/* @trace(123) */\nvoid test_num(void) {}\n",
            "tests/test_bad.c:1: malformed @trace decorator: link must be a non-empty string",
            id="c_non_string_link_failure",
        ),
        pytest.param(
            "test_bad.c",
            '/* @trace("REQ-1") */\nvoid test_same(void);\n'
            '/* @trace("REQ-1") */\nvoid test_same(void) {}\n',
            "tests/test_bad.c:3: duplicate @trace decorator on 'test_same'",
            id="c_duplicate_function_name_failure",
        ),
    ],
)
def test_c_cpp_rust_comment_trace_discovery_failures(
    tmp_path: Path, filename: str, source: str, expected_error: str
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match=expected_error):
        load_source_nodes(path, "TestCase", f"tests/{filename}")


@trace("REQ-SCHEMA")
def test_e2e_cli_multi_language_traceability_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "reqs").mkdir()
    (tmp_path / "reqs" / "REQ-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_c.c").write_text(
        '/* @trace("REQ-1") */\nvoid test_c_case(void) {}\n', encoding="utf-8"
    )
    cfg = tmp_path / "traceability.toml"
    cfg.write_text(
        CONFIG_TOML.replace("tests/**/test_*.py", "tests/test_*.c"),
        encoding="utf-8",
    )
    assert main([str(cfg)]) == 0
    assert capsys.readouterr().out == ""
