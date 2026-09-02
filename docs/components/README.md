# Core Component Design Index

The framework-neutral core is divided into eight boundaries:

| Component | Owns |
|---|---|
| [Sandbox service](./sandbox-service/README.md) | Host lifecycle, handles, registry, create, resume, and delete |
| [Sandbox session](./sandbox-session/README.md) | Agent operations, lifecycle state, coordination, and collaborator composition |
| [Workspace](./workspace/README.md) | Virtual filesystem, paths, bytes, metadata, quotas, and atomic mutations |
| [Command executor](./command-executor/README.md) | Constrained command grammar, registry, dispatch, timeout, and output |
| [Policy admission](./policy-engine/README.md) | Minimal explicit operation admission seam; composed authorization is deferred |
| [Secret broker](./secret-broker/README.md) | Explicit no-secret boundary; functional leases are deferred |
| [Event sink](./event-sink/README.md) | Structured lifecycle, operation, decision, and audit events |
| [Snapshot store](./snapshot-store/README.md) | Immutable versioned snapshot persistence and retrieval |

## Dependency rule

Components collaborate only through narrow interfaces owned by the consuming boundary.
Concrete implementations are supplied through constructor injection. No component imports
an agent SDK, adapter, protocol transport, or global service locator.

See the [High-Level Design](../HIGH_LEVEL_DESIGN.md) for architecture, integration levels,
end-to-end flows, and phase scope.
