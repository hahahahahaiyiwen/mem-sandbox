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
- Add first-class adapters for PydanticAI capabilities, LangChain Deep Agents backends,
  and the OpenAI Agents SDK sandbox client/session contract.
- Treat an OpenAI Agents SDK `SandboxAgent` integration as an experimental adapter while
  that API remains beta.
- Defer MCP until the native boundaries are implemented and validated.
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
- [Product validation and benchmarks](./product-validation/README.md)

## Deferred design explorations

- [Workspace content offload](./components/workspace/content-offload/README.md) - keep
  the workspace tree in memory while optionally externalizing immutable file bytes for
  larger logical workspaces and lazy resume.

## Research references

See [Python Agent Sandbox Integration Research](./AGENT_SANDBOX_INTEGRATION_RESEARCH.md)
for the evidence, tradeoffs, proposed interfaces, roadmap, and learning exercises.

See [OpenAI `SandboxAgent` Backend Contract](./OPENAI_SANDBOX_AGENT_ADAPTER.md) for the
exact client, session, state, snapshot, and runtime wiring APIs required by an in-memory
OpenAI Agents SDK adapter.
