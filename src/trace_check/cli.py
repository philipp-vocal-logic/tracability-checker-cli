"""CLI entrypoint for trace-check with zero-noise output for agentic tools."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from trace_check.checker import check_config, load_config


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for trace-check."""
    parser = argparse.ArgumentParser(
        prog="trace-check",
        description="Validate project traceability against a TOML schema and rules.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="traceability.toml",
        help="Path to traceability TOML file (default: traceability.toml)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run trace-check and return POSIX exit code (0=ok, 1=violations, 2=config error)."""
    args = build_parser().parse_args(argv)
    path = Path(args.config)

    try:
        config = load_config(path)
    except (OSError, ValueError) as error:
        print(f"{path} [ERR_CONFIG]: {error}", file=sys.stderr)
        return 2

    violations = check_config(config)
    if not violations:
        return 0

    for line in violations:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
