# trace-check

Zero-noise CLI project traceability checker using only the Python standard library.

## Usage

```bash
trace-check [traceability.toml]
```

### Exit Codes

- `0`: All traceability rules and links pass (silent output).
- `1`: One or more traceability violations found (printed to `stdout` as `<location>: <id> [<ERR_CODE>]: <message>`, one per line).
- `2`: Configuration or node discovery error (printed to `stderr` with `[ERR_CONFIG]`).

## Configuration (`traceability.toml`)

```toml
[schema]
chain = ["Story", "Requirement", "TestCase"]

[statuses]
Story = ["DRAFT", "APPROVED", "DEPRECATED"]
Requirement = ["DRAFT", "APPROVED", "DEPRECATED"]
TestCase = ["DRAFT", "RED", "GREEN", "DEPRECATED"]

[locations]
Story = "trace/stories/*.toml"
Requirement = "trace/requirements/*.toml"
TestCase = "tests/test_*.py"

[[rules]]
id = "APPROVED-REQ-ALL-TESTS-GREEN"
source_type = "Requirement"
when_status = "APPROVED"
target_type = "TestCase"
target_status = ["GREEN"]
```

Each rule requires that every active node of `source_type` with status `when_status` links to at least one active node of adjacent `target_type`, and that all linked active nodes of `target_type` have a status in `target_status`.

## Node Definitions

- **TOML nodes** (`*.toml`): One node per file (`id` defaults to filename stem, `type` defaults to location type, required `status`, optional `links`, `title`, `description`).
- **Source nodes** (comments in `.py` files, placed before a `def` function definition; `trace-check` validates static traceability while `pytest` verifies test execution):
  ```python
  # @trace TC-EXAMPLE GREEN [REQ-1, REQ-2]
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
