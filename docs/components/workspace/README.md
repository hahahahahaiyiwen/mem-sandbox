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


class WorkspaceArchivePort(Protocol):
    async def export_portable_archive(self) -> WorkspaceArchiveData: ...
    async def prepare_archive_restore(
        self,
        data: WorkspaceArchiveData,
        *,
        required_directory: SandboxPath,
    ) -> PreparedWorkspaceRestore: ...
```

The in-memory workspace may implement all four protocols. Both restore ports publish
through the same `commit_restore` operation and opaque `PreparedWorkspaceRestore`.

Mutation methods remain cancellation-cooperative while waiting for the workspace lock.
After publishing a new state they return the corresponding mutation result without
another suspension point. Session orchestration relies on this boundary so a completed
mutation cannot subsequently be reported as cancelled or timed out.

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
- `SandboxPath.join()` accepts the same path and segment limits as resolution, and
  workspace-internal traversal always supplies the active `WorkspaceLimits` rather than
  silently reapplying defaults.

Path confinement is a workspace invariant, not only a policy rule.
The general POSIX workspace can contain a literal backslash or a drive-like segment.
Portable archive export rejects those otherwise-valid names before emitting bytes because
the version 1 interchange profile deliberately excludes Windows-ambiguous syntax.

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

### Session directory operations

Issue #31 exposes workspace directory creation and path removal through public
`SandboxSession.create_directory` and `SandboxSession.remove_path` methods:

- the session normalizes the path, performs admission and operation orchestration, and
  delegates one `MakeDirectoryRequest` or `RemovePathRequest`;
- `MakeDirectoryRequest.exist_ok=True` makes an existing directory, including root, an
  unchanged success with the same previous/current directory hash and no revision
  increment;
- `exist_ok=False` remains strict for existing directories; `exist_ok=True` does not
  permit replacing an existing file, which still raises `PathAlreadyExistsError`, and
  `create_parents=False` continues to reject a missing parent;
- `MemoryWorkspace` remains the sole owner of parent creation, recursive removal,
  missing-path behavior, root protection, quotas, hashes, revisions, locking, and atomic
  publication;
- the session maps `WorkspaceMutation` into session operation metadata without applying
  another mutation or revision increment;
- command-language `mkdir -p` maps to `create_parents=True, exist_ok=True`, while `rm`
  continues delegating to the same removal request, so native adapter calls and commands
  cannot drift semantically;
- session-native calls never invoke the command parser, host filesystem, or host process.

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
| Encoded snapshot, raw archive input, or expanded tar stream | 32 MiB |

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

## Portable archive boundary

OpenAI workspace persistence uses a separate portable archive codec owned by the
workspace boundary. The adapter supplies and consumes bounded byte streams; it must not
implement tar parsing, path security, workspace limit checks, or restore atomicity.

### Codec-neutral tree model

`WorkspaceSnapshotEntry` and the decoded tree result are codec-neutral workspace models,
not types owned by the JSON or tar implementation. The decoded tree result contains
entries plus measured bytes, root-inclusive node count, and root hash; it does not invent
a workspace revision. Entry construction validates `NodeKind` at runtime, and codecs
branch exhaustively over file and directory kinds so invalid values cannot silently
change node type or discard content.

The public archive value is immutable:

```python
@dataclass(frozen=True, slots=True)
class WorkspaceArchiveData:
    encoded: bytes
    format_version: int
    workspace_revision: Revision
    root_hash: ContentHash
