# Event Sink Design

**Status:** Milestone 3 required-delivery envelope implemented; complete event system proposed

## Purpose

The event system records structured sandbox lifecycle, policy, command, filesystem,
snapshot, and failure facts. The core emits domain events through a narrow sink and does
not depend on logging or telemetry vendors.

## Responsibilities

- Define stable event envelopes and event kinds.
- Accept session-local sequence numbers and operation correlation assigned by the
  session or another per-session emitter.
- Deliver events according to an explicit reliability mode.
- Redact protected content before it reaches a sink.
- Support process-local collection for tests and development.
- Allow adapters outside core to translate events to logs, metrics, traces, or audit
  stores.

## Out of scope

- OpenTelemetry SDK dependencies in core.
- Storing raw file content by default.
- Storing secret values.
- Treating event delivery as the source of truth for workspace state.
- Agent-framework callback APIs.

## Contract

```python
@dataclass(frozen=True)
class SandboxEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    session_id: SessionId
    sequence: int
    operation_id: OperationId | None
    parent_operation_id: OperationId | None
    operation_kind: OperationKind | None
    data: Mapping[str, EventValue]


class SandboxEventSink(Protocol):
    async def emit(self, event: SandboxEvent) -> None: ...
    async def flush(self) -> None: ...
    async def close(self) -> None: ...
```

Events are immutable after construction. `SessionId` and `OperationId` remain typed
inside the core; event exporters convert them to strings only when encoding an external
event envelope.

## Event categories

Lifecycle:

- `sandbox.created`
- `sandbox.started`
- `sandbox.closing`
- `sandbox.closed`
- `sandbox.failed`
- `sandbox.deleted`

Operations:

- `operation.started`
- `operation.completed`
- `operation.failed`
- `operation.cancelled`
- `operation.timed_out`

Decisions and resources:

- `policy.allowed`
- `policy.denied`
- `secret.lease_created`
- `secret.lease_closed`
- `snapshot.created`
- `snapshot.restored`

File and command details are represented as operation data rather than an unbounded set of
vendor-specific events.

## Event data policy

Allowed by default:

- normalized path
- command name and redacted argument summary
- exit code
- duration
- byte and node counts
- content hashes
- policy reason code
- snapshot identifier and schema version

Excluded by default:

- file content
- full command text when it may contain sensitive arguments
- stdout and stderr
- secret values
- raw environment variables
- host filesystem paths

An explicit event payload policy may include bounded outputs after redaction.

## Ordering

- Each session assigns a monotonic sequence at event creation.
- Start precedes terminal events for the same operation.
- Completion order, not invocation order, determines terminal event sequence.
- Timestamps use an injected UTC clock.
- Cross-session total ordering is not guaranteed.

## Delivery modes

`BEST_EFFORT`:

- operation correctness does not depend on sink availability
- failures are routed to a configured diagnostic handler
- failures are never silently ignored

`REQUIRED`:

- selected audit events must be accepted before the protected operation proceeds or
  reports success
- sink failure produces `EventDeliveryFailed`

The configured mode and required event kinds are explicit. Broad exception swallowing is
not allowed.

### Milestone 3 selection

The minimal `SandboxSession` vertical slice uses `REQUIRED` delivery. Its concrete
`NoOpEventSink` is a real accept-and-discard implementation, not the absence of a sink.

- Milestone 3 defines the minimal immutable event envelope needed for lifecycle,
  operation-start, and terminal events.
- That minimal envelope contains event type, UTC occurrence time, session sequence,
  session ID, optional operation/parent IDs, optional operation kind, and bounded safe
  data. A separately allocated `event_id` is deferred with the complete event system.
- The session constructs events and assigns monotonic sequence numbers; the sink does not
  maintain per-session counters.
- Operation events carry an explicit `operation_kind`; lifecycle events use `None`.
- The M3 `data` mapping is empty or contains only bounded non-sensitive identifiers and
  result metadata required by the vertical-slice assertions.
- Start-event failure prevents policy evaluation and mutation.
- Terminal-event failure is surfaced explicitly.
- Terminal-event failure does not roll back already committed workspace or session state.
- `BEST_EFFORT` delivery is deferred until a diagnostic failure handler is implemented.

Dedicated snapshot, sensitivity-classification, redaction, and bounded in-memory
collection behavior remain in Milestone 4. Policy-decision and secret events are deferred
with composed policy and functional secret resolution.

## In-memory sink

The first sink stores immutable events in sequence order and supports test queries by
session, operation, and event type. It has configurable event-count and payload-byte
limits to prevent unbounded memory growth.

This sink is for behavior validation and local inspection, not durable auditing.

## Adapter model

Adapters outside core may translate events into:

- structured logs
- OpenTelemetry spans and metrics
- JSONL journals
- durable audit storage
- framework callbacks

Translation never changes operation behavior or feeds data back into the workspace.

## Failure semantics

Stable errors include:

- `EventInvalid`
- `EventPayloadRejected`
- `EventBufferLimitExceeded`
- `EventDeliveryFailed`
- `EventSinkClosed`

## Test expectations

- Every operation emits one start and one terminal event.
- Sequence numbers are unique and monotonic per session.
- Policy denial includes no operation side effect.
- Timeout and cancellation use distinct event types.
- Event payload policy excludes file content, environment values, and secrets.
- Redaction is applied before sink invocation.
- Best-effort and required delivery modes have distinct tested behavior.
- Flush and close are idempotent.
- In-memory limits fail or evict only according to configured policy.

## Maintenance rule

New event fields or payload classes require a sensitivity review, stable schema behavior,
and updates to this document.
