# Sandbox Session Design

**Status:** Core lifecycle and issue #20 secret execution behavior implemented; issue #31
native directory operation contract designed and under test

## Purpose

`SandboxSession` is the central application-facing facade. It exposes the small agent
operation surface, owns lifecycle and operation ordering, and coordinates the workspace,
command executor, minimal policy-admission seam, secret broker, event sink, and snapshot
store.

The session contains orchestration logic. It does not contain concrete infrastructure.

## Responsibilities

- Expose agent-facing execute, bounded read, write, and patch operations.
- Expose host-only binary, directory, snapshot, and lifecycle operations.
- Enforce the session state machine.
- Normalize requests and attach session identity.
- Require an explicit admission decision before invoking a protected operation
  collaborator.
- Coordinate locks, timeout, and cancellation.
- Resolve secrets only for approved operations.
- Emit the approved structured lifecycle, operation-start, and terminal events.
- Create consistent snapshots and restore them exclusively.
- Surface stable domain results and errors.

## Public operation surface

```python
class SandboxSession:
    async def execute(
        self,
        request: SessionExecuteRequest,
    ) -> SessionExecuteResult: ...
    async def read_file(self, request: ReadFileRequest) -> ReadFileResult: ...
    async def write_file(self, request: WriteFileRequest) -> FileMutationResult: ...
    async def apply_patch(self, request: ApplyPatchRequest) -> PatchMutationResult: ...
```

Session-owned request and result types add operation identity, timing, and revision
metadata while composing existing workspace and command-executor domain values. The
`SessionExecuteRequest` and `SessionExecuteResult` names intentionally avoid ambiguity
with the command executor's already-public `ExecuteRequest` and `ExecuteResult`.

Public session requests do not contain `OperationRequestMetadata`, `session_id`, or
`operation_id`. The live session supplies its own identity and allocates the operation ID
only after gate admission. Internal policy, event, and collaborator values carry that
identity after allocation without requiring them to embed `OperationRequestMetadata`.

Every operation request contains:

```python
@dataclass(frozen=True, kw_only=True)
class OperationLimits:
    timeout_seconds: float = 30.0
    terminal_event_reserve_seconds: float = 1.0
```

Both values are positive and finite, and the terminal reserve must be strictly less than
the total timeout. `SessionExecuteRequest` also carries command limits. Its effective
executor timeout is the minimum of the requested command timeout and the remaining
collaborator budget.

`OperationKind` and `OperationLimits` are shared immutable data contracts owned by
`mem_sandbox.core.operations`. The session, policy, and event modules import them from
core; no module imports session-owned types back into policy or events.

Issue #20 extends only execute requests:

```python
@dataclass(frozen=True, slots=True)
class SessionSecretEnvironmentBinding:
    name: str
    secret_ref: SecretRef


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionExecuteRequest:
    command: str
    secret_environment: tuple[SessionSecretEnvironmentBinding, ...] = ()
    command_limits: CommandLimits = field(default_factory=CommandLimits)
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None
```

Bindings are immutable and deterministically ordered by environment name. `PWD`,
duplicate names, invalid command-environment names, invalid references, and unsupported
types fail during request construction. Multiple names may reference the same
`SecretRef`; policy and broker acquisition de-duplicate the reference while the overlay
retains every approved name.

Host-only methods may include:

```python
async def start() -> None: ...
async def read_bytes(request: ReadBytesRequest) -> ReadBytesResult: ...
async def write_bytes(request: WriteBytesRequest) -> FileMutationResult: ...
async def stat(request: StatRequest) -> StatResult: ...
async def list_entries(request: ListEntriesRequest) -> ListEntriesResult: ...
async def create_directory(request: CreateDirectoryRequest) -> PathMutationResult: ...
async def remove_path(request: RemoveEntryRequest) -> PathMutationResult: ...
async def create_snapshot(request: CreateSnapshotRequest) -> CreateSnapshotResult: ...
async def restore_snapshot(request: RestoreSnapshotRequest) -> RestoreSnapshotResult: ...
async def close() -> None: ...
```

Adapters decide which methods become model-visible tools.

### Native directory mutation contract

Issue #31 adds session-owned directory creation and path removal without routing these
operations through the command parser:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CreateDirectoryRequest:
    path: str
    create_parents: bool = False
    exist_ok: bool = False
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoveEntryRequest:
    path: str
    recursive: bool = False
    missing_ok: bool = False
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None


