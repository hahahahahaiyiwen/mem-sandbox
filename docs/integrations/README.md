# Integration Design Index

## Current phase

The current design supports two integration levels:

1. [Tool/capability integration](./tool-capability/README.md)
2. [Workspace/backend integration](./workspace-backend/README.md)

Both levels delegate to the same framework-neutral `SandboxService` and
`SandboxSession`. Neither level may reimplement workspace, command, policy, secret,
snapshot, or event behavior.

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
| PydanticAI | Tool/capability |
| LangChain Deep Agents | Workspace/backend |
| OpenAI Agents SDK `SandboxAgent` | Workspace/backend, experimental while beta |
| Microsoft Agent Framework | Tool/capability |
| Google ADK | Tool/capability |
| Strands, LlamaIndex, CrewAI, Haystack, Agno, smolagents | Tool/capability examples as needed |

## Deferred integrations

MCP is a possible future interoperability adapter. It is intentionally excluded from the
current architecture and implementation phase. Reconsider it only after both native
integration levels pass shared conformance scenarios without changing the core contracts.

HTTP/OpenAPI, A2A, hosted sandbox providers, and remote multi-tenant services are also
deferred.
