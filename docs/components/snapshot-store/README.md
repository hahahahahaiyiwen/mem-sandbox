# Snapshot Store Design

**Status:** Milestone 3 implemented; complete-core extensions remain deferred

## Purpose

The snapshot subsystem transfers sandbox state across sessions. `SandboxSession` decides
when to capture or restore state; the snapshot store persists and retrieves immutable,
versioned snapshots.

Snapshot storage is separate from the live workspace.

Milestone 3 proves immutable process-local persistence, provenance, compatibility, and
exclusive restore. Owner authorization, expiration, and store count/byte quotas remain
in the complete-core milestone.

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
    snapshot_id: SnapshotId


@dataclass(frozen=True)
class SnapshotMetadata:
    format_name: str
    payload_bytes: int
    process_local: bool


@dataclass(frozen=True)
class SandboxSnapshot:
    snapshot_id: SnapshotId
    schema_version: int
    created_at: datetime
    source_session_id: SessionId
    workspace_revision: Revision
    content_hash: ContentHash
    payload: bytes
    metadata: SnapshotMetadata


class SnapshotStore(Protocol):
    @property
    def process_local(self) -> bool: ...

    async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...
    async def delete(self, snapshot_ref: SnapshotRef) -> None: ...
```

`SnapshotId`, `SessionId`, and `Revision` are core domain types. Snapshot codecs convert
them to stable primitive representations only at the serialized envelope boundary.

Listing snapshots is an optional host administration interface and not required by the
session boundary.

`source_session_id` is provenance, not authorization. The Milestone 3 store accepts and
returns opaque references without an owner argument. The future `SandboxService` owns
owner-bound reference authorization before session creation or restore.

## Captured state

Version 1 captures:

- complete workspace tree, bytes, and metadata
- workspace revision and root hash
- current working directory
- approved non-secret environment values
- session schema version `1`
- capability profile version `1`
- the opaque workspace snapshot and its workspace schema version

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

The state content hash covers only deterministic restorable state: workspace snapshot
data, cwd, approved environment, and session/capability versions. It excludes the random
snapshot identifier and creation timestamp, so separate snapshots of identical state
have the same state hash and different references.

`snapshot_id` and `created_at` are outer immutable store-record fields. They are not
encoded inside the deterministic state payload.

The session allocates `snapshot_id` from its injected identifier generator and
`created_at` from its injected UTC clock before calling `save`. The returned
`SnapshotRef.snapshot_id` must equal the saved `SandboxSnapshot.snapshot_id`; the store
does not silently replace identity or time.

`SnapshotMetadata` contains non-authoritative indexing and storage facts only. Cwd,
environment, capability versions, workspace state, and every restore decision are read
from the integrity-protected payload. Any outer field duplicated for indexing, including
schema version or workspace revision, must match the validated payload before restore
proceeds.

Milestone 3 compatibility is exact:

- session snapshot schema version must equal `1`;
- capability profile version must equal `1`;
- the session codec must support the canonical payload format;
- approved environment values must satisfy current environment validation;
- workspace schema, integrity, content size, node count, file size, path limits, and
  snapshot size are delegated to workspace `prepare_restore`, which validates against the
  target workspace's authoritative live limits.

The session snapshot does not duplicate the target workspace's configured limits.
Stricter target limits are permitted only when the complete workspace snapshot validates
under them. Unsupported session or capability versions raise `SnapshotIncompatible`;
workspace compatibility and quota errors retain their workspace categories.

Framework adapters may require a portable tar stream. That conversion belongs in the
adapter or a dedicated snapshot codec and does not redefine the core snapshot format.

## Session snapshot codec

The snapshots module owns the pure deterministic `JsonSessionSnapshotCodec`. It encodes
and decodes `SessionSnapshotState`; it performs no storage, authorization, workspace
mutation, clock access, or identifier generation.

The codec is constructed with a validated `max_payload_bytes` configuration whose
Milestone 3 default is 64 MiB. Encode and decode reject larger complete session payloads
with `SnapshotTooLarge`. This per-payload safety bound is independent of deferred
store-wide byte/count quotas and is not included in the hashed state.

The session consumes it through `SessionSnapshotCodec`:

```python
@dataclass(frozen=True)
class SnapshotPayload:
    payload: bytes
    content_hash: ContentHash
    format_name: str
    schema_version: int
    workspace_revision: Revision


