# Sandbox Service Design

**Status:** Proposed detailed design under the approved high-level architecture

## Purpose

The sandbox service is the host-facing lifecycle coordinator. It creates, locates,
resumes, expires, and deletes sandbox sessions while keeping framework adapters unaware
of concrete workspace, executor, policy, secret, event, and snapshot implementations.

The service owns lifecycle routing. It does not implement file or command behavior.

## Responsibilities

- Validate create and resume requests.
- Generate opaque sandbox handles and session identifiers.
- Construct each session through constructor injection.
- Maintain the process-local session registry for the in-memory implementation.
- Enforce owner and tenant binding for every handle lookup.
- Resume a new session from a snapshot reference.
- Coordinate close, expiration, and deletion.
- Return domain errors rather than framework-specific exceptions.
- Expose host-only lifecycle operations to adapters and applications.

## Out of scope

- Model-visible tool definitions.
- Filesystem operations.
- Command parsing or execution.
- Policy decisions for individual sandbox operations.
- Snapshot serialization details.
- Agent conversation or workflow state.
- Network transport, MCP, or an HTTP control plane.

## Public contract

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SandboxHandle:
    value: str


@dataclass(frozen=True)
class CreateSandboxRequest:
    owner_id: str
    options: SandboxOptions
    initial_content: WorkspaceSeed | None = None


@dataclass(frozen=True)
class ResumeSandboxRequest:
    owner_id: str
    snapshot_ref: SnapshotRef
    options_override: SandboxOptionsOverride | None = None


class SandboxService(Protocol):
    async def create(self, request: CreateSandboxRequest) -> SandboxHandle: ...
    async def get_session(
        self,
        handle: SandboxHandle,
        *,
        owner_id: str,
    ) -> SandboxSession: ...
    async def resume(self, request: ResumeSandboxRequest) -> SandboxHandle: ...
    async def delete(
        self,
        handle: SandboxHandle,
        *,
        owner_id: str,
    ) -> None: ...
```

Adapters may hold a live `SandboxSession` directly after lookup. Model-visible tools must
not accept arbitrary owner identifiers or handles unless the application explicitly
needs a multi-session tool.

## Construction boundary

The service depends on a narrow session factory owned by this module:

```python
class SessionFactory(Protocol):
    async def create(
        self,
        *,
        identity: SandboxIdentity,
        options: SandboxOptions,
        restored_state: RestoredSandboxState | None,
    ) -> SandboxSession: ...
```

The factory assembles concrete collaborators through constructor injection. The service
must not use a global service locator or import agent SDKs.

## Registry model

The in-memory service stores records shaped conceptually as:

```text
SessionRecord
  handle
  owner_id
  session_id
  session
  created_at
  expires_at
  status
```

Handles are opaque random values. They must not encode owner names, paths, secrets, or
other meaningful data.

## Lifecycle

### Create

1. Validate options and initial content.
2. Allocate identity and handle.
3. Construct a session in `CREATED`.
4. Start the session.
5. Register the session only after startup succeeds.
6. Emit a creation event and return the handle.

Startup failure closes all collaborators in reverse construction order and does not leave
a registry entry.

### Resume

1. Authorize access to the snapshot reference.
2. Load and validate the snapshot.
3. Apply only explicitly permitted option overrides.
4. Construct a new session identity.
5. Restore and start the session.
6. Register and return a new handle.

Resume creates a new live session. It does not revive Python object identity from a prior
process.

### Delete

1. Resolve and authorize the handle.
2. Atomically mark the record as deleting.
3. Close the session.
4. Remove the registry record.
5. Emit the deletion result.

Delete is idempotent for an already-deleted handle only when the caller can prove
ownership; otherwise return `SandboxNotFound`.

## Concurrency and ownership

- Registry operations are safe under concurrent host calls.
- Exactly one logical owner is bound to a session.
- A lookup verifies ownership before returning the session.
- Create and resume never publish partially initialized sessions.
- Delete prevents new lookups before session cleanup begins.
- Session-level operation coordination remains the responsibility of `SandboxSession`.

## Failure semantics

Stable service errors include:

- `InvalidSandboxOptions`
- `SandboxNotFound`
- `SandboxOwnershipDenied`
- `SandboxAlreadyDeleting`
- `SandboxStartupFailed`
- `SnapshotNotFound`
- `SnapshotIncompatible`
- `SandboxResumeFailed`
- `SandboxDeleteFailed`

Errors must preserve a safe cause for host diagnostics without leaking secret values or
untrusted snapshot content.

## Test expectations

- Create returns a usable session only after successful startup.
- Failed construction rolls back every initialized collaborator.
- Wrong-owner lookup and deletion are denied.
- Concurrent create calls produce unique handles and sessions.
- Delete blocks new lookups and closes exactly once.
- Resume restores workspace, cwd, and approved environment state.
- Corrupt or incompatible snapshots never create registry records.
- Expiration follows the same cleanup guarantees as explicit deletion.

## Maintenance rule

Any change to lifecycle ownership, handle semantics, registry state, or session
construction must update this document and the high-level design.
