# Workspace Design

**Status:** Workspace MVP and Milestone 3 prepared restore implemented; later extensions proposed

## Purpose

The workspace is the canonical virtual filesystem. The first implementation stores all
nodes and file bytes in memory and never delegates to the host filesystem.

The workspace owns path semantics, node metadata, content integrity, quotas, and atomic
filesystem mutations.

## Responsibilities

- Normalize POSIX-style paths independently of the host operating system.
- Contain every path within the configured virtual root.
- Store files as arbitrary bytes.
- Provide bounded UTF-8 text reads for model-facing APIs.
- Create, list, inspect, copy, move, and remove files and directories.
- Apply atomic writes and context-aware patches.
- Calculate content hashes and storage statistics.
- Enforce file-size, total-size, and node-count limits atomically.
- Export and restore deterministic workspace state.

## Out of scope

- Shell parsing.
- Policy decisions about whether a caller may perform an operation.
- Secret resolution.
- Agent framework tool schemas.
- Host filesystem projection.
- OS users, processes, sockets, or mount behavior.

## Internal model

```text
Workspace
  root: DirectoryNode
  total_bytes
  node_count
  revision

DirectoryNode
  name
  children
  metadata

FileNode
  name
  content: bytes
  content_hash
  metadata
```

Version 1 does not support symbolic links, hard links, device nodes, sockets, or host
mounts. Requests to create or import them fail with `UnsupportedNodeType`.

Version 1 metadata is deliberately deterministic and minimal:

- node kind
- byte size for files
- content hash for files and directories
- observed workspace revision

It does not include timestamps, permissions, owner, or group.

## Interface separation

Avoid one global filesystem interface. Consumers use focused ports:

```python
class WorkspaceReader(Protocol):
    def resolve_path(
        self,
        value: str,
        *,
        cwd: SandboxPath | None = None,
    ) -> SandboxPath: ...
    async def stats(self) -> WorkspaceStats: ...
    async def stat(self, path: SandboxPath) -> WorkspaceEntry: ...
    async def list(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]: ...
    async def read_bytes(self, path: SandboxPath) -> WorkspaceBinaryResult: ...
    async def read_range(self, request: WorkspaceRangeRequest) -> WorkspaceRangeResult: ...


class WorkspaceMutator(Protocol):
    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation: ...
    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation: ...
    async def append(self, request: WorkspaceAppendRequest) -> WorkspaceMutation: ...
    async def patch(self, request: WorkspacePatchRequest) -> WorkspacePatchResult: ...
    async def remove(self, request: RemovePathRequest) -> WorkspaceMutation: ...
    async def copy(self, request: CopyPathRequest) -> WorkspaceMutation: ...
    async def move(self, request: MovePathRequest) -> WorkspaceMutation: ...


class WorkspaceSnapshotPort(Protocol):
    async def export(self) -> WorkspaceSnapshotData: ...
    async def prepare_restore(
        self,
        data: WorkspaceSnapshotData,
        *,
        required_directory: SandboxPath,
    ) -> PreparedWorkspaceRestore: ...
    async def commit_restore(self, candidate: PreparedWorkspaceRestore) -> None: ...
    async def restore(self, data: WorkspaceSnapshotData) -> None: ...
```

The in-memory workspace may implement all three protocols.

`PreparedWorkspaceRestore` is an immutable, opaque candidate owned by the workspace
module. Preparation decodes and verifies the complete tree, counters, hashes, limits,
schema, and required directory without mutating live state. The candidate is bound to
the workspace instance that prepared it and cannot be committed to another workspace.

`commit_restore` publishes one validated candidate under the workspace state lock. It must
remain cancellation-cooperative while waiting to publish; after publication it returns
without another suspension point. This lets the session cancel a restore that exhausts
its collaborator budget before any live state changes.
`restore(data)` remains a convenience operation equivalent to
`prepare_restore(data, required_directory=SandboxPath.root())` plus commit.
`SandboxSession` uses prepare/commit with its restored cwd so cwd validity is proven
before workspace and session state are published.

Preparing or committing a malformed candidate raises `PreparedRestoreInvalid`, an
`INVALID_REQUEST` error. Committing a candidate prepared by another workspace instance
raises `RestoreCandidateMismatch`, a `CONFLICT` error. Both fail before live state
publication.

