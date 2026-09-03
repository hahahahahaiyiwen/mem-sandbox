# Snapshot Store Design

**Status:** Implemented in issue #19

## Purpose

The snapshot subsystem transfers immutable sandbox state across sessions.
`SandboxSession` decides when to capture or restore state; the snapshot store persists,
bounds, expires, and retrieves versioned process-local snapshots.

Snapshot storage is separate from the live workspace. Creator and source-session fields
are provenance, not authorization.

`SandboxSnapshotDraft`, `SandboxSnapshot`, the quota/statistics models, and
`InMemorySnapshotStore` are implemented in `mem_sandbox.snapshots`. The store uses one
async lock for atomic quota, expiry, save, load, delete, stats, and purge transitions.

## Responsibilities

- Define snapshot draft, persisted snapshot, reference, provenance, and storage metadata.
- Persist complete snapshots atomically.
- Assign absolute expiration at save time from the store's configured TTL.
- Enforce configured retained count and aggregate payload-byte limits.
- Remove expired records lazily on access and through explicit purge.
- Load immutable snapshots by opaque reference.
- Preserve creator and source-session provenance.
- Keep encoding, compatibility, and workspace restore validation in their owning modules.

## Out of scope

- Authenticating callers or authorizing snapshot references.
- Agent conversation history.
- Workflow engine checkpoints.
- Active command processes.
- Secret leases or resolved values.
- Deciding snapshot timing.
- Implementing workspace mutation.
- Background expiry tasks or timers.
- Durable cross-process persistence.

## Contract

The session creates a draft because expiration belongs to the store:

```python
@dataclass(frozen=True, slots=True)
class SnapshotRef:
    snapshot_id: SnapshotId


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    format_name: str
    payload_bytes: int
    process_local: bool


@dataclass(frozen=True, slots=True)
class SandboxSnapshotDraft:
    snapshot_id: SnapshotId
    schema_version: int
    created_at: datetime
    source_session_id: SessionId
    workspace_revision: Revision
    content_hash: ContentHash
    payload: bytes
    metadata: SnapshotMetadata
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxSnapshot:
    snapshot_id: SnapshotId
    schema_version: int
    created_at: datetime
    expires_at: datetime
    source_session_id: SessionId
    workspace_revision: Revision
    content_hash: ContentHash
    payload: bytes
    metadata: SnapshotMetadata
    created_by: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotStoreLimits:
    max_snapshots: int
    max_total_payload_bytes: int


@dataclass(frozen=True, slots=True)
class SnapshotStoreStats:
    snapshot_count: int
    payload_bytes: int


@dataclass(frozen=True, slots=True)
class SnapshotPurgeResult:
    removed_refs: tuple[SnapshotRef, ...]
    removed_payload_bytes: int


class SnapshotStore(Protocol):
    @property
    def process_local(self) -> bool: ...
    async def save(self, draft: SandboxSnapshotDraft) -> SnapshotRef: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...
    async def delete(self, snapshot_ref: SnapshotRef) -> None: ...
    async def purge_expired(self) -> SnapshotPurgeResult: ...
    async def stats(self) -> SnapshotStoreStats: ...
```

The session-owned store port remains narrower and includes only `process_local`, `save`,
and `load`. Delete, purge, and stats are host/owner operations and do not widen every
session collaborator.

## Expiration ownership

`InMemorySnapshotStore` requires an explicit positive finite default TTL at construction:

```python
InMemorySnapshotStore(
    *,
    default_ttl: timedelta,
    limits: SnapshotStoreLimits,
    clock: Clock,
)
```

There is no library-wide snapshot lifetime. The constructing application chooses the
store's default TTL. The store converts it to one absolute UTC `expires_at` value using
its injected clock during atomic save.

Expiration is based on store acceptance time, not the draft's `created_at`. The latter is
snapshot provenance supplied by the session clock. A snapshot is expired when:

```text
clock.now() >= expires_at
```

The initial contract has one TTL per store and no per-save override. A future override
requires a separate bounded save-options design.

There is no background cleanup task. The store removes expired records:

- before enforcing save quotas;
- when loading an expired reference;
- during explicit `purge_expired()`.

Loading an expired snapshot atomically removes it and raises `SnapshotNotFound`. A second
load has the same result. Expiry is intentionally indistinguishable from absence at the
store boundary.

