# Workspace Design

**Status:** Proposed detailed design under the approved high-level architecture

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

## Interface separation

Avoid one global filesystem interface. Consumers use focused ports:

```python
class WorkspaceReader(Protocol):
    async def stat(self, path: SandboxPath) -> WorkspaceEntry: ...
    async def list(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]: ...
    async def read_bytes(self, path: SandboxPath) -> bytes: ...
    async def read_range(self, request: WorkspaceReadRequest) -> WorkspaceReadResult: ...


class WorkspaceMutator(Protocol):
    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation: ...
    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation: ...
    async def patch(self, request: WorkspacePatchRequest) -> WorkspaceMutation: ...
    async def remove(self, request: RemovePathRequest) -> WorkspaceMutation: ...
    async def copy(self, request: CopyPathRequest) -> WorkspaceMutation: ...
    async def move(self, request: MovePathRequest) -> WorkspaceMutation: ...


class WorkspaceSnapshotCodec(Protocol):
    async def export(self) -> WorkspaceSnapshotData: ...
    async def restore(self, data: WorkspaceSnapshotData) -> None: ...
```

The in-memory workspace may implement all three protocols.

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

### Patch

- Patch validation and application occur against one captured revision.
- Optional `expected_hash` prevents stale updates.
- A failed patch changes nothing.
- Version 1 supports the selected project patch format only; adapters translate if a
  framework uses another format.

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
- maximum patch input bytes

Checks and mutations occur under the same mutation boundary. Policy may deny an operation
earlier, but workspace quotas remain authoritative.

## Revisions and hashes

- Each successful mutation increments a monotonic workspace revision.
- File content hashes use a documented algorithm such as SHA-256.
- Directory listings return a stable lexical order.
- Mutation results include the resulting workspace revision.
- Snapshot metadata records the revision and root hash.

## Concurrency

`SandboxSession` coordinates public operation lanes. The workspace still protects its own
multi-step invariants so direct host-only calls cannot corrupt totals or tree structure.

Version 1 may use one async-compatible mutation lock and immutable byte snapshots for
reads. Lock implementation details must not leak into the public contract.

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
- `StaleContent`
- `InvalidPatch`
- `FileSizeLimitExceeded`
- `WorkspaceSizeLimitExceeded`
- `NodeLimitExceeded`

## Test expectations

- Normalize paths identically on Windows, Linux, and macOS.
- Reject traversal, root deletion, and invalid segments.
- Cover empty files, binary files, mixed line endings, and invalid UTF-8.
- Verify exact one-based line-range boundaries.
- Verify atomic write, patch, copy, move, and restore behavior.
- Verify replacement quota accounting and max-boundary values.
- Verify stable listing order, revisions, and content hashes.
- Run property tests over path normalization and random operation sequences.
- Confirm failed mutations leave tree, counters, and revision unchanged.

## Maintenance rule

Changes to path behavior, node types, encoding, quotas, atomicity, or snapshot encoding
must update this document and the snapshot-store design where applicable.