## Path model

- Paths use `/` separators on every host platform.
- The default root is `/workspace`.
- Relative paths resolve against an explicit working directory supplied by the caller.
- `.` is normalized.
- `..` is rejected when supplied by an untrusted agent request, even if normalization
  would remain under root.
- Empty paths, NUL characters, and invalid path segments are rejected.
- Comparisons are case-sensitive.
- A normalized path is represented by a domain `SandboxPath`, not a host `pathlib.Path`.

Path confinement is a workspace invariant, not only a policy rule.

## Binary and text APIs

Files are stored as bytes. Text helpers decode UTF-8 explicitly and fail with
`FileEncodingError` for invalid text.

The model-facing range read uses:

- one-based inclusive line numbers
- `start_line` defaulting to 1
- an optional inclusive `end_line`
- a configured maximum lines and bytes per response
- returned `total_lines`, actual range, and content hash

The range result is materialized at one revision. It is not a lazy cursor over mutable
state.

## Mutation behavior

### Write

- Parent creation is explicit in the internal request.
- Limit validation occurs before mutation.
- Replacement accounts for the previous file size.
- The content becomes visible atomically.
- The result includes old and new content hashes.
- Every write supplies one explicit precondition:
  - `AnyCurrentState` for an intentional unconditional create or replacement
  - `PathMustNotExist` for create-only behavior
  - `ContentHashMustEqual` for compare-and-swap replacement
- `ContentHashMustEqual` compares the current SHA-256 content hash while holding the
  workspace lock and returns `StaleContent` on mismatch.
- The caller sends the observed hash rather than resending the complete original file.

### Append

- Append is one workspace-owned atomic mutation; callers never implement append as a
  read followed by write.
- The request uses the same explicit current-state preconditions as write.
- A missing destination is created when its precondition permits creation.
- File-size and total-workspace quotas are checked against the complete appended content
  before publication.
- Directory targets, stale hashes, and quota failures leave content, counters, hashes,
  and revision unchanged.
- A successful append increments the workspace revision exactly once and returns the
  previous and current SHA-256 hashes.

### Patch

- Patch validation and application occur against one captured revision.
- Optional `expected_hash` prevents stale updates.
- A failed patch changes nothing.
- Version 1 uses a constrained UTF-8 unified-diff format with `---`, `+++`, and `@@`
  headers, context, removal, and addition lines.
- Multiple file sections are applied atomically in one workspace mutation.
- File creation, deletion, rename, binary patches, and special-file patches are not part
  of the version 1 patch format.
- Patched text is normalized to LF so results are host-independent.
- Adapters translate if a framework uses another patch format.

### Copy and move

- Source and destination are validated before mutation.
- Directory operations precompute quota effects.
- Failure does not leave a partial destination.
- Moving a directory into itself is rejected.

### Remove

- Removing a non-empty directory requires `recursive=True`.
- Root removal is never allowed.
- Removing a missing path is an error unless the request explicitly permits idempotence.

## Quotas

Required limits:

- maximum bytes per file
- maximum total workspace bytes
- maximum node count
- maximum path length
- maximum segment length
- maximum read response bytes
- maximum lines per range read
- maximum patch input bytes
- maximum encoded snapshot bytes

Checks and mutations occur under the same mutation boundary. Policy may deny an operation
earlier, but workspace quotas remain authoritative.

The default profile is configurable and starts with:

| Limit | Default |
|---|---:|
| File bytes | 4 MiB |
| Total workspace bytes | 16 MiB |
| Node count, including root | 10,000 |
| Normalized path bytes | 4,096 |
| Path-segment bytes | 255 |
| Model-facing range response | 256 KiB |
| Lines per range response | 2,000 |
| Patch input | 1 MiB |
| Encoded snapshot | 32 MiB |

## Revisions and hashes

- Each successful mutation increments a monotonic workspace revision.
- File content hashes use a documented algorithm such as SHA-256.
- Directory listings return a stable lexical order.
- Mutation results include the resulting workspace revision.
- Snapshot metadata records the revision and root hash.
- Restore reinstates the revision recorded by the snapshot; subsequent mutations advance
  from that restored revision. Revisions are therefore monotonic between restores, while
  restore intentionally rewinds the complete workspace state.