## Creator provenance

`created_by` is optional exact application-supplied provenance of at most 256 UTF-8
bytes. Direct sessions may save drafts with `created_by=None`. The default
`SandboxService` factory injects a
provenance-decorating session-store wrapper that fills the field from its `OwnerId`
before save.

The shared store preserves this value but never compares it during load or delete.
Applications that require authorization must protect references outside this package or
wrap the service/store boundary.

`source_session_id` remains the session that captured the state. On resume, both
`created_by` and `source_session_id` remain unchanged. New snapshots from the resumed
session use the resumed session's new identity and logical owner provenance.

## Quota accounting

`SnapshotStoreLimits` has positive immutable count and
`max_total_payload_bytes` limits. Byte accounting uses `len(draft.payload)`, never the
untrusted metadata field.

Save holds the store lock across:

1. current-time capture;
2. removal of expired records;
3. duplicate-ID validation;
4. exact prospective count and byte calculation;
5. persisted-snapshot construction with absolute expiry;
6. insertion and counter update.

If either prospective limit would be exceeded, the new draft is rejected atomically with
`SnapshotStoreFull`. Existing snapshots and counters do not change. Accepted records are never evicted to
make room for an unexpired draft.

Deleting or expiring a record decrements both count and payload bytes exactly once.
Expired records do not consume quota during a later save.

`stats()` performs the same lazy expiry pass before returning counts, so reported usage
contains only currently unexpired retained records.

Sessions intentionally receive no snapshot-delete operation. Once a store reaches its
limits, the host must delete references or call `purge_expired()` before additional
unexpired snapshots can be accepted.

The store-wide aggregate byte limit is independent of:

- `JsonSessionSnapshotCodec.max_payload_bytes`, which bounds one session payload;
- workspace snapshot limits, which validate restored workspace shape;
- live workspace total-byte limits.

## Encoding, integrity, and compatibility

`JsonSessionSnapshotCodec` remains the pure deterministic encoder/decoder. It:

- checks its per-payload size limit;
- verifies metadata payload size;
- verifies payload content hash;
- rejects non-canonical or malformed JSON;
- checks session schema and capability versions;
- reconstructs typed workspace, cwd, and approved environment state.

The store does not decode payloads during save or load. It guarantees immutable storage,
expiry, and quota accounting. The session and service decode a loaded snapshot before
workspace mutation. During resume, the service performs session-level decoding before
factory invocation and the factory performs target-workspace preparation before startup.

The workspace remains authoritative for target compatibility. `prepare_restore` verifies
workspace schema, integrity, path rules, file and total bytes, node count, and the
required cwd under the target workspace limits before commit.

## Creation flow

1. Session exports one consistent workspace snapshot.
2. Session combines workspace, cwd, environment, and version state.
3. Codec serializes and hashes the deterministic payload.
4. Session creates `SandboxSnapshotDraft` with identity and creation provenance.
5. Optional service provenance wrapper stamps `created_by`.
6. Store removes expired records and checks prospective quotas.
7. Store assigns absolute `expires_at` and inserts one immutable persisted snapshot.
8. Store returns the unchanged opaque `SnapshotRef`.

The store never changes snapshot identity, `created_at`, payload, hash, source session, or
creator provenance.

## Restore and resume flow

Same-session restore:

1. Session store view loads the reference.
2. Store rejects missing or expired references.
3. Codec verifies integrity and compatibility.
4. Workspace prepares a target-bound candidate.
5. Session commits workspace, cwd, and environment atomically.

Service resume:

1. Service loads the reference selected by the application.
2. Store rejects missing or expired references.
3. Service decodes and validates the complete snapshot.
4. Factory prepares the new target workspace before session startup.
5. Service starts and publishes a new independent session.

Authorization is not inferred from `created_by` or `source_session_id`.

## In-memory concurrency

One async lock serializes save, load-expiry removal, delete, purge, and stats snapshots.
Payload bytes are immutable, so returning a loaded snapshot after the lock is released is
safe even if another task later deletes the store record.

Concurrent operations have deterministic atomic outcomes:

- two saves competing for the final count/byte capacity yield exactly one success;
- load racing delete either returns the immutable accepted snapshot or `SnapshotNotFound`;
- load racing expiry uses the clock value captured under the lock;
- purge racing save removes records expired at its captured time before the save computes
  capacity;
