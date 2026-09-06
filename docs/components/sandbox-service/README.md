# Sandbox Service Design

**Status:** Implemented in issue #19

## Purpose

The sandbox service is the host-facing process-local lifecycle coordinator. It creates,
locates, resumes, and deletes sandbox sessions while keeping framework adapters unaware
of concrete workspace, executor, event, snapshot, and resource-scope implementations.

The service owns live-session publication and cleanup. It does not implement file,
command, snapshot encoding, or application authorization behavior.

The implementation lives in `mem_sandbox.service`. `InMemorySandboxService` implements
the public `SandboxService` protocol, while `DefaultSessionFactory`,
`InMemoryServiceSnapshotGateway`, `DefaultServiceSessionRuntime`, and
`CompositeResourceScope` implement the service-owned construction and cleanup ports.

## Responsibilities

- Validate create and resume requests.
- Generate opaque sandbox handles and new session identities.
- Construct sessions through a narrow constructor-injected factory.
- Maintain a race-safe process-local registry of fully started sessions.
- Preserve the application-supplied logical owner as provenance.
- Resume a new independent session from a validated snapshot.
- Prevent lookup as soon as deletion begins.
- Close each published session exactly once during deletion.
- Return stable framework-neutral lifecycle failures.

## Out of scope

- Authenticating callers or authorizing access to handles and snapshot references.
- Model-visible tool definitions.
- Filesystem operations or command execution.
- Per-operation policy decisions.
- Snapshot serialization and store quota enforcement.
- Automatic live-session expiration.
- Agent conversation or workflow state.
- Network transport, MCP, or an HTTP control plane.
- Producing `sandbox.created` or `sandbox.deleted` events in issue #19.

## Trust and provenance model

`OwnerId` is validated application-supplied provenance, not an authenticated security
principal. MemSandbox is a package and cannot determine whether a caller is entitled to
claim a particular value.

The application or adapter must:

- authenticate its caller when required;
- keep the principal-to-handle and principal-to-snapshot-reference mapping;
- expose only authorized handles and references to downstream code;
- wrap `SandboxService` when server-side authorization is needed.

Core service methods do not accept a second caller-supplied owner value and compare it
with stored metadata. Such equality would provide security only if the application had
already authenticated the value, at which point the application can enforce the mapping
directly.

The service stores `OwnerId` on the live-session record and arranges for snapshots
created by that session to preserve the same creator provenance. `source_session_id` and
creator provenance are diagnostic facts only. Neither grants access to a handle or
snapshot.

## Public contract

The initial service contract is intentionally small:

```python
@dataclass(frozen=True, slots=True)
class OwnerId:
    value: str


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    value: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class SandboxOptions:
    workspace_limits: WorkspaceLimits = field(default_factory=WorkspaceLimits)
    lifecycle_limits: OperationLimits = field(default_factory=OperationLimits)


@dataclass(frozen=True, slots=True)
class WorkspaceSeedFile:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateSandboxRequest:
    owner_id: OwnerId
    options: SandboxOptions = field(default_factory=SandboxOptions)
    initial_files: tuple[WorkspaceSeedFile, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ResumeSandboxRequest:
    owner_id: OwnerId
    snapshot_ref: SnapshotRef
    options: SandboxOptions = field(default_factory=SandboxOptions)


class SandboxService(Protocol):
    async def create(self, request: CreateSandboxRequest) -> SandboxHandle: ...
    async def get_session(self, handle: SandboxHandle) -> SandboxSession: ...
    async def resume(self, request: ResumeSandboxRequest) -> SandboxHandle: ...
    async def delete(self, handle: SandboxHandle) -> None: ...
    async def close(self) -> None: ...
```

`OwnerId` is non-empty text of at most 256 UTF-8 bytes and is preserved exactly without
Unicode, case, or whitespace normalization. `SandboxHandle` wraps a non-nil random UUID
and does not encode an owner, path, secret, or other meaningful value.

