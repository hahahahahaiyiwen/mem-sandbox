# MemSandbox Design and Research

This directory is the authoritative source for MemSandbox architecture, component
boundaries, integration guidance, and implementation planning. The project is the Python
successor to the original .NET Agent Sandbox.

## Current recommendation

- Keep the core sandbox independent of every agent framework and model provider.
- Expose a plain, typed Python API as the canonical in-process interface.
- Design two native integration levels:
  - tool/capability adapters
  - workspace/backend adapters
- Keep the implemented OpenAI Agents SDK capability and sandbox client/session as the
  first native integration; select a second SDK from user and ecosystem evidence.
- Preserve the host-free `virtual` profile as the default.
- Add external capabilities only through immutable host-selected profiles, focused
  policy, cumulative resource accounting, and bounded audit.
- Sequence host-controlled repository exchange before model-visible VCS authority.
- Add controlled outbound HTTP before composing network access with external Python.
- Execute arbitrary Python only through an external backend with an explicit security
  classification.
- Defer MCP and remote control planes until their underlying domain boundaries are
  implemented and validated.
- Describe the product as a deterministic virtual workspace, not an OS security boundary.

## Design documents

- [High-Level Design](./HIGH_LEVEL_DESIGN.md)
- [Implementation Plan](./PLAN.md)
- [Core component index](./components/README.md)
- [Tool/capability integration](./integrations/tool-capability/README.md)
- [Workspace/backend integration](./integrations/workspace-backend/README.md)
- [Sandbox service](./components/sandbox-service/README.md)
- [Sandbox session](./components/sandbox-session/README.md)
- [Workspace](./components/workspace/README.md)
- [Command executor](./components/command-executor/README.md)
- [Policy engine](./components/policy-engine/README.md)
- [Secret broker](./components/secret-broker/README.md)
- [Event sink](./components/event-sink/README.md)
- [Snapshot store](./components/snapshot-store/README.md)
- [Controlled network egress](./components/network-egress/README.md)
- [External execution and Python runtime](./components/external-execution/README.md)
- [Product validation and benchmarks](./product-validation/README.md)

## Post-Milestone-5 design directions

- [Workspace content offload](./components/workspace/content-offload/README.md) - keep
  the workspace tree in memory while optionally externalizing immutable file bytes for
  larger logical workspaces and lazy resume; implementation remains conditional on
  Milestone 6 evidence.
- [Controlled network egress](./components/network-egress/README.md) - default-deny
  outbound HTTP shared by trusted command and typed-tool adapters.
- [External execution and Python runtime](./components/external-execution/README.md) -
  external backend execution with bounded workspace transfer, accurate isolation
  profiles, and Python as the first planned runtime.
- [Implementation plan](./PLAN.md#11-milestone-7-external-capability-foundations) -
  capability grants, unified resource accounting, and host-controlled repository
  ingestion and result export.

## Research references

See [Python Agent Sandbox Integration Research](./AGENT_SANDBOX_INTEGRATION_RESEARCH.md)
for the evidence, tradeoffs, proposed interfaces, roadmap, and learning exercises.

See [OpenAI `SandboxAgent` Backend Contract](./OPENAI_SANDBOX_AGENT_ADAPTER.md) for the
exact client, session, state, snapshot, and runtime wiring APIs required by an in-memory
OpenAI Agents SDK adapter.

See [OSS POSIX and Bash Parser Evaluation](./Python/POSIX_PARSER_EVALUATION.md) for the
comparison of the MemSandbox parser with `shlex`, Parsify, `bashlex`, and
`tree-sitter-bash`, plus the decision to continue with the constrained parser.
