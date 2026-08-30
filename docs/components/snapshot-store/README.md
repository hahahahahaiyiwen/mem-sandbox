# Snapshot Store Design

**Status:** Proposed detailed design under the approved high-level architecture

## Purpose

The snapshot subsystem transfers sandbox state across sessions. `SandboxSession` decides
when to capture or restore state; the snapshot store persists and retrieves immutable,
versioned snapshots.

Snapshot storage is separate from the live workspace.

## Responsibilities

- Define the snapshot envelope and metadata.
- Persist complete snapshots atomically.
- Load and validate snapshots by opaque reference.
- Verify schema compatibility and content integrity.
- Support process-local in-memory storage first.
- Permit future local-file or remote stores through the same narrow contract.
- Exclude transient and secret state.

## Out of scope

- Agent conversation history.
- Workflow engine checkpoints.
- Active command processes.
- Secret leases or resolved values.
- Deciding snapshot timing.
- Implementing workspace mutation.

## Contract

```python
@dataclass(frozen=True)
class SnapshotRef:
    value: str


@dataclass(frozen=True)
class SandboxSnapshot:
    snapshot_id: str
    schema_version: int
    created_at: datetime
    source_session_id: str
    workspace_revision: int
    content_hash: str
    payload: bytes
    metadata: SnapshotMetadata


class SnapshotStore(Protocol):
    async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...
    async def delete(self, snapshot_ref: SnapshotRef) -> None: ...
```

Listing snapshots is an optional host administration interface and not required by the
session boundary.

## Captured state

Version 1 captures:

- complete workspace tree, bytes, and metadata
- workspace revision and root hash
- current working directory
- approved non-secret environment values
- capability and format version identifiers
- limits required to validate safe restore

Version 1 does not capture:

- command history
- active operations
- framework agents or tool objects
- policy service instances
- event sink buffers
- resolved secrets or leases
- owner authentication tokens

## Encoding

The snapshot envelope is schema-versioned and deterministic. The exact payload format may
be a compact binary or canonical structured encoding, but it must provide:

- deterministic serialization for the same state
- integrity verification
- explicit schema and feature versions
- bounded decoding
- no executable object deserialization
- no dependency on Python pickle

Framework adapters may require a portable tar stream. That conversion belongs in the
adapter or a dedicated snapshot codec and does not redefine the core snapshot format.

## Creation flow

1. Session acquires a consistent snapshot boundary.
2. Workspace exports immutable state.
3. Session adds cwd, environment, and version metadata.
4. Snapshot codec serializes and hashes the payload.
5. Store writes the complete snapshot atomically.
6. Store returns an opaque reference.
7. Session emits `snapshot.created`.

Failure before atomic store completion creates no visible snapshot reference.

## Restore flow

1. Service or session loads the reference.
2. Store validates reference and retrieves immutable bytes.
3. Codec verifies hash, size limits, and schema.
4. Session validates feature and option compatibility.
5. Workspace restores into a new or exclusively held instance.
6. Session restores cwd and approved environment state.
7. Session emits `snapshot.restored`.

Restore failure leaves the previous live state unchanged. Restoring into a new session is
preferred because rollback is simpler.

## In-memory store

The first store uses a mapping from random snapshot reference to immutable snapshot
objects.

Required behavior:

- configurable total bytes and snapshot count
- no mutation after save
- atomic insert
- idempotent delete
- optional expiration
- owner binding enforced by the service or store wrapper

Process-local storage does not survive restart. This limitation is explicit in metadata
and documentation.

## Compatibility

- Major schema incompatibility fails with `SnapshotIncompatible`.
- Minor additive metadata may be ignored only when explicitly marked optional.
- Migrations are pure functions from one validated schema to the next.
- Unsupported workspace features fail before restore.
- Restored limits may become stricter but never silently weaker than required by the
  snapshot.

## Failure semantics

Stable errors include:

- `SnapshotNotFound`
- `SnapshotOwnershipDenied`
- `SnapshotCorrupt`
- `SnapshotTooLarge`
- `SnapshotIncompatible`
- `SnapshotStoreFull`
- `SnapshotSaveFailed`
- `SnapshotLoadFailed`
- `SnapshotRestoreFailed`

## Test expectations

- Round-trip empty, text, binary, and nested workspaces.
- Preserve cwd, approved environment, metadata, and revision.
- Exclude secret and transient state.
- Detect single-byte corruption.
- Cover exact store count and byte boundaries.
- Verify failed save creates no reference.
- Verify failed restore leaves live state unchanged.
- Verify deterministic serialization and hash for identical state.
- Cover every supported schema migration.
- Verify process-local limitations and expiration behavior.

## Maintenance rule

Changes to captured state, encoding, integrity, compatibility, or restore atomicity
require updates to this document and the workspace/session designs.