@dataclass(frozen=True, slots=True)
class PathMutationResult:
    metadata: OperationResultMetadata
    path: SandboxPath
    created: bool
    changed: bool
    previous_hash: ContentHash | None
    current_hash: ContentHash | None


FileMutationResult = PathMutationResult
```

`RemoveEntryRequest` deliberately avoids colliding with the workspace-owned
`RemovePathRequest`. `PathMutationResult` is the canonical mutation result for file and
path changes; `FileMutationResult` remains a public alias so existing callers keep one
runtime result type. The alias means the canonical runtime class name and representation
are `PathMutationResult`. Request construction validates `path` and every boolean option
before session orchestration begins, consistent with existing session request validation.

The session methods use `OperationKind.CREATE_DIRECTORY` and
`OperationKind.REMOVE_PATH`. They normalize the model-independent string path against
the session working directory, perform the existing lifecycle gate, operation event,
policy, deadline, and cancellation sequence, and then invoke exactly one owning
workspace mutation.

`create_directory` delegates to workspace `MakeDirectoryRequest`; `remove_path`
delegates to workspace `RemovePathRequest`. The session does not recreate parent
handling, recursive removal, missing-path, quota, hash, revision, or atomicity rules.
The returned metadata uses the workspace revision supplied by the mutation. A successful
workspace change advances revision exactly once. `exist_ok=True` succeeds unchanged when
the target is already a directory, including the workspace root; it returns the existing
directory hash as both `previous_hash` and `current_hash`. An existing file still raises
`PathAlreadyExistsError`. `exist_ok=False` remains strict for every existing directory,
including root. Missing removal with `missing_ok=True` returns `None` for both hashes.
Both idempotent paths preserve the current revision.

Non-recursive removal succeeds for a file or an empty directory and rejects a non-empty
directory. Recursive removal is required only when deleting a directory tree.

Admission includes a typed `PathMutationPolicyContext` so policy can distinguish parent
creation, existing-directory idempotence, recursive removal, and missing-path
idempotence. Other operation kinds set this policy field to `None`.

The methods never invoke `SessionCommandExecutor`. OpenAI's `mkdir` and `rm` adapter
methods translate to this public session seam, while command-language `mkdir` and `rm`
continue using the same workspace operations through the command executor.

## Collaborator ports

The session module owns the narrow ports it consumes:

```python
class SessionWorkspaceReader(Protocol):
    def resolve_path(
        self,
        value: str,
        *,
        cwd: SandboxPath | None = None,
    ) -> SandboxPath: ...
    async def stats(self) -> WorkspaceStats: ...
    async def stat(self, path: SandboxPath) -> WorkspaceEntry: ...
    async def list(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]: ...
    async def read_range(self, request: WorkspaceRangeRequest) -> WorkspaceRangeResult: ...
    async def read_bytes(self, path: SandboxPath) -> WorkspaceBinaryResult: ...


class SessionWorkspaceMutator(Protocol):
    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation: ...
    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation: ...
    async def patch(self, request: WorkspacePatchRequest) -> WorkspacePatchResult: ...
    async def remove(self, request: RemovePathRequest) -> WorkspaceMutation: ...


