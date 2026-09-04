# Integration Design Index

## Current phase

The current design supports two integration levels:

1. [Tool/capability integration](./tool-capability/README.md)
2. [Workspace/backend integration](./workspace-backend/README.md)

Both levels delegate to the same framework-neutral `SandboxService` and
`SandboxSession`. Neither level may reimplement workspace, command, policy, secret,
snapshot, or event behavior.

Milestone 5 proves both levels first through the OpenAI Agents SDK:

- an OpenAI custom `Capability` exposes the approved four model-facing tools;
- an OpenAI sandbox client/session implements the native workspace and lifecycle
  contract.

PydanticAI, LangChain Deep Agents, and other SDKs are evidence-driven follow-ups, not
Milestone 5 completion requirements. Shared production helpers are extracted only after
two adapters prove that the behavior is actually identical.

## Selection guide

| Framework capability | Integration level |
|---|---|
| Accepts typed Python functions or toolsets | Tool/capability |
| Supports lifecycle-aware capability packages | Tool/capability |
| Defines a replaceable filesystem or workspace backend | Workspace/backend |
| Defines a sandbox client and live session contract | Workspace/backend |
| Has no stable native extension beyond functions | Tool/capability |

A framework may support both. In that case:

- use tool/capability integration for the smallest stable dependency
- add workspace/backend integration when it activates valuable framework-native
  lifecycle, file, shell, or snapshot behavior

## Shared invariants

- The host owns session creation and disposal.
- The model receives only explicitly registered tools.
- Framework state is not sandbox state.
- Adapters translate domain requests, results, and errors.
- Unsupported features fail explicitly.
- No adapter falls back to the host filesystem or host shell.
- Adapter dependencies remain outside the core package.

## Framework mapping

| Framework | Initial integration |
|---|---|
| OpenAI Agents SDK `SandboxAgent` | **Milestone 5 priority:** custom capability plus workspace/backend client and session |
| PydanticAI | Deferred tool/capability follow-up |
| LangChain Deep Agents | Deferred workspace/backend follow-up |
| Microsoft Agent Framework | Deferred tool/capability follow-up |
| Google ADK | Deferred tool/capability follow-up |
| Strands, LlamaIndex, CrewAI, Haystack, Agno, smolagents | Examples only when a concrete use case requires them |

## Deferred integrations

MCP is a possible future interoperability adapter. It is intentionally excluded from the
current architecture and implementation phase. Reconsider it only after the OpenAI
capability and sandbox backend pass shared conformance without changing the core
contracts.

HTTP/OpenAPI, A2A, hosted sandbox providers, and remote multi-tenant services are also
deferred.
