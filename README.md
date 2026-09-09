# MemSandbox

A deterministic in-memory workspace and command sandbox for AI agents.

> **Status:** Early implementation. The architecture is approved, and the
> framework-neutral Python core is being built from the workspace outward.

MemSandbox provides stateful virtual files, constrained command execution, snapshots,
policy enforcement, and agent-framework adapters without exposing the host filesystem or
shell.

## Project boundary

The core owns:

- a byte-oriented in-memory workspace with POSIX paths
- deterministic, atomic, quota-aware filesystem operations
- a constrained virtual command language
- session lifecycle, policy, events, secrets, and snapshots
- typed integration boundaries for agent tools and workspace backends

The core does not depend on an agent SDK. Framework integrations are adapters over the
same domain API.

## Security positioning

MemSandbox is a **logical and API sandbox**, not an operating-system security boundary.
It prevents supported operations from falling through to the host filesystem or shell,
but it cannot safely contain arbitrary Python or native code running in the same process.
Untrusted code execution requires a future process, container, VM, microVM, or WASM
backend.

## Design

- [Design and research index](./docs/README.md)
- [High-level design](./docs/HIGH_LEVEL_DESIGN.md)
- [Implementation plan](./docs/PLAN.md)
- [Workspace design](./docs/components/workspace/README.md)
- [Command executor design](./docs/components/command-executor/README.md)
- [Runnable integration samples](./samples/README.md)

MCP, remote transports, host subprocesses, containers, and full POSIX compatibility are
outside the current implementation phase.

## Development

Prerequisites:

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)

From the repository root:

```console
uv sync --all-groups
uv run pytest
uv run ruff format --check benchmarks evaluations samples src tests
uv run ruff check benchmarks evaluations samples src tests
uv run pyright benchmarks evaluations samples src tests
uv build
```

Development uses short-lived issue branches in separate Git worktrees. The main checkout
stays on `main`, and changes reach it through pull requests.

## Contributing and security

See [CONTRIBUTING.md](./CONTRIBUTING.md) before starting a change. Report security issues
using the private process in [SECURITY.md](./SECURITY.md), not a public issue.

## License

MemSandbox is licensed under the [MIT License](./LICENSE).
