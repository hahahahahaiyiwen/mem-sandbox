# OpenAI Agents SDK Integration

## Purpose and ownership

This package owns translation between MemSandbox public contracts and the OpenAI Agents
SDK sandbox client, session, state, snapshot, and capability APIs. It does not own
filesystem semantics, command execution, lifecycle state, policy, secrets, events, or
snapshot content.

Install the optional dependency with:

```text
pip install "mem-sandbox[openai-agents]"
```

The supported SDK range is `openai-agents>=0.22,<0.23`. Contract tests execute against
exactly `0.22.0`.

## Current implementation status

Milestone 5.1 establishes the package and dependency boundary only. No client, session,
or capability implementation is exported yet.

The public core audit confirmed these reusable contracts:

| Required behavior | Public core contract |
|---|---|
| Complete binary reads and writes | `SandboxSession.read_bytes` and `SandboxSession.write_bytes` |
| Metadata and directory listing | `SandboxSession.stat` and `SandboxSession.list_entries` |
| Owned lifecycle | `SandboxService.create`, `get_session`, `resume`, `delete`, and `close` |
| Process-local snapshots | `SandboxSession.create_snapshot` and service snapshot gateways |
| Constrained command execution | `SandboxSession.execute` |

The adapter prerequisites intentionally remain in owning core modules:

- native session-level directory creation and removal are tracked by issue #31;
- bounded portable workspace export and hydration are tracked by issue #32.

The OpenAI client/session, lifecycle state, and capability implementations follow only
after those prerequisites are available.

## Compatibility policy

OpenAI Sandbox Agents are beta. Any change to the pinned abstract methods, method
signatures, state or run-configuration fields, lifecycle ordering, serialization,
capability cloning/binding, or snapshot protocol must fail contract tests and receive an
explicit compatibility review.

Support is limited to the documented `>=0.22,<0.23` range. Expanding that range requires
running the contract and conformance suites against the proposed versions and updating
this README and the integration design documents.

## Maintenance rules

- Import MemSandbox behavior only from public package exports such as
  `mem_sandbox.session` and `mem_sandbox.service`.
- Keep OpenAI-native types and dependencies inside this package.
- Use constructor injection for core service dependencies. The SDK-required capability
  `bind` hook may attach the live session to a per-run clone but must not perform global
  lookup.
- Translate requests and results; do not duplicate core validation or mutation behavior.
- Reject unsupported SDK features explicitly and never use the host filesystem or shell
  as a fallback.
- Do not introduce a cross-framework adapter abstraction until another implemented SDK
  proves identical reusable behavior.
