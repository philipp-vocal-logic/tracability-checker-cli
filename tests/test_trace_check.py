"""Atomic pytest suite for trace-check schemas, lifecycles, @trace annotations, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_check.checker import Config, Node, Rule, check_config, trace
from trace_check.cli import main

STORY_REQ_TEST_CHAIN = ("Story", "Requirement", "TestCase")
STORY_REQ_TEST_STATUSES = {
    "Story": frozenset({"DRAFT", "APPROVED", "DEPRECATED"}),
    "Requirement": frozenset({"DRAFT", "APPROVED", "DEPRECATED"}),
    "TestCase": frozenset({"DRAFT", "APPROVED", "DEPRECATED"}),
}


def make_config(
    nodes: dict[str, Node],
    rules: tuple[Rule, ...] = (),
    chain: tuple[str, ...] = STORY_REQ_TEST_CHAIN,
    statuses: dict[str, frozenset[str]] | None = None,
) -> Config:
    """Build a Config with sensible defaults for atomic unit tests."""
    return Config(chain, statuses or STORY_REQ_TEST_STATUSES, rules, nodes)


@trace("REQ-SCHEMA")
def test_story_requirement_testcase_chain_passes() -> None:
    config = make_config(
        rules=(
            Rule("REQ-STORY", "Requirement", "APPROVED", "Story", ("APPROVED",)),
            Rule("REQ-TESTS", "Requirement", "APPROVED", "TestCase", ("APPROVED",)),
        ),
        nodes={
            "US-1": Node("US-1", "Story", "APPROVED"),
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED", links=("US-1",)),
            "test_case_1": Node("test_case_1", "TestCase", "APPROVED", links=("REQ-1",)),
        },
    )
    assert check_config(config) == []


@trace("REQ-SCHEMA")
def test_requirement_specification_testcase_chain_passes() -> None:
    config = make_config(
        chain=("Requirement", "Specification", "TestCase"),
        statuses={
            "Requirement": frozenset({"DRAFT", "APPROVED", "DEPRECATED"}),
            "Specification": frozenset({"DRAFT", "REVIEWED", "APPROVED", "DEPRECATED"}),
            "TestCase": frozenset({"DRAFT", "APPROVED", "DEPRECATED"}),
        },
        rules=(
            Rule("REQ-SPEC", "Requirement", "APPROVED", "Specification", ("APPROVED",)),
            Rule("SPEC-TEST", "Specification", "APPROVED", "TestCase", ("APPROVED",)),
        ),
        nodes={
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED", links=("SPEC-1",)),
            "SPEC-1": Node("SPEC-1", "Specification", "APPROVED", links=("test_case_1",)),
            "test_case_1": Node("test_case_1", "TestCase", "APPROVED"),
        },
    )
    assert check_config(config) == []


@trace("REQ-SCHEMA")
@pytest.mark.parametrize(
    ("nodes", "expected_error"),
    [
        pytest.param(
            {"N-1": Node("N-1", "Epic", "APPROVED")},
            "N-1 [ERR_UNKNOWN_TYPE]: unknown type 'Epic'",
            id="unknown_node_type",
        ),
        pytest.param(
            {"US-1": Node("US-1", "Story", "INVALID")},
            "US-1 [ERR_INVALID_STATUS]: invalid status 'INVALID' for Story",
            id="invalid_node_status",
        ),
        pytest.param(
            {"US-1": Node("US-1", "Story", "DRAFT", links=("MISSING-99",))},
            "US-1 [ERR_DANGLING_LINK]: target 'MISSING-99' does not exist",
            id="dangling_link",
        ),
        pytest.param(
            {"US-1": Node("US-1", "Story", "DRAFT", links=("bad id",))},
            "US-1 [ERR_DANGLING_LINK]: target 'bad id' does not exist",
            id="malformed_target_link_is_dangling",
        ),
        pytest.param(
            {"US-OLD": Node("US-OLD", "Story", "DEPRECATED", links=("MISSING-99",))},
            "US-OLD [ERR_DANGLING_LINK]: target 'MISSING-99' does not exist",
            id="deprecated_node_dangling_link",
        ),
        pytest.param(
            {
                "US-1": Node("US-1", "Story", "DRAFT", links=("test_1",)),
                "test_1": Node("test_1", "TestCase", "DRAFT"),
            },
            "US-1 [ERR_INVALID_EDGE]: cannot link Story to TestCase ('test_1')",
            id="non_adjacent_edge",
        ),
        pytest.param(
            {"US-1": Node("US-1", "Story", "DRAFT", links=("MISSING",), location="s/US-1.toml")},
            "s/US-1.toml: US-1 [ERR_DANGLING_LINK]: target 'MISSING' does not exist",
            id="node_location_prefix",
        ),
    ],
)
def test_schema_and_link_violation(nodes: dict[str, Node], expected_error: str) -> None:
    assert check_config(make_config(nodes=nodes)) == [expected_error]


@trace("REQ-LIFECYCLE")
def test_rule_fails_when_required_link_is_missing() -> None:
    config = make_config(
        rules=(Rule("REQ-NEEDS-TEST", "Requirement", "APPROVED", "TestCase", ("APPROVED",)),),
        nodes={"REQ-1": Node("REQ-1", "Requirement", "APPROVED")},
    )
    assert check_config(config) == [
        "REQ-1 [ERR_MISSING_LINK] (REQ-NEEDS-TEST): requires >= 1 active TestCase"
    ]


@trace("REQ-LIFECYCLE")
def test_rule_fails_when_linked_target_has_wrong_status() -> None:
    config = make_config(
        rules=(Rule("REQ-NEEDS-TEST", "Requirement", "APPROVED", "TestCase", ("APPROVED",)),),
        nodes={
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED", links=("test_1", "test_2")),
            "test_1": Node("test_1", "TestCase", "APPROVED"),
            "test_2": Node("test_2", "TestCase", "DRAFT"),
        },
    )
    assert check_config(config) == [
        "REQ-1 [ERR_TARGET_STATUS] (REQ-NEEDS-TEST): "
        "linked TestCase 'test_2' is 'DRAFT', expected ['APPROVED']"
    ]


@trace("REQ-LIFECYCLE")
def test_active_node_linking_to_deprecated_node_fails() -> None:
    config = make_config(
        nodes={
            "US-OLD": Node("US-OLD", "Story", "DEPRECATED"),
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED", links=("US-OLD",)),
        },
    )
    assert check_config(config) == [
        "REQ-1 [ERR_DEPRECATED_LINK]: active Requirement links to DEPRECATED Story 'US-OLD'"
    ]


@trace("REQ-LIFECYCLE")
def test_deprecated_target_does_not_satisfy_active_coverage_rule() -> None:
    config = make_config(
        rules=(Rule("REQ-NEEDS-TEST", "Requirement", "APPROVED", "TestCase", ("APPROVED",)),),
        nodes={
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED"),
            "test_old": Node("test_old", "TestCase", "DEPRECATED", links=("REQ-1",)),
        },
    )
    assert check_config(config) == [
        "REQ-1 [ERR_MISSING_LINK] (REQ-NEEDS-TEST): requires >= 1 active TestCase"
    ]


@trace("REQ-LIFECYCLE")
def test_deprecated_node_is_exempt_from_active_rules_and_link_errors() -> None:
    config = make_config(
        rules=(Rule("REQ-NEEDS-STORY", "Requirement", "DEPRECATED", "Story", ("APPROVED",)),),
        nodes={
            "US-1": Node("US-1", "Story", "APPROVED"),
            "US-OLD": Node("US-OLD", "Story", "DEPRECATED"),
            "REQ-OLD": Node("REQ-OLD", "Requirement", "DEPRECATED", links=("US-1", "US-OLD")),
        },
    )
    assert check_config(config) == []


@trace("REQ-SCHEMA")
@pytest.mark.parametrize(
    ("toml_content", "expected_error_substring"),
    [
        pytest.param(
            '[schema]\nchain = ["OnlyOne"]\n',
            "schema.chain must contain at least 2 node types",
            id="chain_too_short",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "Requirement"]\n',
            "schema.chain must not contain duplicate node types",
            id="chain_duplicate_types",
        ),
        pytest.param(
            '[schema]\nchain = "Requirement"\n',
            "'schema.chain' must be a list of strings",
            id="chain_not_a_list",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\n',
            "missing statuses configuration",
            id="missing_type_statuses",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\nEpic = ["DRAFT"]\n',
            "statuses defined for unknown node types",
            id="unknown_type_in_statuses",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "reqs/*.toml"\n',
            "missing locations configuration",
            id="missing_type_locations",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "reqs/*.toml"\nTestCase = "tests/*.py"\n'
            'Epic = "epics/*.toml"\n',
            "locations defined for unknown node types",
            id="unknown_type_in_locations",
        ),
        pytest.param(
            '[schema]\nchain = ["Story", "Requirement", "TestCase"]\n'
            '[statuses]\nStory = ["APPROVED"]\nRequirement = ["APPROVED"]\n'
            'TestCase = ["APPROVED"]\n'
            '[locations]\nStory = "s/*.toml"\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n'
            '[[rules]]\nid = "R1"\nsource_type = "Story"\nwhen_status = "APPROVED"\n'
            'target_type = "TestCase"\ntarget_status = ["APPROVED"]\n',
            "not in schema.chain",
            id="rule_non_adjacent_types",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n'
            '[[rules]]\nid = "R1"\nsource_type = "Requirement"\nwhen_status = "TYPO"\n'
            'target_type = "TestCase"\ntarget_status = ["APPROVED"]\n',
            "when_status 'TYPO' not in Requirement statuses",
            id="rule_invalid_when_status",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n'
            '[[rules]]\nid = "R1"\nsource_type = "Requirement"\nwhen_status = "APPROVED"\n'
            'target_type = "TestCase"\ntarget_status = ["UNKNOWN"]\n',
            "invalid target_status",
            id="rule_invalid_target_status",
        ),
        pytest.param('schema = "not-a-table"\n', "'schema' must be a table", id="schema_not_table"),
        pytest.param(
            'rules = ["not-a-table"]\n'
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n',
            "'rule' must be a table",
            id="rules_not_tables",
        ),
        pytest.param(
            'extra = true\n[schema]\nchain = ["Requirement", "TestCase"]\n',
            "unknown keys in config",
            id="unknown_top_level_key",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\ndeprecated_status = "RETIRED"\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n',
            "deprecated_status 'RETIRED' not found",
            id="unknown_deprecated_status",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "/tmp/*.toml"\nTestCase = "t/*.py"\n',
            "must be a relative path pattern",
            id="absolute_location_pattern",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "../reqs/*.toml"\nTestCase = "t/*.py"\n',
            "must be a relative path pattern",
            id="parent_traversal_location_pattern",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = ["reqs/*.toml"]\nTestCase = "t/*.py"\n',
            "locations.Requirement must be a non-empty string",
            id="non_string_location_pattern",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED", "DEPRECATED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n'
            '[[rules]]\nid = "R1"\nsource_type = "Requirement"\nwhen_status = "DEPRECATED"\n'
            'target_type = "TestCase"\ntarget_status = ["APPROVED"]\n',
            "when_status 'DEPRECATED' not in Requirement statuses",
            id="rule_when_status_deprecated",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n',
            "no traceability nodes discovered",
            id="empty_node_discovery",
        ),
    ],
)
def test_cli_returns_exit_2_on_invalid_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    toml_content: str,
    expected_error_substring: str,
) -> None:
    cfg_file = tmp_path / "invalid.toml"
    cfg_file.write_text(toml_content, encoding="utf-8")
    exit_code = main([str(cfg_file)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert expected_error_substring in captured.err


@trace("REQ-SCHEMA")
@pytest.mark.parametrize(
    ("req_filename", "req_content", "test_content", "expected_error"),
    [
        pytest.param(
            "REQ-1.toml",
            'id = "REQ-999"\nstatus = "APPROVED"\n',
            "",
            "reqs/REQ-1.toml: id 'REQ-999' does not match filename 'REQ-1'",
            id="id_filename_mismatch",
        ),
        pytest.param(
            "REQ 1.toml",
            'status = "APPROVED"\n',
            "",
            "reqs/REQ 1.toml: invalid node id 'REQ 1'",
            id="invalid_toml_filename_id",
        ),
        pytest.param(
            "REQ-1.toml",
            'type = "Story"\nstatus = "APPROVED"\n',
            "",
            "reqs/REQ-1.toml: type 'Story' does not match location type 'Requirement'",
            id="type_location_mismatch",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "@trace\ndef test_a(): pass\n",
            "tests/test_sample.py:2: malformed @trace decorator: expected @trace(...)",
            id="bare_trace_decorator",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "@trace(123)\ndef test_a(): pass\n",
            "tests/test_sample.py:2: malformed @trace decorator: link must be a non-empty string",
            id="non_string_trace_arg",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "@trace('REQ-1', unknown='x')\ndef test_a(): pass\n",
            "tests/test_sample.py:2: malformed @trace decorator: unknown keyword 'unknown'",
            id="unknown_trace_keyword",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "@trace('REQ-1', id='TC-DUP')\ndef test_a(): pass\n"
            "@trace('REQ-1', id='TC-DUP')\ndef test_b(): pass\n",
            "tests/test_sample.py: duplicate node id 'TC-DUP'",
            id="duplicate_node_id",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "@trace('REQ-1')\ndef sample_fn(): pass\n",
            "tests/test_sample.py:2: @trace must decorate a test_* function, got 'sample_fn'",
            id="trace_on_non_test_function",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "@trace('REQ-1')\nclass SampleSuite: pass\n",
            "tests/test_sample.py:2: @trace must decorate a test_* function, got 'SampleSuite'",
            id="trace_on_class",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "@trace('REQ-1')\n@trace('REQ-1')\ndef test_a(): pass\n",
            "tests/test_sample.py:2: duplicate @trace decorator on 'test_a'",
            id="duplicate_trace_on_same_function",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "@trace('REQ-1', 'REQ-1')\ndef test_a(): pass\n",
            "duplicate link targets",
            id="duplicate_trace_links",
        ),
        pytest.param(
            "REQ-1.toml",
            'links = ["US-1"]\n',
            "@trace\ndef test_a(): pass\n",
            "reqs/REQ-1.toml: missing or invalid 'status'; "
            "tests/test_sample.py:2: malformed @trace decorator",
            id="multiple_discovery_errors_collected",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\nlinks = "US-1"\n',
            "",
            "links' must be a list of strings",
            id="links_not_a_list",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\nlink = ["US-1"]\n',
            "",
            "unknown keys in reqs/REQ-1.toml",
            id="unknown_node_key",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "def f():\n    if 1:\n        x = 1\n      y = 2\n",
            "unindent does not match any outer indentation level",
            id="python_indentation_error",
        ),
    ],
)
def test_cli_returns_exit_2_on_node_discovery_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    req_filename: str,
    req_content: str,
    test_content: str,
    expected_error: str,
) -> None:
    (tmp_path / "reqs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "reqs" / req_filename).write_text(req_content, encoding="utf-8")
    test_source = f"from trace_check import trace\n{test_content}"
    (tmp_path / "tests" / "test_sample.py").write_text(test_source, encoding="utf-8")
    cfg = tmp_path / "traceability.toml"
    cfg.write_text(
        '[schema]\nchain = ["Requirement", "TestCase"]\n'
        '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
        '[locations]\nRequirement = "reqs/*.toml"\nTestCase = "tests/*.py"\n',
        encoding="utf-8",
    )
    assert main([str(cfg)]) == 2
    assert expected_error in capsys.readouterr().err


@trace("REQ-ZERO-NOISE")
def test_cli_returns_exit_2_when_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path / "missing.toml")]) == 2
    assert "[ERR_CONFIG]" in capsys.readouterr().err


@trace("REQ-ZERO-NOISE")
def test_cli_returns_exit_1_and_prints_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "reqs").mkdir()
    (tmp_path / "reqs" / "REQ-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    cfg_file = tmp_path / "failing.toml"
    cfg_file.write_text(
        '[schema]\nchain = ["Requirement", "TestCase"]\n'
        '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
        '[locations]\nRequirement = "reqs/*.toml"\nTestCase = "tests/*.py"\n'
        '[[rules]]\nid = "REQ-TEST"\nsource_type = "Requirement"\n'
        'when_status = "APPROVED"\ntarget_type = "TestCase"\ntarget_status = ["APPROVED"]\n',
        encoding="utf-8",
    )
    exit_code = main([str(cfg_file)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out.strip() == (
        "reqs/REQ-1.toml: REQ-1 [ERR_MISSING_LINK] (REQ-TEST): requires >= 1 active TestCase"
    )
    assert captured.err == ""


@trace("REQ-ZERO-NOISE")
def test_cli_json_format_outputs_structured_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    assert main(["--format", "json", str(repo_root / "traceability.toml")]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "violations": []}

    (tmp_path / "reqs").mkdir()
    (tmp_path / "reqs" / "REQ-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    cfg_file = tmp_path / "failing.toml"
    cfg_file.write_text(
        '[schema]\nchain = ["Requirement", "TestCase"]\n'
        '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["APPROVED"]\n'
        '[locations]\nRequirement = "reqs/*.toml"\nTestCase = "tests/*.py"\n'
        '[[rules]]\nid = "REQ-TEST"\nsource_type = "Requirement"\n'
        'when_status = "APPROVED"\ntarget_type = "TestCase"\ntarget_status = ["APPROVED"]\n',
        encoding="utf-8",
    )
    assert main(["--format", "json", str(cfg_file)]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "violations": [
            {
                "location": "reqs/REQ-1.toml",
                "node_id": "REQ-1",
                "code": "ERR_MISSING_LINK",
                "rule_id": "REQ-TEST",
                "message": "requires >= 1 active TestCase",
            }
        ],
    }

    assert main(["--format", "json", str(tmp_path / "missing.toml")]) == 2
    error_payload = json.loads(capsys.readouterr().err)
    assert error_payload["ok"] is False
    assert error_payload["error"]["code"] == "ERR_CONFIG"


@trace("REQ-ZERO-NOISE")
def test_cli_returns_exit_0_with_zero_noise_on_project_traceability_toml(
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    exit_code = main([str(repo_root / "traceability.toml")])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