- duplicate identifiers never overwrite an unexpired record.

The injected UUID generator is required to produce a unique snapshot identifier for the
process lifetime. The store still rejects duplicate identifiers while a record is
retained, but it does not keep unbounded tombstones after delete or expiry.

## Failure semantics

Stable snapshot failures are:

- `SnapshotIdentifierConflict`;
- `SnapshotNotFound`;
- `SnapshotCorrupt`;
- `SnapshotTooLarge`;
- `SnapshotIncompatible`;
- `SnapshotStoreFull`;
- `SnapshotSaveFailed`;
- `SnapshotLoadFailed`;
- `SnapshotRestoreFailed`.

Expiry uses `SnapshotNotFound`; no separate `SnapshotExpired` leaks a historical record.
Creator provenance adds no `SnapshotOwnershipDenied` error.

## Behavior-first test matrix

### Models and configuration

- Drafts and persisted snapshots are immutable and accept only immutable `bytes` payloads.
- Drafts require UTC `created_at`; persisted snapshots require UTC `expires_at`.
- Store limits reject booleans, zero, negatives, and unsupported types.
- Store construction requires an explicit positive finite `timedelta` TTL and an injected
  UTC clock.
- `created_by` accepts exactly 256 UTF-8 bytes or `None`, rejects 257 bytes, and performs
  no normalization.
- Persisted references preserve the draft snapshot ID exactly.

### Count and byte quotas

- Exact maximum count and aggregate payload-byte boundaries succeed.
- One-snapshot and one-byte prospective overflow reject only the new draft.
- Overflow leaves retained snapshots, count, bytes, and duplicate-ID behavior unchanged.
- Delete frees exactly one count slot and the actual payload bytes.
- Metadata payload-byte disagreement cannot undercount quota because accounting uses
  `len(payload)`.
- An individually oversized payload retains `SnapshotTooLarge` from the codec; aggregate
  exhaustion uses `SnapshotStoreFull`.
- Concurrent saves for one remaining slot produce one success and one stable quota
  failure.

### Expiry and purge

- Save uses the injected store clock and configured TTL to produce exact absolute
  `expires_at`.
- Store-created `expires_at` is strictly after the acceptance time captured under the
  store lock.
- Load succeeds one clock tick before expiry and fails at the exact expiry boundary.
- Expired load removes the record and decrements count/bytes once.
- Save purges expired records before quota evaluation and can reuse released capacity.
- Explicit purge removes all and only records with `expires_at <= now`.
- Purge returns removed references in deterministic canonical-ID order and exact removed
  payload bytes.
- Repeated purge is idempotent.
- Delete of an already expired or missing reference is idempotent.
- Clock advancement is test-controlled; no sleep or background task is used.
- `stats()` purges expired records before reporting retained count and bytes.

### Integrity and compatibility

- Single-byte payload corruption is detected before workspace preparation.
- Metadata payload length, outer content hash, schema version, workspace revision,
  canonical JSON, environment, and cwd mismatches fail before mutation.
- Unsupported session/capability versions raise `SnapshotIncompatible`.
- Target workspace quota or schema incompatibility fails during prepare and leaves live
  state unchanged.
- Service resume never invokes the factory for corrupt, incompatible, expired, or missing
  snapshots.

### Provenance and forks

- Direct session drafts may persist with no creator provenance.
- Service-created session snapshots preserve exact `OwnerId` provenance through load and
  resume.
- The store never uses creator or source session for access decisions.
- Resume preserves original snapshot provenance and allocates a new live session identity.
- Multiple resumes from one reference are independent forks.
- Deleting a live session does not delete its snapshots.

### Concurrency and atomicity

- Save/delete/load/purge interleavings preserve exact counters.
- Duplicate-ID races accept one immutable record and reject the other.
- Load racing delete has one of the two documented atomic outcomes and never returns
  partial data.
- Purge racing save evaluates both operations under serialized clock snapshots.
- Failed save, load, decode, prepare, or commit never exposes a partial snapshot or live
  workspace state.

## Maintenance rule

Changes to snapshot state, provenance, expiry, quota accounting, integrity,
compatibility, or restore atomicity require updates to this README and exact boundary
tests in the same change.