`WorkspaceSeedFile` is create-only. Paths are normalized by the newly created workspace,
duplicate normalized paths are rejected before publication, and all writes complete
before the session starts. Nested seed paths create parent directories. Resume and
initial files are separate operations and cannot be combined.

There is no live-session TTL in the initial service. A session remains registered until
explicit deletion or until a failed/externally closed session is encountered and cleaned
up.

## Construction boundary

The service owns a narrow session factory:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class SessionFactoryRequest:
    session_id: SessionId
    options: SandboxOptions
    snapshot_store: FactorySnapshotStore
    initial_files: tuple[WorkspaceSeedFile, ...] = ()
    restored_state: SessionSnapshotState | None = None


class ServiceSessionRuntime(Protocol):
    @property
    def session(self) -> SandboxSession: ...
    async def close(self) -> None: ...


class SessionFactory(Protocol):
    async def create(self, request: SessionFactoryRequest) -> ServiceSessionRuntime: ...


class FactorySnapshotStore(Protocol):
    @property
    def process_local(self) -> bool: ...
    async def save(self, draft: SandboxSnapshotDraft) -> SnapshotRef: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...


class ServiceSnapshotGateway(Protocol):
    def session_store(self, owner_id: OwnerId) -> FactorySnapshotStore: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...


class ServiceSnapshotDecoder(Protocol):
    def decode(self, snapshot: SandboxSnapshot) -> SessionSnapshotState: ...
```

`SessionFactoryRequest` rejects a request containing both `initial_files` and
`restored_state`.

The concrete factory assembles collaborators with constructor injection:

- one new `MemoryWorkspace`;
- one executor bound to that workspace;
- borrowed shared policy, secret, event, snapshot, clock, and UUID collaborators;
- one per-session event dispatcher when configured;
- one per-session `SessionResourceScope`;
- one post-session resource scope;
- the service-supplied provenance-decorating snapshot-store view.

The provenance wrapper implements the existing session-owned snapshot-store port. On
save, it stamps the immutable snapshot draft with the logical creator before delegating
to the shared snapshot store. Load remains reference-based and performs no authorization.
This preserves creator metadata without adding owner identity to `SandboxSession`,
operation policy requests, or snapshot restore requests.

The factory prepares all initial state while the session is still `CREATED`:

- create applies validated seed files to the new workspace;
- resume prepares and commits the decoded workspace snapshot, then supplies restored cwd
  and approved environment to the session constructor.

The service starts `ServiceSessionRuntime.session`. The factory never starts or registers
it. The runtime's service-owned `close()` port hides the concrete session and
post-session scopes from the registry.

If construction fails before a session is returned, the factory closes every initialized
owned resource in reverse construction order. Shared borrowed collaborators are never
closed. Cleanup continues after an individual close failure and surfaces one stable
factory failure with secondary failures attached through `BaseException.add_note()`.

The concrete service is constructor-injected:

```python
InMemorySandboxService(
    *,
    session_factory: SessionFactory,
    snapshot_gateway: ServiceSnapshotGateway,
    snapshot_decoder: ServiceSnapshotDecoder,
    clock: Clock,
    uuid_generator: UuidGenerator,
)
```

The gateway creates owner-provenance session-store views and loads resume references from
the same underlying store. This composition invariant prevents sessions from saving to
one store while the service resumes from another. The decoder and runtime are
service-owned ports even when the current concrete implementations delegate to the
snapshot codec and session resource scopes.

## Session resource scope

The session resource scope contains resources that may close before `sandbox.closed`.
The event dispatcher must not be placed in that scope because `SandboxSession.close()`
closes its scope before emitting `sandbox.closed`.

The concrete `ServiceSessionRuntime` owns the per-session dispatcher and any other
resource that must remain available through the final session event. Runtime cleanup
always runs:

```text
session.close()
  -> post_session_scope.close()
