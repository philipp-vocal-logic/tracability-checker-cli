# trace-check

Zero-noise CLI project traceability checker using only the Python standard library.

## Usage

```bash
trace-check [--format {text,json}] [traceability.toml]
```

### Exit Codes

- `0`: All traceability rules and links pass (silent output in `text` mode; `{"ok": true, "violations": []}` in `json` mode).
- `1`: One or more traceability violations found (printed to `stdout` as `<location>: <id> [<ERR_CODE>][ (<rule_id>)]: <message>` in `text` mode, or structured JSON in `json` mode).
- `2`: Configuration or node discovery error (printed to `stderr` with `[ERR_CONFIG]` in `text` mode, or structured JSON in `json` mode).

## Configuration (`traceability.toml`)

```toml
[schema]
chain = ["Story", "Requirement", "TestCase"]

[statuses]
Story = ["DRAFT", "APPROVED", "DEPRECATED"]
Requirement = ["DRAFT", "APPROVED", "DEPRECATED"]
TestCase = ["DRAFT", "APPROVED", "DEPRECATED"]

[locations]
Story = "trace/stories/*.toml"
Requirement = "trace/requirements/*.toml"
TestCase = "tests/test_*.py"

[[rules]]
id = "APPROVED-REQ-NEEDS-TEST"
source_type = "Requirement"
when_status = "APPROVED"
target_type = "TestCase"
target_status = ["APPROVED"]
```

Each rule requires that every active node of `source_type` with status `when_status` links to at least one active node of adjacent `target_type`, and that all linked active nodes of `target_type` have a status in `target_status`.

## Node Definitions

- **TOML nodes** (`*.toml`): One node per file (`id` defaults to filename stem, `type` defaults to location type, required `status`, optional `links`, `title`, `description`).
- **Source nodes** (`@trace` decorator on `def test_*` functions in `.py` files, parsed statically via `ast`; `id` defaults to the pytest-style qualified name such as `tests/test_x.py::TestSuite::test_example`, and `status` defaults to `"APPROVED"`). The decorator is recognized only when it resolves to `trace_check.trace` through the module's imports (`from trace_check import trace [as t]`, `import trace_check [as tc]`, `from trace_check import checker`, ...); other decorators named `trace` are ignored. Pass `id="TC-1"` for a short explicit id (`[A-Za-z0-9_-]+`):
  ```python
  from trace_check import trace


  @trace("REQ-1", "REQ-2")
  def test_example() -> None: ...
  ```

## Development

```bash
# Enable local git hooks (pre-commit and commit-msg)
git config core.hooksPath .githooks

uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run glossary
uv run symbol
uv run architecture
uv run trace-check
```
