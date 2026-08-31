# Sandbox Session Design

**Status:** Proposed detailed design under the approved high-level architecture

## Purpose

`SandboxSession` is the central application-facing facade. It exposes the small agent
operation surface, owns lifecycle and operation ordering, and coordinates the workspace,
command executor, policy engine, secret broker, event sink, and snapshot store.

The session contains orchestration logic. It does not contain concrete infrastructure.

## Responsibilities

- Expose agent-facing execute, bounded read, write, and patch operations.
- Expose host-only binary, directory, snapshot, and lifecycle operations.
- Enforce the session state machine.
- Normalize requests and attach session identity.
- Ask policy before invoking a collaborator.
- Coordinate locks, timeout, and cancellation.
- Resolve secrets only for approved operations.
- Emit structured start, decision, completion, and failure events.
- Create consistent snapshots and restore them exclusively.
- Surface stable domain results and errors.

## Public operation surface

```python
class SandboxSession:
    async def execute(self, request: ExecuteRequest) -> ExecuteResult: ...
    async def read_file(self, request: ReadFileRequest) -> ReadFileResult: ...
    async def write_file(self, request: WriteFileRequest) -> FileMutationResult: ...
    async def apply_patch(self, request: ApplyPatchRequest) -> FileMutationResult: ...
```

Host-only methods may include:

```python
async def read_bytes(request: ReadBytesRequest) -> ReadBytesResult: ...
async def write_bytes(request: WriteBytesRequest) -> FileMutationResult: ...
async def list_entries(request: ListEntriesRequest) -> ListEntriesResult: ...
async def create_snapshot(request: CreateSnapshotRequest) -> SnapshotRef: ...
async def restore_snapshot(request: RestoreSnapshotRequest) -> None: ...
async def close() -> None: ...
```

Adapters decide which methods become model-visible tools.

## Collaborator ports

The session module owns the narrow ports it consumes:

```python
class WorkspaceReader(Protocol):
    async def read_range(self, request: WorkspaceReadRequest) -> WorkspaceReadResult: ...
    async def read_bytes(self, path: SandboxPath) -> bytes: ...
    async def list_entries(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]: ...


class WorkspaceMutator(Protocol):
    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation: ...
    async def patch(self, request: WorkspacePatchRequest) -> WorkspaceMutation: ...


class SessionCommandExecutor(Protocol):
    async def execute(
        self,
        request: ExecuteRequest,
        context: ExecutionContext,
    ) -> ExecuteResult: ...


class SessionPolicyEngine(Protocol):
    async def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...


class SessionSecretBroker(Protocol):
    async def lease(self, request: SecretAccessRequest) -> SecretLease: ...


class SessionEventSink(Protocol):
    async def emit(self, event: SandboxEvent) -> None: ...


class SessionSnapshotStore(Protocol):
    async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...
```

Concrete components may implement several compatible protocols, but the session must not
depend on one broad infrastructure interface.

## Operation pipeline

Every agent-facing operation follows the same ordered pipeline:

1. Validate request shape and limits that do not depend on mutable session state.
2. Acquire the session-wide operation gate.
3. Reject calls when the session state does not permit the operation.
4. Normalize paths and command metadata.
5. Create an operation identifier and emit `operation.started`.
6. Ask the policy engine for an allow or deny decision.
7. Resolve approved secret references, if any.
8. Invoke the workspace or command executor.
9. Commit shell/session state changes only after successful execution.
10. Emit a completion or failure event.
11. Release secret leases and the operation gate.
12. Return a domain result or raise a stable domain error.

Policy denial occurs before secret resolution and before workspace mutation.

## State machine

```text
CREATED
  -> STARTING
  -> RUNNING
  -> STOPPING
  -> STOPPED

STARTING, RUNNING, or STOPPING
  -> FAILED
```

Rules:

- Agent operations require `RUNNING`.
- `close()` is idempotent.
- `FAILED` rejects new operations but still permits cleanup.
- Snapshot restore is allowed only through an exclusive host operation.
- Resume constructs a new session from restored state rather than mutating a stopped
  instance from another process.

  Two sessions resumed from the same snapshot are independent forks. They do not share a
  live workspace, automatically merge changes, or report conflicts with one another.
  Multiple callers that attach to the same live session share its operation gate and
  workspace.

## Concurrency model

The first version permits one active public operation per session. Every public
operation acquires one session-wide async operation gate and holds it through policy
approval, collaborator invocation, state commit, and completion or failure event
ordering.

| Operation | Lane |
|---|---|
| Execute | Session-wide exclusive |
| Read file/range | Session-wide exclusive |
| Write file | Session-wide exclusive |
| Apply patch | Session-wide exclusive |
| Create snapshot | Session-wide exclusive |
| Restore snapshot | Session-wide exclusive |
| Close | Session-wide exclusive |

There is no true operation concurrency inside one session in version 1. Independent
sessions may execute concurrently because each owns its own operation gate and workspace.
The session guarantee avoids a need for object- or file-level locks or MVCC in the
workspace.

The workspace still owns a coarse state lock as a separate invariant boundary. The lock
order is always the session operation gate followed by the workspace state lock; the
workspace never calls back into the session. Expected content hashes remain optional
request preconditions for stale-write detection, not an internal optimistic-concurrency
mechanism.

Later optimization may introduce shared read lanes or revision-based immutable reads,
but it must preserve defined results, real-time operation ordering, and event ordering.

Timeout or cancellation must not leave a command mutating state after the operation has
returned. Non-cooperative executors are rejected or quarantined until completion.

## Session-owned state

The session owns:

- session and owner identity
- lifecycle status
- operation sequence number
- current working directory
- approved environment variables
- capability profile
- creation and expiration metadata

File content belongs to the workspace. Persisted snapshot bytes belong to the snapshot
store. Secret values never become session state.

## Snapshot behavior

Snapshot creation captures a consistent combination of:

- workspace state
- current working directory
- approved non-secret environment state
- schema and feature versions
- configured limits needed for safe restore

Snapshot creation does not capture active operations, secret leases, framework tool
objects, or agent conversation state.

## Failure semantics

The session never returns success-shaped fallback values after collaborator failure.
Errors retain the operation ID and stable category. If both an operation and cleanup fail,
the primary operation error remains primary and cleanup details are attached safely.

## Test expectations

- Happy path for each of the four agent operations.
- Policy-denied operations invoke no workspace, executor, or secret collaborator.
- Dependency failures are surfaced with stable categories.
- Timeout and cancellation produce no later mutation.
- Concurrent calls on one session are serialized deterministically.
- Independent sessions can make progress concurrently.
- Cancellation while waiting for the operation gate performs no collaborator call or
  mutation.
- Reads honor range boundaries and content hashes.
- Stale expected hashes reject writes and patches.
- Snapshot creation is point-in-time consistent.
- Restore excludes concurrent operations and reestablishes cwd/environment.
- Close is idempotent and disposes collaborators in a defined order.

## Maintenance rule

Changes to operation ordering, lifecycle, concurrency, snapshot composition, or any
session-owned port require an update to this document.