```

Each scope `close()`:

- is idempotent and safe under concurrent calls;
- closes resources in reverse construction order;
- continues cleanup after one resource fails;
- preserves native task cancellation while completing already-started cleanup;
- never closes borrowed shared collaborators.

`SandboxSession` continues to own only its internal scope's `close()` capability. It does
not gain individual collaborator lifecycle methods or close the post-session scope.

The runtime gives post-session cleanup a fresh
`SandboxOptions.lifecycle_limits.timeout_seconds` budget. Post-session cleanup timeout or
failure is a runtime-close failure and is translated by delete to `SandboxDeleteFailed`.
Cleanup remains cancellation-resilient, so a non-conforming collaborator that suppresses
cancellation can delay final shutdown; the process-local package cannot forcibly
terminate arbitrary Python code.

## Registry model

The in-memory service keeps an internal record:

```text
SessionRecord
  handle
  owner_id
  session_id
  runtime: ServiceSessionRuntime
  created_at
  status: active | deleting
  deletion_task
```

A single service lock protects handle reservation, active publication, lookup state, and
the transition to deleting. Slow session startup and close operations run outside that
lock.

Pending handles are reserved internally to prevent generator collisions, but pending
sessions are not discoverable through `get_session`.

The service also has `open`, `closing`, and `closed` states and tracks pending create and
resume tasks. Handle and session IDs are reserved together so collisions are detected
before factory invocation.

## Lifecycle

### Create

1. Validate owner provenance, options, and seed shape.
2. Allocate and reserve a unique opaque handle and session identity.
3. Ask the factory to construct a `CREATED` session.
4. Start the session outside the service lock.
5. Publish one active registry record only after startup succeeds.
6. Return the handle.

Construction or startup failure removes the pending reservation, closes any returned
runtime in session-then-post-session order, and leaves no active registry entry. Cleanup
failures are attached to the primary startup failure rather than replacing it.

If service shutdown begins while construction or startup is in progress, the operation
must not publish. It closes any constructed runtime, releases both identity reservations,
and raises `SandboxServiceClosed`. Service close waits for this cleanup.

### Lookup

Lookup returns only an active `RUNNING` session. Unknown, pending, and deleting sessions
raise `SandboxNotFound`.

If a published session is `CLOSING`, `CLOSED`, or `FAILED`, lookup atomically transitions
the record to deleting and drives the same shielded cleanup task used by explicit delete.
It then raises `SandboxNotFound`, or `SandboxDeleteFailed` if cleanup failed. Lookup never
removes a terminal record without closing its remaining owned resources.

This lookup may wait for an in-flight operation and bounded runtime cleanup, just like
explicit delete. It never holds the service registry lock while waiting.

Applications must not treat possession of a handle as authentication unless their own
boundary makes that guarantee.

### Resume

1. Load the snapshot reference through the service snapshot gateway.
2. Decode and verify complete snapshot integrity and compatibility.
3. Validate decoded session-level state.
4. Allocate a new handle and session identity.
5. Ask the factory to construct a new `CREATED` session with the restored state.
6. Start and publish it using the same rules as create.

Resume never calls the live-session `restore_snapshot()` operation. The factory creates
the target workspace, validates workspace schema and quotas through
`prepare_restore`, and commits it before `sandbox.started`.

Every resume creates a new session identity and independent workspace. Reusing one
snapshot reference creates independent forks. The snapshot's original creator and source
session remain unchanged; `request.owner_id` becomes provenance for the new live session
and for snapshots it later creates.

The application is responsible for deciding whether the caller may use the supplied
snapshot reference.

### Delete

1. Resolve an active record under the service lock.
2. Atomically change its status to deleting and create one deletion task.
3. Block all new lookups before cleanup begins.
4. Close the session and then its post-session scope outside the lock.
5. Remove the registry record even when close reports a failure.
6. Return normally or raise `SandboxDeleteFailed`.

Concurrent delete calls that observe the same deleting record await the same shielded
deletion task. The session and its resource scope close exactly once. After the record is
removed, another delete raises `SandboxNotFound`; the service retains no ownership
tombstones.

Cancellation of one delete caller does not cancel shared cleanup. The caller's native
`asyncio.CancelledError` propagates only after the deletion task reaches a terminal
state.

Delete waits for an in-flight session operation because `SandboxSession.close()` owns the
operation-gate semantics. The service does not add a second operation timeout.

### Service close

`close()` prevents new create, resume, and lookup calls, transitions every active record
to deleting, waits for pending create/resume tasks to finish rollback, and awaits all
shared deletion tasks. It is idempotent and cancellation-resilient. All records and
identity reservations are removed even if one session cleanup fails; one stable service
failure reports secondary cleanup failures with exception notes.

## Service lifecycle events

`sandbox.created` and `sandbox.deleted` remain defined but producer-less in issue #19.
The current `SandboxEvent` identity is `(session_id, sequence)`, and `SandboxSession`
privately owns that sequence. A service producer cannot allocate from the same stream
without risking duplicate event IDs.

Timestamp is occurrence metadata, not a unique identity or causal ordering source:
injected clocks may intentionally return the same timestamp and real wall clocks may
repeat or move backward.

Producing service lifecycle events requires a separate approved change that either:

- injects one shared per-session sequencer/publisher into service and session; or
- defines a separate service-scoped event identity and ordering contract.

## Failure semantics

Stable service errors are:

- `InvalidSandboxRequest`;
- `SandboxNotFound`;
- `SandboxIdentifierConflict`;
- `SandboxServiceClosed`;
- `SandboxStartupFailed`;
- `SandboxResumeFailed`;
- `SandboxDeleteFailed`;
- `SessionFactoryFailed`;
- `SessionFactoryCleanupFailed`.

There is no `SandboxOwnershipDenied` error because authorization is application-owned.
Snapshot corruption, incompatibility, quota, expiry, and not-found failures retain their
snapshot-domain errors. `SandboxResumeFailed` covers unexpected factory/startup failure
after a snapshot decoded successfully. Workspace-domain failures from target
`prepare_restore`, including target quota and schema failures, propagate unchanged.
`SessionFactoryFailed` covers unexpected construction failure;
`SessionFactoryCleanupFailed` is raised only when cleanup itself is the primary failure.
Secondary cleanup failures use `add_note()`.

Create, resume, lookup, and delete after service shutdown raise `SandboxServiceClosed`.
Repeated `close()` remains successful.

Errors preserve safe causes for host diagnostics without including seed content,
snapshot payload bytes, environment values, or secrets.

## Behavior-first test matrix

### Models and validation

- `OwnerId` accepts exactly 256 UTF-8 bytes and rejects empty or 257-byte values without
  normalization.
- `SandboxHandle` rejects nil and non-UUID values and has stable canonical text.
- Options, requests, seed files, and factory requests are immutable and reject unsupported
  types.
- Request tuples remain immutable after construction.
- Factory requests reject simultaneous `initial_files` and `restored_state`.
- Duplicate normalized seed paths, invalid UTF-8 paths, and files exceeding workspace
  limits fail before session publication.
- Nested seed paths create parent directories.
- Service requests never place `OwnerId` in operation policy requests.

### Factory and resource ownership

- A created session receives a new workspace, workspace-bound executor, provenance
  snapshot wrapper, and one resource scope.
- Seed files exist before `sandbox.started` and are absent from other sessions.
- Restored workspace, cwd, and approved environment are committed before
  `sandbox.started`.
- Factory construction failure closes only successfully created owned resources in
  reverse order.
- The dispatcher is never closed before `SandboxSession.close()` returns.
- Required lifecycle events are accepted before dispatcher close; best-effort
  `sandbox.closing` and `sandbox.closed` reach the sink when post-session close drains the
  queue.
- The dispatcher closes exactly once after `SandboxSession.close()` completes.
- Scope close is idempotent and concurrent callers observe one cleanup execution.
- Scope cleanup continues after failure and reports secondary failures deterministically.
- Shared policy, secret, event, snapshot, clock, and UUID collaborators are never closed.

### Create and lookup

- Successful create returns only after startup and lookup returns the same running
  session.
- Startup failure leaves no active or pending record and closes the returned session once.
- Factory failure leaves no record and does not call session startup.
- Concurrent creates with fixed generators reserve unique handles and session IDs or
  surface deterministic identifier conflicts without partial publication.
- A handle is not discoverable while construction or startup is blocked.
- Service close racing blocked construction/startup prevents publication, cleans the
  runtime, and causes the lifecycle caller to receive `SandboxServiceClosed`.
- Unknown, pending, and deleting sessions raise `SandboxNotFound`.
- Lookup of an externally closing, closed, or failed session drives the shared deletion
  path and never leaks its remaining resources.
- Distinct sessions have isolated workspaces, cwd, environment, event sequence, and
  resource scopes.

### Resume and provenance

- Resume verifies store load, canonical payload integrity, session/capability versions,
  and typed cwd/environment state before factory invocation.
- The factory validates target workspace schema and quotas before session startup and
  registry publication, including that cwd exists as a directory.
- Corrupt, incompatible, expired, or missing snapshots create no handle, session, or
  registry record.
- Resume allocates a new handle and session identity while preserving snapshot
  `source_session_id` and creator provenance.
- The new session uses `ResumeSandboxRequest.owner_id` as its own provenance.
- Two resumes from one snapshot produce independent workspace forks.
- Mutating or deleting one resumed session does not mutate the snapshot or another fork.
- The service performs no owner comparison and invokes no authorization collaborator.

### Delete and concurrency

- Delete marks the record deleting before session close begins; lookup fails from that
  point.
- Concurrent delete calls await one cleanup task and close the session and scope once.
- Cancellation of one delete waiter does not cancel deletion or affect another waiter.
- Session close failure still removes the record and is translated to
  `SandboxDeleteFailed`.
- A published session that reaches `FAILED` is closed exactly once through explicit
  delete or lookup-driven cleanup.
- Delete of an unknown or already removed handle raises `SandboxNotFound`.
- Delete waits for an in-flight operation, blocks new lookups immediately, and does not
  invent a second operation timeout.
- Create, lookup, and delete interleavings never return a partially started or deleting
  session.
- Service close blocks new lifecycle publication, removes all records, and closes every
  runtime once even when one cleanup fails.
- Service close waits for pending create/resume rollback and leaves no reserved handle or
  session identity.
- Create, resume, lookup, and delete after service close raise
  `SandboxServiceClosed`; repeated close succeeds.

### Boundary and regression

- Direct `SandboxSession` construction remains supported without `SandboxService`.
- The minimal allow-all policy seam and no-secret broker behavior remain unchanged.
- Application authorization remains outside core and is documented with a wrapper
  example.
- No automatic live-session expiry task or timer is created.
- `sandbox.created` and `sandbox.deleted` remain unproduced.
- Core and service modules import no agent framework or transport dependency.
- The public create, mutate, snapshot, close, resume, repeated-resume fork, delete, and
  service-close sequence is composed in
  [Direct Core Conformance](../../../tests/conformance/README.md). Session close alone
  does not unregister a service handle, and live-session deletion does not delete
  independently retained snapshots.

## Maintenance rule

Changes to lifecycle publication, provenance, handle semantics, registry state, factory
ownership, or application trust assumptions require updates to this README and behavior
tests in the same change.
## Provider reattachment

Agent-framework providers may persist the string form of `SandboxHandle` as a
process-local reattachment hint. The handle remains opaque application data: it is not
authorization, is not a durable workspace identifier, and is useful only with the same
live `SandboxService` instance.

A provider resume must first call `get_session()` with that handle. If the record is
still live, the provider reattaches without allocating. If it is absent, the provider
creates a replacement session and relies on its framework snapshot to hydrate portable
workspace state. The service does not add lookup by caller-selected identifier and does
not expose its runtime registry.
