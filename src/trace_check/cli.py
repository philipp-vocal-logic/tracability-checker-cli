"""CLI entrypoint for trace-check with zero-noise text and JSON output."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from trace_check.checker import Violation, check_violations, load_config


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for trace-check."""
    parser = argparse.ArgumentParser(
        prog="trace-check",
        description="Validate project traceability against a TOML schema and rules.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("traceability.toml"),
        help="Path to traceability TOML file (default: traceability.toml).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    return parser


def _emit_config_error(path: Path, error: Exception, output_format: str) -> int:
    if output_format == "json":
        payload = {
            "ok": False,
            "error": {"code": "ERR_CONFIG", "path": str(path), "message": str(error)},
            "violations": [],
        }
        print(json.dumps(payload), file=sys.stderr)
    else:
        print(f"{path} [ERR_CONFIG]: {error}", file=sys.stderr)
    return 2


def _emit_violations(violations: list[Violation], output_format: str) -> int:
    if output_format == "json":
        payload = {
            "ok": not violations,
            "violations": [violation.to_dict() for violation in violations],
        }
        print(json.dumps(payload))
        return 0 if not violations else 1
    if not violations:
        return 0
    for violation in violations:
        print(violation.format())
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run trace-check and return POSIX exit code (0=ok, 1=violations, 2=config error)."""
    args = build_parser().parse_args(argv)
    path = args.config

    try:
        config = load_config(path)
    except (OSError, ValueError) as error:
        return _emit_config_error(path, error, args.format)

    return _emit_violations(check_violations(config), args.format)


if __name__ == "__main__":
    raise SystemExit(main())
