# Contributing to MemSandbox

Thank you for helping build MemSandbox.

## Before starting

1. Open or select a GitHub issue describing the behavior and acceptance criteria.
2. Keep the main checkout on `main`.
3. Create a short-lived issue branch in a separate worktree:

   ```console
   git fetch origin
   git worktree add ../mem-sandbox-<short-name> -b feature/<issue>-<short-name> main
   ```

Use `fix/<issue>-<short-name>` for bug fixes. Do not implement directly in the main
checkout.

## Development setup

Install Python 3.12 or later and [uv](https://docs.astral.sh/uv/), then run:

```console
uv sync --all-groups
```

## Implementation expectations

- Write or update behavior tests before implementation.
- Keep core modules independent of agent frameworks.
- Express collaborators through narrow constructor-injected interfaces.
- Use immutable domain request and result types at boundaries.
- Reject unsupported behavior explicitly; never fall back to the host filesystem or shell.
- Update the owning module `README.md` when behavior, interfaces, invariants, or
  dependencies change.

## Required checks

Run the same checks used by CI:

```console
uv run pytest
uv run ruff format --check src tests
uv run ruff check src tests
uv run pyright src tests
uv build
```

## Pull requests

Keep each pull request focused on one issue or behavior seam. Explain the behavior change,
important design decisions, and any deferred work. Link the issue and ensure all required
checks pass before merge.