class SessionCommandExecutor(Protocol):
    async def execute(
        self,
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult: ...


class SessionPolicyEngine(Protocol):
    async def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...


class SessionSecretBroker(Protocol):
    async def lease(self, request: SecretAccessRequest) -> SecretLease: ...


class SessionEventSink(Protocol):
    async def emit(self, event: SandboxEvent) -> None: ...


class SessionSnapshotStore(Protocol):
    @property
    def process_local(self) -> bool: ...
    async def save(self, draft: SandboxSnapshotDraft) -> SnapshotRef: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...


class SessionSnapshotCodec(Protocol):
    def encode(self, state: SessionSnapshotState) -> SnapshotPayload: ...
    def decode(self, snapshot: SandboxSnapshot) -> SessionSnapshotState: ...


class SessionWorkspaceSnapshotPort(Protocol):
    async def export(self) -> WorkspaceSnapshotData: ...
    async def prepare_restore(
        self,
        data: WorkspaceSnapshotData,
        *,
        required_directory: SandboxPath,
    ) -> PreparedWorkspaceRestore: ...
    async def commit_restore(self, candidate: PreparedWorkspaceRestore) -> None: ...


class SessionResourceScope(Protocol):
    async def close(self) -> None: ...
```

Concrete components may implement several compatible protocols, but the session must not
depend on one broad infrastructure interface.

Issue #31 widens the session-owned mutator port with required `mkdir` and `remove`
methods. Custom session compositions must implement those methods even if their workspace
object already provides compatible operations.

Workspace mutation collaborators have an explicit publication invariant: they may
suspend while waiting to publish, but after publishing live state they return their
mutation result without another suspension point. A completed mutation result is
authoritative even if cancellation is requested or its collaborator budget expires
before the session observes that result. The session then reports completion rather than
misreporting an already-published mutation as cancelled or timed out. Required
`operation.completed` delivery is protected from native cancellation after that commit
boundary; delivery still uses the original operation deadline and still surfaces a
required-event failure explicitly.

The snapshot module implements a deterministic `JsonSessionSnapshotCodec`; the session
depends only on the session-owned codec protocol. Encoding produces the payload and state
hash before store save. `create_snapshot` builds a `SandboxSnapshotDraft`; the store
assigns absolute expiration when it accepts that draft. Decoding a persisted
`SandboxSnapshot` verifies the payload size, canonical encoding, content hash, session
schema, and capability version before workspace restore preparation.

The codec has a validated, session-configured `max_payload_bytes` limit of 64 MiB by
default. This bounds the complete session envelope independently of the workspace
snapshot limit and the Milestone 4 store-wide count/byte quotas. The limit is local
configuration and is not serialized into the deterministic state payload.

Behavior collaborators are borrowed. The session never closes the workspace, command
executor, policy engine, secret broker, event sink, snapshot store, clock, or identifier
generator individually. The composition root instead supplies one session-owned
`SessionResourceScope` containing only resources created specifically for that session.
Shared services are excluded from the scope. Operation-scoped secret leases remain owned
by the operation and close in `finally`.

A per-session event dispatcher must remain outside this scope because `close()` emits
`sandbox.closed` after scope cleanup. The service/factory-owned post-session scope closes
that dispatcher only after `SandboxSession.close()` completes.

The default `SandboxService` factory may inject a provenance-decorating implementation
of the existing `SessionSnapshotStore` port. That wrapper stamps snapshot drafts with
application-supplied logical owner provenance before delegating to the shared store. The
session remains unaware of owner identity and performs no authorization decision.

## Operation pipeline

Every model-facing and host operation follows the same ordered coordination pipeline,
except lifecycle methods where a step does not apply:

1. Validate request shape and limits that do not depend on mutable session state.
2. Start one end-to-end operation deadline before waiting for admission.
3. Acquire the session-wide operation gate.
4. Reject calls when the session state does not permit the operation.
5. Normalize paths and command metadata.
6. Create an operation identifier and emit the required `operation.started` event.
7. Ask the minimal policy-admission collaborator for an explicit decision.
8. For execute, acquire each approved unique secret reference in deterministic order.
9. Build one operation-local redactor and ephemeral command-environment overlay.
10. Invoke the workspace or command executor with the remaining deadline budget.
11. Close all acquired leases before committing transient cwd/environment state.
12. Commit explicit session state from a normal result.
13. Emit the required completion or failure event.
14. Release the operation gate.
15. Return a domain result or raise a stable domain error.

Admission denial occurs before secret resolution and before workspace mutation. Policy
receives only the sorted unique `SecretRef` values, not environment values or resolved
material. The release retains the explicit admission seam but does not implement
composed operation, path, command, argument, destination, or resource policy.

The deadline covers gate wait, policy, collaborator execution, session-state commit, and
terminal event delivery. Cancellation or timeout while waiting for the gate creates no
operation identifier or event and invokes no policy, secret, workspace, executor, or
snapshot collaborator.

The operation reserves the request's terminal-event budget, defaulting to one second,
before mutation begins. If the remaining end-to-end budget cannot preserve that reserve,
the session raises its operation-timeout error before constructing or invoking a
collaborator request. Command execution receives:

```text
min(requested command timeout, end-to-end deadline - terminal event reserve - now)
```

Workspace calls do not take a deadline argument. The session enforces their collaborator
budget with its own outer timeout scope. Required terminal delivery uses the reserved
portion but must still complete before the original end-to-end deadline.

Policy may narrow the protected-operation timeout after `operation.started` has already
been delivered. The narrowed deadline constrains collaborator invocation and mutation,
while the caller's original reserved terminal window remains available for the required
`operation.timed_out` event. The exactly-one-terminal-event invariant takes precedence
over applying the narrowed timeout to audit delivery.

Session deadlines use `asyncio` loop monotonic time. The injected UTC `Clock` supplies
event and result timestamps only; it is not widened into a timeout or scheduling
dependency.

For execute, the existing action closure owns lease acquisition and cleanup without
duplicating the shared operation pipeline. `_run_operation` accepts only the normalized
reference facts needed to construct the policy request:

```text
policy-approved action
  -> verify remaining collaborator budget
  -> acquire unique leases
  -> build overlay and protection adapter
  -> await command executor
  -> close all leases
  -> read workspace stats
  -> compute and commit cwd/approved non-secret environment
  -> return to shared terminal-event handling
```

Lease cleanup therefore completes before cwd/environment commit. A cleanup-only failure
prevents those transient changes from becoming session state. The shared terminal event
is emitted after leases have closed. If lease acquisition or redactor construction
fails, earlier leases close and the command executor is never invoked.

Cleanup continues through every lease when one close raises any `BaseException`.
Failures originating in a close task are reduced to the stable, value-free
`SecretLeaseCleanupFailed`; interruption delivered to the outer operation is deferred
until every lease has closed and then passes through the same protected exception-graph
sanitizer as other operation failures.

Before constructing each `SecretAccessRequest`, the session rechecks the remaining
effective collaborator budget. An exhausted budget raises `SessionOperationTimeout`
rather than exposing a non-positive requested-duration validation error.

A normal command-executor result commits its returned cwd and approved non-secret
environment changes even when its exit code is non-zero. Raised timeout, cancellation,
policy, secret, infrastructure, or internal failures do not commit transient cwd or
environment state. A command result containing a protected persistent environment
change is rejected by the executor before it reaches this commit boundary.
Earlier workspace mutations performed by a timed-out command may remain committed while
its transient cwd/environment changes are discarded; this divergence is intentional and
matches the command-executor contract.

After command execution and session-state commit, the session reads workspace `stats()`
while still holding the operation gate. That revision populates the mandatory
`OperationResultMetadata.workspace_revision` in `SessionExecuteResult`.

## State machine

```text
CREATED
  -> RUNNING
  -> CLOSING
  -> CLOSED

CREATED
  -> CLOSING
  -> CLOSED

CREATED, RUNNING, or CLOSING
  -> FAILED

FAILED
  -> CLOSING
  -> CLOSED
```

Rules:

- Construction produces `CREATED`; only explicit `start()` enters `RUNNING`.
- Agent operations require `RUNNING`.
- `start()` is valid only from `CREATED`.
- Once close is requested, the active operation may finish, but queued and newly
  arriving operations are rejected.
- Once the transition to `CLOSING` begins, close completes resource-scope cleanup despite
  caller cancellation before propagating cancellation.
- `close()` is idempotent for concurrent or repeated callers.
- `FAILED` rejects new operations but still permits cleanup.
- Expected operation errors, policy denials, command non-zero results, timeouts, and
  cancellations do not by themselves fail the session.
- `FAILED` is reserved for lifecycle or invariant failures that make safe continued use
  uncertain.
- Concrete `FAILED` triggers in Milestone 3 are:
  - failure to complete required startup event delivery;
  - an internal lifecycle-state or operation-gate invariant violation;
  - a restore or session-state publication failure for which unchanged live state cannot
    be proven.
- Atomic restore rejection, expected collaborator errors, event failure after an
  otherwise safe completed operation, and resource-scope cleanup failure do not
  transition the session to `FAILED`.
- Snapshot restore is allowed only through an exclusive host operation.
- A closed session is never restarted. Future service resume constructs a new session
  from restored state rather than reviving object identity.

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
| Start | Session-wide exclusive |
| Close | Admission cutoff, then session-wide exclusive cleanup |

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
returned. The factory admits only command executors that honor the command-executor
cooperative cancellation contract. As with resource scopes, Python in-process code
cannot safely force-terminate a collaborator that suppresses cancellation and never
returns.

The operation gate may be FIFO for admitted work, but close has a separate admission
cutoff. Operations waiting when close is requested do not drain ahead of shutdown; they
acquire only to observe `CLOSING` or `CLOSED` and fail without invoking collaborators.

## Session-owned state

The session owns:

- session identity
- lifecycle status
- operation sequence number
- current working directory
- approved environment variables
- capability profile
- creation metadata
- close admission and completion state

File content belongs to the workspace. Persisted snapshot bytes belong to the snapshot
store. Secret values, leases, overlays, and operation redactors never become session state.

## Snapshot behavior

Snapshot creation captures a consistent combination of:

- workspace state
- current working directory
- approved non-secret environment state
- schema and feature versions

Snapshot creation does not capture active operations, secret leases, framework tool
objects, or agent conversation state.

An execute operation closes all leases before another operation can acquire the session
gate. Snapshot creation therefore cannot race or observe an active overlay. Snapshots
encode only the approved persistent `CommandEnvironment`.

Milestone 3 records `source_session_id` as provenance only. Snapshot authorization is not
performed by `SandboxSession`, the snapshot store, or the process-local
`SandboxService`. Applications decide which references may cross their trust boundary.
Service-created sessions preserve logical creator provenance without treating it as an
access decision.

Restore is an exclusive host operation on a live `RUNNING` session. The session validates
session schema, capability version, cwd, and approved environment before changing live
state. Workspace schema, integrity, and target-limit compatibility are delegated to
workspace preparation. The workspace prepares an immutable restore candidate and verifies
that the restored cwd is an existing directory. Candidate commit and synchronous
cwd/environment assignment occur without an await between them. Any failure before or
during a cancellation-cooperative candidate commit leaves the workspace, cwd, and
environment unchanged. If a non-conforming collaborator suppresses cancellation and
successfully publishes, publication success wins so the session does not report a failed
restore after live state changed.

The deterministic state hash excludes the random snapshot identifier and creation
timestamp. Repeated snapshots of identical workspace, cwd, environment, and feature
metadata therefore have the same state hash while retaining distinct references.

Same-session restore publishes the snapshot's workspace revision. Revisions are monotonic
between restores but may move backward across an explicit restore; callers use snapshot
identity and operation ordering when reasoning across that boundary.

## Event delivery

Required delivery remains the default. The concrete `NoOpEventSink` is a real
accept-and-discard implementation whose `emit` operation succeeds.

The session constructs immutable event envelopes and assigns the session-local monotonic
sequence number. The sink only accepts an already-formed event; a shared sink never owns
session sequencing.

The session-owned event set is:

- lifecycle: `sandbox.started`, `sandbox.closing`, `sandbox.closed`, `sandbox.failed`;
- operation start: `operation.started`;
- exactly one terminal event: `operation.completed`, `operation.failed`,
  `operation.cancelled`, or `operation.timed_out`;
- post-commit snapshots: `snapshot.created`, `snapshot.restored`.

Policy-decision and secret lifecycle events remain deferred; issue #20 adds no new event
types. Dedicated snapshot events use a post-commit hook before `operation.completed`.
`sandbox.created` and `sandbox.deleted` remain producer-less until service and session
events share one approved sequencer or use separate event identity contracts.

For operation start and terminal events, failure to deliver the required event emits no
further event for that operation:

- if `operation.started` delivery fails, no terminal event is attempted;
- if terminal delivery fails, no replacement failure event is attempted.

If the end-to-end deadline expires while delivering `operation.started`, the session
raises `SessionOperationTimeout` and attempts no terminal event. A sink-originated start
failure raises `SessionEventDeliveryFailed`, also without a terminal event.

Startup failure caused by `sandbox.started` delivery transitions to `FAILED` without
attempting `sandbox.failed` on the same failing sink. `sandbox.failed` is emitted only
when a non-event lifecycle or invariant failure transitions the session to `FAILED`; its
own delivery failure does not change the already-published state and is attached safely
to the primary failure.

Terminal operation kind is selected from the stable error category regardless of whether
the error originated in the session or a collaborator:

- `TIMEOUT` -> `operation.timed_out`;
- `CANCELLED` -> `operation.cancelled`;
- every other error -> `operation.failed`;
- a normal domain result, including a non-zero command exit, -> `operation.completed`.

Execute admission requests use `path=None` and `command_name=None`, plus the sorted
unique secret references declared by the request. The session does not duplicate
command-language parsing merely to populate speculative policy metadata.
Descriptor-level command authorization remains deferred until an adapter or service
introduces a concrete trust boundary. Any future design must consume executor-owned
prepared artifacts rather than reparse command text in the session.

Session requests may carry an optional cooperative `CancellationSignal`. The session
passes the same signal into `CommandExecutionContext`, while preserving native
`asyncio.CancelledError` behavior and enforcing the absolute deadline with its own
timeout scope.

- Failure to deliver `operation.started` prevents policy evaluation and mutation.
- Failure to deliver a terminal event is surfaced explicitly.
- A terminal-event failure does not roll back an already committed workspace or session
  mutation.
- Event delivery never returns a success-shaped fallback.

Best-effort delivery is available through a per-session owner-managed dispatcher. The
session still consumes only `emit()`.
Best-effort dispatch prepares and enqueues events without waiting for the concrete sink,
requires an explicit synchronous diagnostic handler, and never changes operation or
lifecycle outcomes. If that handler raises, the secondary failure is reported through
the asyncio loop exception handler.

Snapshot-specific events are emitted after snapshot persistence or restore publication,
but before `operation.completed`, using the first half of the terminal-event reserve.
The second half remains available for the final operation event attempt. A required
snapshot-event failure produces `operation.failed` while the already committed snapshot
or restored state remains committed.

## Close and resource cleanup

`close()` transitions to `CLOSING`, waits for the active operation, and closes the
session-owned resource scope exactly once. Shared borrowed collaborators are untouched.

If resource-scope cleanup fails, the session still reaches `CLOSED` and `close()` raises a
stable cleanup error. Later idempotent close calls observe the completed close and do not
retry cleanup implicitly.

The composition root may place a future closeable workspace or executor resource in the
scope. The session still closes only the resource scope and never calls lifecycle methods
on behavior ports directly.

`start()` uses session-configured lifecycle limits with the same 30-second timeout and
one-second required-terminal-event reserve defaults as operation requests.

Start acquires the operation gate while `CREATED`, delivers `sandbox.started`, and then
publishes `RUNNING` without another await. Startup-event failure transitions the session
to `FAILED`.

Close performs this cancellation-resilient sequence:

1. Publish the close admission cutoff and `CLOSING`.
2. Wait for the active operation and acquire the operation gate.
3. Attempt required `sandbox.closing` delivery.
4. Close the resource scope exactly once even if event delivery failed.
5. Publish `CLOSED`.
6. Attempt required `sandbox.closed` delivery.
7. Raise the primary lifecycle event or cleanup failure, with secondary cleanup details
   attached safely.

Close-event delivery or cleanup failure never leaves the session between states and does
not transition it to `FAILED`; the terminal state is `CLOSED`. A non-cooperative resource
scope that cannot complete cleanup safely is not accepted by the session factory.

Waiting for the active operation is not charged to close's lifecycle timeout. The active
operation already has its own finite end-to-end deadline, and close must not return while
that operation may still mutate session state. After close acquires the gate, a fresh
30-second lifecycle budget begins. One second is reserved for `sandbox.closed`;
`sandbox.closing` delivery and resource-scope cleanup share the preceding collaborator
budget. Close timeout is surfaced only after cancellation-safe cleanup finishes and the
session reaches `CLOSED`.

Concurrent callers awaiting the same close sequence observe the same primary close
failure. A later call made after close has completed returns normally and never replays a
previous event, cleanup action, or failure.

This is a deliberate safety trade-off rather than a hard preemption guarantee: a resource
scope that suppresses cancellation and never returns can prevent `close()` from
returning. The factory must admit only cooperatively cancellable resource scopes; Python
in-process code cannot be forcibly terminated safely.

## Failure semantics

The session never returns success-shaped fallback values after collaborator failure.
Session-specific errors retain a stable category and include an `operation_id` when one
was allocated. Queue timeout/cancellation and close-cutoff rejection occur before
operation allocation and therefore have no operation ID.

Milestone 3 defines:

- `SessionRequestInvalid`;
- `SessionStartInvalid`;
- `SessionNotRunning`, `SessionClosing`, and `SessionClosed`;
- `SessionFailed`;
- `SessionOperationTimeout` and `SessionOperationCancelled`;
- `SessionCloseTimeout`;
- `SessionPolicyDenied`;
- `SessionEventDeliveryFailed`;
- `SessionSnapshotRestoreFailed`;
- `SecretLeaseCleanupFailed`;
- `SessionCleanupFailed`.

| Session error | Core category |
|---|---|
| `SessionRequestInvalid` | `INVALID_REQUEST` |
| `SessionStartInvalid`, `SessionNotRunning`, `SessionClosing`, `SessionClosed` | `CONFLICT` |
| `SessionFailed` | `INTERNAL` |
| `SessionOperationTimeout` | `TIMEOUT` |
| `SessionOperationCancelled` | `CANCELLED` |
| `SessionCloseTimeout` | `TIMEOUT` |
| `SessionPolicyDenied` | `POLICY_DENIED` |
| `SessionEventDeliveryFailed` | `INTERNAL` |
| `SessionSnapshotRestoreFailed` | `INTERNAL` |
| `SecretLeaseCleanupFailed` | `INTERNAL` |
| `SessionCleanupFailed` | `INTERNAL` |
| `SecretReferenceInvalid` | `INVALID_REQUEST` |
| `SecretDenied` | `POLICY_DENIED` |
| `SecretNotFound` | `NOT_FOUND` |
| `SecretExpired` | `TIMEOUT` |
| `SecretLeaseLimitExceeded` | `QUOTA_EXCEEDED` |
| `SecretSourceUnavailable`, `SecretSourceValueInvalid` | `INTERNAL` |
| `SecretLeaseClosed` | `CONFLICT` |

`SecretExpired` maps to `operation.timed_out`. Every other secret error maps to
`operation.failed`. No secret error text, cause, or cleanup note contains a resolved
value.

Session errors subclass the corresponding existing core category classes while adding
optional operation identity. Collaborator errors that already have a stable category are
preserved; session orchestration wrappers retain them as safe causes. If both an
operation and lease cleanup fail, the primary operation error remains primary and cleanup
details are attached safely.

`SessionPolicyDenied` carries the safe policy `reason_code` and allocated operation ID.
It maps to `operation.failed`; no protected collaborator or secret broker is invoked.

Snapshot load, decode, compatibility, and prepared-restore rejections preserve their
existing `NOT_FOUND`, `INVALID_REQUEST`, `CONFLICT`, `QUOTA_EXCEEDED`, or `UNSUPPORTED`
categories. `SessionSnapshotRestoreFailed` is reserved for a post-commit session-state
publication failure where unchanged live state cannot be proven; that failure transitions
the session to `FAILED`.

## Test expectations

- Happy path for each of the four agent operations.
- Policy-denied operations invoke no workspace, executor, or secret collaborator.
- Secret-bearing execute denial invokes neither broker nor source.
- Empty secret bindings preserve the current no-broker-call path.
- Partial lease acquisition failure closes earlier leases and does not invoke the
  executor.
- Lease cleanup runs in reverse order across success, non-zero results, failures,
  timeouts, cooperative cancellation, native cancellation, and terminal-event failure.
- An arbitrary `BaseException` raised by one lease close is contained, later leases still
  close, and no unsafe exception detail escapes.
- Secret overlays are visible only during execute and never enter session environment,
  cwd, snapshots, results, events, exception text, or useful object representations.
- Protected environment persistence, workspace paths, and redirection destinations are
  rejected before mutation.
- Dependency failures are surfaced with stable categories.
- Timeout and cancellation produce no later mutation.
- Concurrent calls on one session are serialized deterministically.
- Independent sessions can make progress concurrently.
- Cancellation while waiting for the operation gate performs no collaborator call or
  mutation.
- End-to-end timeout while waiting for the operation gate has the same no-side-effect
  guarantee.
- Close rejects queued and new operations after the admission cutoff while allowing the
  active operation to finish.
- Reads honor range boundaries and content hashes.
- Stale expected hashes reject writes and patches.
- Snapshot creation is point-in-time consistent.
- Restore excludes concurrent operations and reestablishes cwd/environment.
- Restore preparation validates cwd before live state changes and failed restore leaves
  all live state unchanged.
- Source-session provenance is preserved without performing Milestone 3 owner
  authorization.
- Required start and terminal event failures follow the documented mutation behavior.
- Event sequence numbers are session-owned, monotonic, and start-before-terminal.
- Startup event failure reaches `FAILED`; closing an unstarted `CREATED` session remains
  legal.
- Close is idempotent and closes the session resource scope exactly once.
- The public cross-module lifecycle and normalized trace are defined in
  [Direct Core Conformance](../../../tests/conformance/README.md), including both
  same-identity `restore_snapshot()` and new-identity service resume. Snapshot result
  `content_hash` is the complete session state hash; conformance obtains the distinct
  workspace root hash by decoding the persisted snapshot through retained public
  fixtures.

## Maintenance rule

Changes to operation ordering, lifecycle, concurrency, snapshot composition, or any
session-owned port require an update to this document.