File hashes are SHA-256 over their exact bytes. Directory hashes use a domain-separated
SHA-256 sequence over each lexically ordered child's node kind, UTF-8 name, and content
hash. Host metadata and insertion order never participate.

## Consistency and concurrency

One workspace method call is the version 1 transaction boundary. The workspace provides
strict serializability and linearizability at that boundary:

- Every operation appears to take effect atomically at one point between invocation and
  completion.
- Operations that do not overlap respect their real-time order.
- Reads materialize their complete result from one committed revision.
- Mutations never expose partially updated nodes, counters, hashes, or revisions.
- Separate method calls do not form a transaction and do not receive repeatable-read
  guarantees across calls.

`SandboxSession` serializes complete public operations before invoking the workspace.
The workspace independently protects its invariants so direct component calls cannot
corrupt state. Independent sessions own independent workspaces and may execute
concurrently.

Version 1 uses one async-compatible workspace state lock. Every read, mutation, export,
and restore acquires that lock. A mutation holds it while validating the current state,
checking quotas and stale-content preconditions, constructing private candidate state,
and publishing the complete candidate through one committed-state replacement. The
revision increments exactly once at that replacement.

This is equivalent to strict two-phase locking at coarse workspace granularity, but
there are no object- or file-level locks, lock upgrades, or release-and-reacquire commit
sequence. The workspace does not implement MVCC, retain addressable committed versions,
or automatically retry conflicts.

An optional `expected_hash` is an API precondition for detecting stale content. It is
checked while holding the workspace lock and returns `StaleContent` on mismatch. It is
not an internal optimistic-concurrency scheduler.

Snapshot export may copy a complete immutable snapshot value while holding the lock and
encode that captured value after releasing it. The export remains a point-in-time image
of its recorded revision. Restore validates private candidate state before publishing it
atomically under the lock and is globally exclusive when invoked through
`SandboxSession`.

The version 1 codec uses canonical UTF-8 JSON with sorted entries, base64 file content,
and a SHA-256 integrity digest over the canonical payload. Decoding is bounded before
allocation, rejects duplicate fields and malformed base64, recomputes all counters and
hashes, and never uses pickle or executable object deserialization.

The required lock order is:

```text
SandboxSession operation gate
  -> Workspace state lock
```

The workspace never calls back into the session while holding its state lock. Future
versions may allow concurrent reads from immutable committed state, but that optimization
must preserve the same observable per-operation guarantees.

## Failure semantics

Stable workspace errors include:

- `InvalidPath`
- `PathOutsideWorkspace`
- `PathNotFound`
- `PathAlreadyExists`
- `NotAFile`
- `NotADirectory`
- `DirectoryNotEmpty`
- `UnsupportedNodeType`
- `FileEncodingError`
- `InvalidRange`
- `ReadLimitExceeded`
- `StaleContent`
- `InvalidPatch`
- `PatchContextMismatch`
- `FileSizeLimitExceeded`
- `WorkspaceSizeLimitExceeded`
- `NodeLimitExceeded`
- `SnapshotTooLarge`
- `SnapshotCorrupt`
- `SnapshotIncompatible`

## Test expectations

- Normalize paths identically on Windows, Linux, and macOS.
- Reject traversal, root deletion, and invalid segments.
- Cover empty files, binary files, mixed line endings, and invalid UTF-8.
- Verify exact one-based line-range boundaries.
- Verify atomic write, append, patch, copy, move, and restore behavior.
- Verify replacement quota accounting and max-boundary values.
- Verify stable listing order, revisions, and content hashes.
- Run property tests over path normalization and random operation sequences.
- Confirm failed mutations leave tree, counters, and revision unchanged.
- Confirm concurrent callers are serialized, cancelled lock waiters do not mutate state,
  and independent workspaces do not block one another.

## Deferred extensions

[Workspace content offload](./content-offload/README.md) explores keeping the logical
workspace tree in memory while optionally storing immutable file bytes through an
external provider. It remains deferred until the post-Milestone 5 scalability review.

## Maintenance rule

Changes to path behavior, node types, encoding, quotas, atomicity, or snapshot encoding
must update this document and the snapshot-store design where applicable.