class SessionSnapshotCodec(Protocol):
    def encode(self, state: SessionSnapshotState) -> SnapshotPayload: ...
    def decode(self, snapshot: SandboxSnapshot) -> SessionSnapshotState: ...
```

Decode verifies the canonical payload and `content_hash` before the session asks the
workspace to prepare a restore candidate. The codec owns the payload format name, and the
store owns whether persistence is process-local; the session copies those capabilities
into `SnapshotMetadata` without assuming a concrete implementation.

## Creation flow

1. Session acquires a consistent snapshot boundary.
2. Workspace exports immutable state.
3. Session adds cwd, environment, and version metadata.
4. Snapshot codec serializes and hashes the deterministic state payload.
5. Store writes the complete snapshot atomically.
6. Store returns an opaque reference.
7. Session emits the required generic terminal event for the snapshot-create operation.

Failure before atomic store completion creates no visible snapshot reference.

## Restore flow

1. Service or session loads the reference.
2. Store validates reference and retrieves immutable bytes.
3. Codec verifies hash, size limits, and schema.
4. Session validates feature, option, environment, and cwd compatibility.
5. Workspace prepares an immutable restore candidate and verifies the required cwd exists
   as a directory in the candidate.
6. Session commits the candidate and synchronously assigns cwd/environment without an
   intervening await.
7. Session emits the required generic terminal event for the snapshot-restore operation.

Restore failure leaves the previous live state unchanged. Restoring into a new session is
the future service-level resume behavior; Milestone 3 also proves exclusive same-session
restore.

## In-memory store

The first store uses a mapping from random snapshot reference to immutable snapshot
objects.

Milestone 3 behavior:

- no mutation after save
- atomic insert
- idempotent delete
- source-session provenance preserved
- duplicate identifier rejection

Saving a snapshot whose identifier already exists raises `SnapshotIdentifierConflict`,
a `CONFLICT` error. The store never overwrites an existing immutable record.

The complete-core milestone adds configurable total bytes, snapshot count, expiration,
and owner-bound service or store-wrapper authorization.

Process-local storage does not survive restart. This limitation is explicit in metadata
and documentation.

## Compatibility

- Major schema incompatibility fails with `SnapshotIncompatible`.
- Minor additive metadata may be ignored only when explicitly marked optional.
- Migrations are pure functions from one validated schema to the next.
- Unsupported workspace features fail before restore.
- Restore may use stricter current limits only when the complete snapshot remains valid
  under them; required limits are never silently weakened.

## Failure semantics

Stable errors include:

- `SnapshotIdentifierConflict`
- `SnapshotNotFound`
- `SnapshotCorrupt`
- `SnapshotTooLarge`
- `SnapshotIncompatible`
- `SnapshotStoreFull`
- `SnapshotSaveFailed`
- `SnapshotLoadFailed`
- `SnapshotRestoreFailed`

`SnapshotOwnershipDenied` belongs to the future service authorization boundary rather
than the Milestone 3 store/session contract.

## Test expectations

- Round-trip empty, text, binary, and nested workspaces.
- Preserve cwd, approved environment, metadata, and revision.
- Exclude secret and transient state.
- Detect single-byte corruption.
- Verify failed save creates no reference.
- Verify failed restore leaves live state unchanged.
- Verify deterministic serialization and hash for identical state.
- Verify distinct snapshot identifiers do not change the deterministic state hash.
- Preserve source-session provenance without treating it as authorization.
- Verify prepared restore rejects an invalid cwd before live state changes.
- Verify process-local limitations.

Count/byte boundaries, expiration, ownership authorization, and schema migration matrices
are completed in Milestone 4.

## Maintenance rule

Changes to captured state, encoding, integrity, compatibility, or restore atomicity
require updates to this document and the workspace/session designs.
