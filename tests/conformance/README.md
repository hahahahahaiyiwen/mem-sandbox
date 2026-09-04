# Direct Core Conformance

**Status:** Issue #21 direct-core reference implemented; adapter reuse begins in Milestone 5

## Purpose

The direct conformance suite proves the complete in-memory core through public service
and session contracts before any agent-framework adapter is implemented. The resulting
normalized trace becomes the reference that later adapter drivers must match.

The suite composes existing component behavior into one lifecycle. It does not replace
focused unit, integration, or generated invariant tests.

The direct suite may include host-lifecycle operations, such as in-place session restore,
that are intentionally absent from the common framework-adapter protocol. Later adapters
compare the shared trace projection; direct-only assertions remain required core
conformance rather than becoming adapter requirements.

## Boundary rules

Conformance tests:

- import only exported `mem_sandbox.<module>` contracts and public concrete in-memory
  composition types;
- invoke runtime behavior only through `SandboxService` and `SandboxSession`;
- may retain injected public snapshot codec/store/gateway and event-sink instances to
  observe persisted snapshots and emitted events;
- assert immutable results, public session properties, public snapshot payloads, and
  collaborator-owned traces;
- never inspect private registry, workspace, session, broker, dispatcher, or lock state;
- never use a host shell, subprocess, temporary host workspace, or host filesystem as a
  behavioral oracle.

Public `OwnerId` values are provenance, not authorization. Fork means repeated
`SandboxService.resume()` from one immutable snapshot reference; there is no separate
fork operation.

## Reference fixture

The test-owned composition retains:

- `SystemClock` and `SystemUuidGenerator`;
- `JsonSessionSnapshotCodec`;
- `InMemorySnapshotStore` and `InMemoryServiceSnapshotGateway`;
- `InMemoryEventSink`;
- `DefaultSessionFactory`;
- `InMemorySandboxService`.

The fixture uses default allow-all policy and no-secret behavior for the primary
lifecycle. Focused conformance cases replace public policy, secret, event, snapshot, or
runtime ports with deterministic fakes at the relevant seam.

## Normalized trace

The direct suite records domain meaning rather than object formatting:

- lifecycle action and stable result or error category;
- command exit code and stable command failure code;
- resulting cwd and approved environment;
- workspace revision after each committed mutation;
- file bytes and content hashes;
- session snapshot state hash and workspace root hash at checkpoints;
- snapshot provenance and source-session relationships;
- event type/order and session/operation identity relationships;
- cleanup outcomes and handle availability.

Random UUID values, timestamps, and measured durations are excluded. Equality,
inequality, ordering, and correlation relationships between identities remain part of
the trace.

`CreateSnapshotResult.content_hash` is the deterministic session state hash, not the
workspace root hash. The fixture obtains the root hash by loading and decoding the
persisted snapshot through its retained public store and codec.

## Stateful lifecycle scenario

`tests/conformance/test_direct_session.py` implements one scripted, model-free
scenario:

1. Create an empty sandbox and obtain its running session.
2. Write deterministic text and binary fixtures through session operations.
3. Execute a command plan that inspects files and changes cwd and approved environment.
4. Perform bounded reads and retain file content hashes.
5. Apply a hash-guarded patch and verify one revision and hash transition.
6. Attempt one stale mutation and prove bytes, revision, cwd, and environment are
   unchanged.
7. Create a snapshot and retain state hash, decoded workspace root hash, revision, cwd,
   approved environment, source-session identity, and creator provenance.
8. Mutate workspace, cwd, and environment beyond the checkpoint, then call
   `SandboxSession.restore_snapshot()` and prove the same session identity returns
   atomically to the checkpoint revision, root hash, cwd, and environment. Verify
   `snapshot.restored` occurs between operation start and completion.
9. Close the source session, prove later operations fail with `SessionClosed`, then
   delete its service handle because session close does not unregister it.
10. Resume the snapshot under a new owner and prove a new handle/session identity with the
   exact checkpoint state.
11. Continue reading, writing, patching, and executing from the restored cwd; prove the
    resumed revision advances without changing the original snapshot.
12. Resume the same snapshot twice more and prove both forks start identically.
13. Mutate each fork differently and prove source, continued session, sibling fork, and
    persisted snapshot isolation.
14. Delete every live handle, prove later lookup/delete returns `SandboxNotFound`, close
    the service idempotently, and clean retained snapshots separately.

Each run also verifies contiguous per-session event sequence, shared operation identity
between start and terminal events, snapshot-specific event ordering, and absence of
unbounded or secret content.

## Behavior-first coverage

| Category | Required conformance behavior |
|---|---|
| Happy path | Complete lifecycle trace; text/binary round trips; stat/list ordering; command cwd/environment persistence; deterministic same-state snapshots; same-identity in-place restore; independent resumes |
| Policy/safety block | Denial for each policy-aware operation family; no protected collaborator call; unchanged workspace/session/snapshot state; sorted unique secret references before source access |
| Dependency failure | Start-event, operation-event, policy, snapshot-save/load, in-place restore prepare/publication, service resume, secret-source, lease-cleanup, and runtime-cleanup failures preserve documented primary outcome and commit boundary |
| Exact boundary | Exact and one-over public request/limit cases succeed or fail atomically without changing unrelated state |
| Timeout/cancellation | Gate wait has no operation/event/collaborator activity; admitted operations emit exactly one terminal outcome; no late mutation; close reaches a terminal state |
| Unsupported behavior | Parser/profile rejection occurs before mutation; unknown host executables return virtual command-not-found; outside-root paths never reach host resources |
| Deterministic replay | Repeated runs from equivalent fixtures produce the same normalized trace, revisions, hashes, errors, and event ordering |

The conformance suite uses representative cross-module cases. Exhaustive generated path,
quota, mutation, snapshot, grammar, and pipeline sequences belong to
[the property-test design](../property/README.md).

In-place session restore remains a direct conformance responsibility because it combines
store load, codec validation, workspace prepare/commit, cwd/environment publication, and
session-owned events. The lower-level workspace snapshot machine does not replace it.

## Failure and mutation assertions

For every denied, invalid, stale, quota, timeout, cancellation, corrupt-snapshot, or
unsupported request, capture the relevant public projection before the operation and
compare it afterward:

- recursive entries and file bytes;
- workspace revision and root hash when exposed by a snapshot checkpoint;
- session cwd and approved environment;
- snapshot store count/bytes where the store is the collaborator under test;
- event sequence and terminal category;
- broker/executor/fake collaborator trace.

Expected normal command non-zero results remain values, not exceptions. Required event
failure after an already committed operation may surface an error while preserving the
documented mutation.

## Implementation and acceptance

- File: `tests/conformance/test_direct_session.py`
- Run: `python -m pytest tests/conformance/test_direct_session.py -q`
- The test must pass on Windows and Ubuntu with Python 3.12 and 3.14.
- The normalized trace must contain no private object representations or unstable timing
  fields.
- Later framework adapters must reuse the logical scenario through a driver and compare
  against this direct trace rather than copying assertions.
