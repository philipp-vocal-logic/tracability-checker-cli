"""Atomic pytest suite for trace-check schemas, lifecycles, discovery, and CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from trace_check.checker import Config, Node, Rule, check_config
from trace_check.cli import main

STORY_REQ_TEST_CHAIN = ("Story", "Requirement", "TestCase")
STORY_REQ_TEST_STATUSES = {
    "Story": frozenset({"DRAFT", "APPROVED", "DEPRECATED"}),
    "Requirement": frozenset({"DRAFT", "APPROVED", "DEPRECATED"}),
    "TestCase": frozenset({"DRAFT", "RED", "GREEN", "DEPRECATED"}),
}


def make_config(
    nodes: dict[str, Node],
    rules: tuple[Rule, ...] = (),
    chain: tuple[str, ...] = STORY_REQ_TEST_CHAIN,
    statuses: dict[str, frozenset[str]] | None = None,
) -> Config:
    """Build a Config with sensible defaults for atomic unit tests."""
    return Config(
        chain=chain,
        statuses=statuses or STORY_REQ_TEST_STATUSES,
        rules=rules,
        nodes=nodes,
    )


# @trace TC-STORY-REQ-CHAIN GREEN [REQ-SCHEMA]
def test_story_requirement_testcase_chain_passes() -> None:
    config = make_config(
        rules=(
            Rule("REQ-STORY", "Requirement", "APPROVED", "Story", ("APPROVED",)),
            Rule("REQ-TESTS", "Requirement", "APPROVED", "TestCase", ("GREEN",)),
        ),
        nodes={
            "US-1": Node("US-1", "Story", "APPROVED"),
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED", links=("US-1",)),
            "TC-1": Node("TC-1", "TestCase", "GREEN", links=("REQ-1",)),
        },
    )

    assert check_config(config) == []


# @trace TC-REQ-SPEC-CHAIN GREEN [REQ-SCHEMA]
def test_requirement_specification_testcase_chain_passes() -> None:
    config = make_config(
        chain=("Requirement", "Specification", "TestCase"),
        statuses={
            "Requirement": frozenset({"DRAFT", "APPROVED", "DEPRECATED"}),
            "Specification": frozenset({"DRAFT", "REVIEWED", "APPROVED", "DEPRECATED"}),
            "TestCase": frozenset({"DRAFT", "RED", "GREEN", "DEPRECATED"}),
        },
        rules=(
            Rule("REQ-SPEC", "Requirement", "APPROVED", "Specification", ("APPROVED",)),
            Rule("SPEC-TEST", "Specification", "APPROVED", "TestCase", ("GREEN",)),
        ),
        nodes={
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED", links=("SPEC-1",)),
            "SPEC-1": Node("SPEC-1", "Specification", "APPROVED", links=("TC-1",)),
            "TC-1": Node("TC-1", "TestCase", "GREEN"),
        },
    )

    assert check_config(config) == []


# @trace TC-SCHEMA-VIOLATIONS GREEN [REQ-SCHEMA]
@pytest.mark.parametrize(
    ("nodes", "expected_error"),
    [
        pytest.param(
            {"N-1": Node("N-1", "Epic", "APPROVED")},
            "N-1 [ERR_UNKNOWN_TYPE]: unknown type 'Epic'",
            id="unknown_node_type",
        ),
        pytest.param(
            {"US-1": Node("US-1", "Story", "GREEN")},
            "US-1 [ERR_INVALID_STATUS]: invalid status 'GREEN' for Story",
            id="invalid_node_status",
        ),
        pytest.param(
            {"US-1": Node("US-1", "Story", "DRAFT", links=("MISSING-99",))},
            "US-1 [ERR_DANGLING_LINK]: target 'MISSING-99' does not exist",
            id="dangling_link",
        ),
        pytest.param(
            {"US-OLD": Node("US-OLD", "Story", "DEPRECATED", links=("MISSING-99",))},
            "US-OLD [ERR_DANGLING_LINK]: target 'MISSING-99' does not exist",
            id="deprecated_node_dangling_link",
        ),
        pytest.param(
            {
                "US-1": Node("US-1", "Story", "DRAFT", links=("TC-1",)),
                "TC-1": Node("TC-1", "TestCase", "DRAFT"),
            },
            "US-1 [ERR_INVALID_EDGE]: cannot link Story to TestCase ('TC-1')",
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
    config = make_config(nodes=nodes)

    assert check_config(config) == [expected_error]


# @trace TC-MISSING-LINK GREEN [REQ-LIFECYCLE]
def test_rule_fails_when_required_link_is_missing() -> None:
    config = make_config(
        rules=(Rule("REQ-TESTS-GREEN", "Requirement", "APPROVED", "TestCase", ("GREEN",)),),
        nodes={"REQ-1": Node("REQ-1", "Requirement", "APPROVED")},
    )

    assert check_config(config) == [
        "REQ-1 [ERR_MISSING_LINK] (REQ-TESTS-GREEN): requires >= 1 active TestCase"
    ]


# @trace TC-TARGET-STATUS GREEN [REQ-LIFECYCLE]
def test_rule_fails_when_linked_target_has_wrong_status() -> None:
    config = make_config(
        rules=(Rule("REQ-TESTS-GREEN", "Requirement", "APPROVED", "TestCase", ("GREEN",)),),
        nodes={
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED", links=("TC-1", "TC-2")),
            "TC-1": Node("TC-1", "TestCase", "GREEN"),
            "TC-2": Node("TC-2", "TestCase", "RED"),
        },
    )

    assert check_config(config) == [
        "REQ-1 [ERR_TARGET_STATUS] (REQ-TESTS-GREEN): "
        "linked TestCase 'TC-2' is 'RED', expected ['GREEN']"
    ]


# @trace TC-DEPRECATED-LINK GREEN [REQ-LIFECYCLE]
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


# @trace TC-DEPRECATED-COVERAGE GREEN [REQ-LIFECYCLE]
def test_deprecated_target_does_not_satisfy_active_coverage_rule() -> None:
    config = make_config(
        rules=(Rule("REQ-NEEDS-TEST", "Requirement", "APPROVED", "TestCase", ("GREEN",)),),
        nodes={
            "REQ-1": Node("REQ-1", "Requirement", "APPROVED"),
            "TC-OLD": Node("TC-OLD", "TestCase", "DEPRECATED", links=("REQ-1",)),
        },
    )

    assert check_config(config) == [
        "REQ-1 [ERR_MISSING_LINK] (REQ-NEEDS-TEST): requires >= 1 active TestCase"
    ]


# @trace TC-DEPRECATED-EXEMPT GREEN [REQ-LIFECYCLE]
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


# @trace TC-INVALID-CONFIG GREEN [REQ-SCHEMA]
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
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\nEpic = ["DRAFT"]\n',
            "statuses defined for unknown node types",
            id="unknown_type_in_statuses",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = "reqs/*.toml"\n',
            "missing locations configuration",
            id="missing_type_locations",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = "reqs/*.toml"\nTestCase = "tests/*.py"\n'
            'Epic = "epics/*.toml"\n',
            "locations defined for unknown node types",
            id="unknown_type_in_locations",
        ),
        pytest.param(
            '[schema]\nchain = ["Story", "Requirement", "TestCase"]\n'
            '[statuses]\nStory = ["APPROVED"]\nRequirement = ["APPROVED"]\n'
            'TestCase = ["GREEN"]\n'
            '[locations]\nStory = "s/*.toml"\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n'
            '[[rules]]\nid = "R1"\nsource_type = "Story"\nwhen_status = "APPROVED"\n'
            'target_type = "TestCase"\ntarget_status = ["GREEN"]\n',
            "not in schema.chain",
            id="rule_non_adjacent_types",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n'
            '[[rules]]\nid = "R1"\nsource_type = "Requirement"\nwhen_status = "TYPO"\n'
            'target_type = "TestCase"\ntarget_status = ["GREEN"]\n',
            "when_status 'TYPO' not in Requirement statuses",
            id="rule_invalid_when_status",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n'
            '[[rules]]\nid = "R1"\nsource_type = "Requirement"\nwhen_status = "APPROVED"\n'
            'target_type = "TestCase"\ntarget_status = ["PASSED"]\n',
            "invalid target_status",
            id="rule_invalid_target_status",
        ),
        pytest.param(
            'schema = "not-a-table"\n',
            "'schema' must be a table",
            id="schema_not_a_table",
        ),
        pytest.param(
            'rules = ["not-a-table"]\n'
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
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
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n',
            "deprecated_status 'RETIRED' not found",
            id="unknown_deprecated_status",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = "/tmp/*.toml"\nTestCase = "t/*.py"\n',
            "must be a relative path pattern",
            id="absolute_location_pattern",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = "../reqs/*.toml"\nTestCase = "t/*.py"\n',
            "must be a relative path pattern",
            id="parent_traversal_location_pattern",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = ["reqs/*.toml"]\nTestCase = "t/*.py"\n',
            "locations.Requirement must be a non-empty string",
            id="non_string_location_pattern",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED", "DEPRECATED"]\nTestCase = ["GREEN"]\n'
            '[locations]\nRequirement = "r/*.toml"\nTestCase = "t/*.py"\n'
            '[[rules]]\nid = "R1"\nsource_type = "Requirement"\nwhen_status = "DEPRECATED"\n'
            'target_type = "TestCase"\ntarget_status = ["GREEN"]\n',
            "when_status 'DEPRECATED' not in Requirement statuses",
            id="rule_when_status_deprecated",
        ),
        pytest.param(
            '[schema]\nchain = ["Requirement", "TestCase"]\n'
            '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
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


# @trace TC-DISCOVERY-ERRORS GREEN [REQ-SCHEMA]
@pytest.mark.parametrize(
    ("req_filename", "req_content", "test_content", "expected_error"),
    [
        pytest.param(
            "REQ-1.toml",
            'id = "REQ-999"\nstatus = "APPROVED"\n',
            "",
            "id 'REQ-999' does not match filename 'REQ-1'",
            id="id_filename_mismatch",
        ),
        pytest.param(
            "REQ-1.toml",
            'type = "Story"\nstatus = "APPROVED"\n',
            "",
            "type 'Story' does not match location type 'Requirement'",
            id="type_location_mismatch",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "# @trace\ndef test_a(): pass\n",
            "malformed @trace directive",
            id="malformed_trace_directive",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "# @trace TC-DUP GREEN [REQ-1]\ndef test_a(): pass\n"
            "# @trace TC-DUP GREEN [REQ-1]\ndef test_b(): pass\n",
            "duplicate node id 'TC-DUP'",
            id="duplicate_node_id",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "# @trace TC-ORPHAN GREEN [REQ-1]\nx = 1\n",
            "@trace must precede a function",
            id="orphan_trace_without_def",
        ),
        pytest.param(
            "REQ-1.toml",
            'status = "APPROVED"\n',
            "# @trace TC-1 GREEN [REQ-1, REQ-1]\ndef test_a(): pass\n",
            "duplicate link targets",
            id="duplicate_trace_links",
        ),
        pytest.param(
            "REQ-1.toml",
            'links = ["US-1"]\n',
            "",
            "REQ-1.toml: missing or invalid 'status'",
            id="missing_node_status",
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
            "unknown keys in",
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
    (tmp_path / "tests" / "test_sample.py").write_text(test_content, encoding="utf-8")
    cfg = tmp_path / "traceability.toml"
    cfg.write_text(
        '[schema]\nchain = ["Requirement", "TestCase"]\n'
        '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
        '[locations]\nRequirement = "reqs/*.toml"\nTestCase = "tests/*.py"\n',
        encoding="utf-8",
    )

    exit_code = main([str(cfg)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert expected_error in captured.err


# @trace TC-CLI-MISSING-FILE GREEN [REQ-ZERO-NOISE]
def test_cli_returns_exit_2_when_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([str(tmp_path / "missing.toml")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[ERR_CONFIG]" in captured.err


# @trace TC-CLI-VIOLATIONS GREEN [REQ-ZERO-NOISE]
def test_cli_returns_exit_1_and_prints_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "reqs").mkdir()
    (tmp_path / "reqs" / "REQ-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    cfg_file = tmp_path / "failing.toml"
    cfg_file.write_text(
        '[schema]\nchain = ["Requirement", "TestCase"]\n'
        '[statuses]\nRequirement = ["APPROVED"]\nTestCase = ["GREEN"]\n'
        '[locations]\nRequirement = "reqs/*.toml"\nTestCase = "tests/*.py"\n'
        '[[rules]]\nid = "REQ-GREEN"\nsource_type = "Requirement"\n'
        'when_status = "APPROVED"\ntarget_type = "TestCase"\ntarget_status = ["GREEN"]\n',
        encoding="utf-8",
    )

    exit_code = main([str(cfg_file)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out.strip() == (
        "reqs/REQ-1.toml: REQ-1 [ERR_MISSING_LINK] (REQ-GREEN): requires >= 1 active TestCase"
    )
    assert captured.err == ""


# @trace TC-DOGFOOD GREEN [REQ-ZERO-NOISE]
def test_cli_returns_exit_0_with_zero_noise_on_project_traceability_toml(
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = Path(__file__).resolve().parent.parent

    exit_code = main([str(repo_root / "traceability.toml")])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
