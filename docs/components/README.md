# Core Component Design Index

The framework-neutral core has eight implemented boundaries and two approved future
external-capability boundaries:

| Component | Owns | Status |
|---|---|---|
| [Sandbox service](./sandbox-service/README.md) | Host lifecycle, handles, registry, provenance, create, resume, delete, and shutdown | Implemented |
| [Sandbox session](./sandbox-session/README.md) | Agent operations, lifecycle state, coordination, and collaborator composition | Implemented |
| [Workspace](./workspace/README.md) | Virtual filesystem, paths, bytes, metadata, quotas, and atomic mutations | Implemented |
| [Command executor](./command-executor/README.md) | Constrained command grammar, registry, dispatch, timeout, and output | Implemented |
| [Policy admission](./policy-engine/README.md) | Minimal explicit operation admission seam; focused external-resource policy remains future work | Implemented seam |
| [Secret broker](./secret-broker/README.md) | Explicit no-secret default and operation-scoped leasing | Implemented |
| [Event sink](./event-sink/README.md) | Structured lifecycle, operation, decision, and audit events | Implemented |
| [Snapshot store](./snapshot-store/README.md) | Immutable versioned snapshot persistence, quotas, expiry, purge, and retrieval | Implemented |
| [Controlled network egress](./network-egress/README.md) | HTTP admission, destination policy, SSRF defense, credential routing, transfer limits, and audit | Approved design; not implemented |
| [External execution and Python runtime](./external-execution/README.md) | Execution coordination, workspace transfer, backend isolation, publication, resources, and runtime provenance | Approved design; not implemented |

## Dependency rule

Components collaborate only through narrow interfaces owned by the consuming boundary.
Concrete implementations are supplied through constructor injection. No component imports
an agent SDK, adapter, protocol transport, or global service locator.

The future unified resource-accounting and host repository-exchange contracts are
sequenced in Milestone 7. Their module boundaries are created only when those issue-sized
design tasks begin.

See the [High-Level Design](../HIGH_LEVEL_DESIGN.md) for architecture, integration levels,
end-to-end flows, and phase scope.