```

Only `encoded` is written to the OpenAI snapshot stream. The format version, revision,
and root hash are copied into serializable provider session state and supplied again when
hydrating. A snapshot stream without matching provider metadata is unsupported in version
1. This preserves exact revision behavior without making tar bytes depend on revision.

The decoder recomputes the root hash and compares it with the out-of-band value before it
can create a restore candidate. A missing, incompatible, or mismatched format version or
root hash fails before publication.

### Portable tar profile version 1

Canonical export is an uncompressed POSIX PAX tar stream:

- effective member names are UTF-8 workspace-relative paths without a leading `/` or
  `./`; directory headers may carry the conventional single trailing `/` emitted by the
  standard tar writer;
- the root itself is not emitted;
- every directory, including an empty directory, is emitted explicitly;
- members are ordered by canonical `SandboxPath.value`;
- regular files use mode `0644` and directories use `0755`;
- uid, gid, mtime, device numbers, user name, and group name are normalized to zero or
  empty values;
- file bytes are exact and no permission, timestamp, owner, xattr, ACL, or host metadata
  affects workspace identity;
- PAX `path` records support UTF-8 and paths beyond USTAR limits.

The golden archive digest in the unit tests is the cross-platform contract. A Python or
dependency upgrade that changes those bytes requires an explicit archive format-version
decision rather than silently changing persisted snapshots.

Hydration accepts uncompressed PAX-compatible tar or gzip-compressed tar. Bzip2, xz,
zstd, and other compression profiles are rejected as incompatible in version 1. A valid
uncompressed tar header takes precedence over compression-like leading filename bytes,
so names such as `BZh-file` remain portable tar members. A root directory marker named
`.` or `./` is ignored, and one leading `./` on another member is accepted for
compatibility with common tar producers. At most one root marker is accepted; it must be
an empty directory and counts toward the archive member limit without counting as a
workspace node. Other member names must already be canonical POSIX-relative paths.
Missing parent directories are synthesized; explicit empty directories remain
preserved.

Only regular files and directories are supported. Import rejects absolute paths,
traversal, Windows drive paths and separators, NULs, repeated separators, interior `.`
segments, duplicate canonical paths, file/directory conflicts, links, devices, FIFOs,
contiguous files, sparse members, malformed headers, and unsupported extensions or node
types. A complete stream must end with two 512-byte zero blocks followed only by
zero-filled block padding; each physical member payload must also use zero-filled padding
through its final 512-byte block. Missing terminators, partial blocks, concatenated
archives, and non-zero padding or trailing data are malformed. Raw USTAR/PAX header names
and PAX `path` values are validated independently before directory-name normalization, so
an effective PAX path cannot mask an unsafe fallback header. Exactly one trailing
separator is removed only for a directory; GNU long-name and long-link extensions are
unsupported because version 1 uses PAX for extended paths. A bounded physical-header scan
runs before `tarfile` processing, permits at most one global PAX header and one local PAX
header per member, caps total PAX records at `max_nodes`, validates every PAX `path`
record, and rejects stacked extension chains. PAX path byte and segment limits are
checked before UTF-8 string or component materialization. Other PAX metadata is ignored
except where tar parsing supplies the effective member path; `size` overrides and sparse
PAX metadata are unsupported because they alter physical payload framing.

Every canonical component is checked independently for Windows drive syntax. POSIX USTAR
prefixes are included in the raw path; non-POSIX and GNU headers with non-empty prefix
regions are rejected so a PAX override cannot mask parser-specific fallback semantics.
PAX `.` and `./` root markers remain valid when `max_path_bytes` is exactly the UTF-8
length of `/workspace`.

### Limits, errors, and memory behavior

The codec accepts immutable bytes. The OpenAI adapter bounded-reads an `IOBase` into bytes
using its `max_stream_bytes`; the workspace codec independently rechecks authoritative
workspace limits. It never owns or closes the SDK stream.

`max_snapshot_bytes` bounds each of:

- raw uncompressed or gzip-compressed input;
- the expanded tar byte stream, preventing compressed metadata/header bombs;
- canonical encoded archive output;
- cumulative UTF-8 bytes retained by canonical decoded member and synthesized-parent
  paths, preventing a compact deep member from amplifying into quadratic path metadata.

`max_total_bytes`, `max_file_bytes`, `max_nodes`, `max_path_bytes`, and
`max_segment_bytes` then bound decoded workspace state. `max_nodes` also caps logical
archive members, including the optional root marker, while the restored tree separately
retains its root-inclusive node limit. Checks are incremental and occur before retaining
an over-limit member. The decoder iterates members and file payloads without `extract`,
`extractall`, temporary files, host paths, or host filesystem access.
Canonical export writes through a bounded in-memory sink and fails before retaining a
write that would cross `max_snapshot_bytes`.

Malformed or unsafe input raises `SnapshotCorruptError`; a recognized but unsupported
format or compression profile raises `SnapshotIncompatibleError`; every raw, expanded,
file, workspace, member, path, or segment limit failure raises
`SnapshotTooLargeError`.

### Atomicity, cancellation, and OpenAI resume

Hydration follows the prepared-restore model: decode and validate a complete immutable
candidate first, verify required directories against that candidate, then publish the
candidate under the workspace state lock. Any invalid archive or failed candidate check
leaves live contents, counters, revision, and root hash unchanged.

A prepared candidate is a one-use workspace capability and exposes no state accessor.
The wrapper carries only an unforgeable key into a workspace-owned weak registry; it
retains no prepared graph or metadata. Copies share the same key, commit removes the
registry record once, and abandoned wrappers allow their records to be collected.
Reconstructing or editing a wrapper cannot repopulate consumed state. Commit explicitly
requires a directory root, iteratively clones and revalidates the graph, and publishes
only the detached state. Mutation of a leaked or forged candidate graph therefore either
fails revalidation before publication or cannot alias the committed live workspace.
Clone tracks directory identities, rejects cycles or shared-directory aliases, and
enforces `max_nodes` before retaining an oversized detached graph.
Expected counters, root hash, and revision are sealed separately from the candidate graph;
commit validates both copies and reconstructs with a fresh `Revision` value that is not
shared with the candidate across the final cancellation checkpoint.

The pure codec performs no mutation. Archive preparation has cooperative cancellation
checkpoints before decoding, after decoding, and before publication. Waiting to acquire
the state lock remains cancellable; after state replacement, commit returns without
another suspension point. Cancellation or timeout before that replacement cannot publish
the candidate, and all in-memory tar/gzip readers are closed through scoped context
managers.

Commit also yields before candidate consumption and again after detached validation.
Pending cancellation or an expired timeout is therefore observed before publication even
when the state lock is uncontended.

Tree hashing, state measurement, snapshot enumeration, and copy-on-write cloning use
iterative traversals. A valid tree may therefore use the full configured path and node
limits without depending on the Python interpreter recursion limit.

The OpenAI SDK's default snapshot resume clears the workspace before calling
`hydrate_workspace`. The in-memory adapter must override that pre-clear operation and let
the workspace archive port perform the single atomic replacement. Otherwise invalid
snapshot input would destroy the prior live state before core validation.

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
- Run the bounded Hypothesis path and workspace state machines defined in
  [Property and Stateful Test Design](../../../tests/property/README.md); do not use
  custom random operation loops or production-private state as the oracle.
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
