# Event Collection and Delivery Design

**Status:** Issue #18 implemented; issue #20 secret-canary coverage implemented

## Purpose

The event system records bounded structured facts about sandbox lifecycle, operations,
snapshots, and failures. Core code emits domain events through a narrow interface and
does not depend on logging, telemetry, or agent-framework SDKs.

Events are observability records, not the source of truth for workspace or session state.

## Responsibilities

- Define stable immutable event identities, types, categories, and classified attributes.
- Assign unique monotonic sequence numbers per session.
- Redact registered protected representations before an event reaches a sink.
- Enforce deterministic field and complete-payload bounds.
- Deliver events through one immutable required or best-effort policy per session.
- Route every best-effort failure to an explicit diagnostic handler.
- Provide a bounded process-local sink with deterministic queries.
- Keep sink flushing and closing under the constructing resource owner's control.

## Out of scope

- OpenTelemetry or logging-vendor dependencies in core.
- Raw file content, command text, stdout, stderr, environment values, host paths, or
  secret values in default event payloads.
- Policy-decision and secret lifecycle events; issue #20 adds no new event types.
- Treating event delivery as an operating-system isolation or authorization boundary.
- Agent-framework callback APIs.

## Future resource events

Milestones 7 through 9 may add bounded child resource events for repository transfer,
accounting, HTTP, and external execution. Those events remain correlated with one parent
sandbox operation and follow the same classification, redaction, payload, sequencing,
delivery, and failure rules.

The resource-owning module defines its event facts; it does not emit raw provider
exceptions through the generic sink. Candidate facts include resource kind, outcome,
stable reason, bounded usage, destination class, runtime/profile identifier, workspace
hash, and publication outcome. Full URLs, queries, headers, bodies, source code, command
arguments, stdout, stderr, workspace content, host paths, and secret values remain
excluded by default.

Required audit availability must be decided before an irreversible external side effect.
Adding resource events does not retroactively make current policy-decision or secret
lifecycle events part of the implemented profile.

## Boundary ownership

`SandboxSession` consumes only the emit capability:

```python
class SessionEventSink(Protocol):
    async def emit(self, event: SandboxEvent) -> None: ...
```

The constructing resource owner may receive the richer lifecycle contract:

```python
class OwnedEventSink(SessionEventSink, Protocol):
    async def flush(self) -> None: ...
    async def close(self) -> None: ...
```

The session never calls `flush()` or `close()`. A per-session configured dispatcher may
wrap a shared concrete sink and expose only `emit()` to the session. The dispatcher,
worker task, and concrete sink lifecycle belong to the composition root or the
session-owned `SessionResourceScope`.

## Event identity and categories

An event identity is derived from the existing session identity and positive sequence:

```python
@dataclass(frozen=True, order=True)
class EventId:
    session_id: SessionId
    sequence: int


@dataclass(frozen=True)
class SandboxEvent:
    event_type: SandboxEventType
    occurred_at: datetime
    session_id: SessionId
    sequence: int
    operation_id: OperationId | None
    parent_operation_id: OperationId | None
    operation_kind: OperationKind | None
    attributes: tuple[EventAttribute, ...]

    @property
    def event_id(self) -> EventId: ...
```

`event_id` is computed rather than stored. This prevents disagreement between an
independent identifier and the `(session_id, sequence)` source of truth and avoids
consuming UUIDs that are otherwise used for operation and snapshot identities.

Event categories and issue #18 producers are:

| Category | Event types | Producer |
|---|---|---|
| Lifecycle | `sandbox.started`, `sandbox.closing`, `sandbox.closed`, `sandbox.failed` | `SandboxSession` |
| Operation | `operation.started`, `operation.completed`, `operation.failed`, `operation.cancelled`, `operation.timed_out` | `SandboxSession` |
| Snapshot | `snapshot.created`, `snapshot.restored` | `SandboxSession` post-commit hook |
| Service lifecycle | `sandbox.created`, `sandbox.deleted` | Defined but producer-less until a shared service/session sequencing design is approved |

Policy and secret event types are not added by issue #20. File and command details remain
bounded operation attributes rather than creating an unbounded vendor-specific event
taxonomy. Secret references and environment binding names are intentionally omitted from
the existing operation events.

Each session assigns sequence numbers when events are created. Accepted events are unique
and strictly increasing per session. A failed delivery may leave a gap, so consumers must
not require contiguity.

## Classified attributes

Every attribute carries an explicit sensitivity:

```python
type EventValue = str | int | float | bool | None


class EventSensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PROTECTED = "protected"
    SECRET = "secret"


@dataclass(frozen=True, order=True)
class EventAttribute:
    name: str
    value: EventValue
    sensitivity: EventSensitivity
```

Classification cannot be omitted. Event producers assign it when constructing the
attribute.

- `PUBLIC` is safe for ordinary external observability.
- `INTERNAL` is bounded operational metadata such as sandbox-normalized paths, typed
  identifiers, revisions, counts, durations, hashes, canonical command names, and
  snapshot schema metadata.
- `PROTECTED` is excluded by default. It is accepted only when an explicit event payload
  policy enables protected attributes and the value passes through structural redaction.
- `SECRET` is always rejected. Secret values do not become acceptable merely because a
  redactor exists.

Sandbox-normalized paths may be `INTERNAL`. Host filesystem paths are always excluded.

## Default payload policy

Allowed by default:

- operation kind and terminal status;
- sandbox-normalized path;
- canonical command name without full command text or arguments;
- exit code and stable failure code;
- duration and bounded byte/node counts;
- workspace revision and content hash;
- snapshot identifier, schema version, and process-local indicator.

Excluded by default:

- file or patch content;
- full command text or unredacted argument values;
- stdout and stderr;
- raw environment names or values;
- host filesystem paths;
- secret values or provider credentials.

Adding a new field requires a named producer, sensitivity classification, bounded
representation, and behavior tests.

## Payload validation and canonical bytes

`EventPayloadLimits` controls:

- maximum attribute count;
- maximum UTF-8 attribute-name bytes;
- maximum UTF-8 string-value bytes;
- maximum canonical complete-event bytes.

Validation rejects non-finite floats and unsupported value types. UTF-8 input is preserved
exactly; the event system performs no Unicode normalization.

Canonical bytes use UTF-8 JSON with:

- object keys sorted lexically by their UTF-8 representation;
- attributes sorted by attribute name;
- no insignificant whitespace;
- `ensure_ascii=False`;
- `allow_nan=False`;
- fixed UTC timestamps encoded with microseconds and a trailing `Z`;
- typed identifiers and enums encoded through their canonical string values;
- standard JSON `true`, `false`, and `null`;
- Python's deterministic finite-float shortest-round-trip representation.

The complete envelope, not only the attribute mapping, counts toward the payload limit.
Two events that differ only in input mapping order produce identical canonical bytes.

## Redaction pipeline

`ProtectedValueRedactor` accepts exact protected text and byte representations. Empty
representations are invalid. Redaction:

- replaces every exact match with `[REDACTED]`;
- evaluates longer registered values before shorter overlapping values;
- merges matches that overlap at different offsets so no registered representation is
  partially re-exposed;
- replaces repeated and adjacent values without rescanning replacement output;
- leaves the source event unchanged and returns a new immutable event;
- protects a decoded text representation when registered bytes are valid UTF-8;
- does not infer hexadecimal, base64, URL-encoded, or other derived forms unless those
  forms are registered explicitly.

Event payloads do not accept bytes, so byte redaction is also exposed independently for
future encoders and protected collaborators.

The configured event dispatcher owns the mandatory delivery pipeline:

```text
construct classified event
  -> reject SECRET or disallowed PROTECTED attributes
  -> redact every string attribute against registered protected values
  -> validate field limits and finite scalar values
  -> calculate and validate canonical complete-event bytes
  -> invoke or enqueue for the concrete sink
```

Redaction therefore occurs before the final field and aggregate byte checks and before
sink invocation. A caller cannot bypass redaction by invoking the concrete sink through
the session boundary.

Issue #20 does not mutate a per-session dispatcher's registered values. The session
creates a separate operation-local redactor after lease acquisition and passes it to the
command executor's protection port. Existing events contain no command output,
environment, reference, or exception text, so the event dispatcher continues to enforce
its static classified payload rules.

The secret canary suite nevertheless treats events as an external observation boundary:
the canary must be absent from source events, prepared events, canonical bytes, queued
best-effort events, sink diagnostics, and query results across success, denial, source
failure, command failure, timeout, cancellation, and lease-cleanup failure.

## Delivery modes

One immutable delivery policy is configured for each session. `REQUIRED` is the default.

### Required

- Event preparation and sink acceptance complete inline.
- Failure of `sandbox.started` leaves the session failed.
- Failure of `operation.started` prevents policy admission and the protected operation.
- Terminal-event failure is surfaced after any already committed workspace or session
  state and does not roll that state back.
- Event delivery uses the existing operation or lifecycle deadline.
- Native `asyncio.CancelledError` propagates unchanged.

### Best effort

Best effort requires an injected synchronous diagnostic failure handler. Construction
without that handler is invalid.

A per-session dispatcher prepares the event and enqueues it without waiting for the
concrete sink. One owner-managed serial worker drains the queue in emission order. This
keeps sink latency, timeout, and failure from changing session lifecycle or operation
outcomes.

- preparation, queue-overflow, sink, and worker failures each produce exactly one
  immutable diagnostic;
- start-event failure does not prevent the protected operation;
- terminal and lifecycle failure does not change the published result or session state;
- the diagnostic handler must be non-blocking and should not raise;
- if the handler raises, the dispatcher reports that secondary failure through
  `asyncio`'s loop exception handler and preserves the original operation outcome;
- worker tasks, queue draining, `flush()`, and `close()` are owned outside
  `SandboxSession`.

Best-effort queue capacity is finite. Queue overflow reports a diagnostic and drops the
new event; it never evicts an older accepted event.

## Snapshot event ordering

`SandboxSession._run_operation` has a first-class post-commit event hook. Snapshot
events use the terminal-event budget and are emitted after the snapshot side effect or
restored state is published, but before `operation.completed`:

```text
operation.started
  -> snapshot side effect or restore publication
  -> snapshot.created | snapshot.restored
  -> operation.completed
```

The post-commit snapshot event receives the first half of the configured terminal
reserve, preserving the second half for the final operation event attempt.

In required mode, failure of the dedicated snapshot event produces
`operation.failed` and a public operation failure while the already committed snapshot
or restored state remains committed. In best-effort mode, the failure becomes a
diagnostic and `operation.completed` remains eligible.

## Process-local in-memory sink

`InMemoryEventSink` has immutable positive limits for accepted event count and aggregate
canonical payload bytes.

- A new event is rejected atomically when either limit would be exceeded.
- Accepted events are never evicted.
- Rejection changes neither retained events, retained bytes, nor per-session sequence
  high-water marks.
- Duplicate or non-increasing accepted sequences for one session are rejected; gaps are
  allowed.
- Concurrent calls are serialized by the sink.
- Queries may filter by session, operation, category, and event type.
- Query results use deterministic order:
  `(occurred_at, session_id, sequence)`.
- `flush()` and `close()` are idempotent.
- `emit()` after close raises `EventSinkClosed`.
- Queries remain available after close.

This sink is intended for tests and process-local inspection. A full required-delivery
buffer intentionally rejects later operations once full; production audit storage should
use an appropriately durable sink rather than silently evicting accepted records.

## Failure semantics

Stable event failures include:

- `EventInvalid`;
- `EventPayloadRejected`;
- `EventBufferLimitExceeded`;
- `EventDeliveryFailed`;
- `EventSinkClosed`.

Required-mode failures are translated through the existing session delivery errors.
Best-effort failures are diagnostics and never become operation success or failure
signals.

## Behavior-first test matrix

### Models and canonical payloads

- Event ID is the canonical derived `(session_id, sequence)` value and cannot disagree
  with the envelope.
- Events, attributes, limits, and delivery policy are immutable.
- UTC, positive sequence, supported event type, category mapping, unique attribute name,
  explicit classification, scalar type, and finite-float validation are exact.
- Attribute-count, key-byte, string-byte, and complete canonical-byte limits pass at the
  boundary and reject one byte or item beyond it.
- Attribute input order does not change canonical bytes.
- Caller-owned input collections cannot mutate the event after construction.
- `SECRET` is always rejected; `PROTECTED` requires explicit opt-in and structural
  redaction.

### Redaction

- Exact text and byte matches at start, middle, and end are replaced.
- Longest-match-first behavior handles overlaps.
- Repeated, adjacent, and replacement-marker-like values cannot reconstruct or re-expose
  protected text.
- Empty registrations are rejected.
- Valid UTF-8 byte registration protects the corresponding text representation.
- Hexadecimal and base64 forms remain unchanged unless separately registered.
- The source event remains unchanged.
- A sink spy observes only the redacted event.
- Redaction occurs before final field and canonical-byte validation.

### In-memory collection

- Count and aggregate-byte limits accept the exact boundary.
- One-event or one-byte overflow rejects the new event atomically without eviction.
- Duplicate and decreasing sequence values are rejected per session; gaps and interleaved
  fixed session IDs are accepted.
- Concurrent emissions remain serialized.
- Queries filter correctly and return deterministic order under a fixed clock and fixed
  identifiers.
- Flush and close are idempotent, emit-after-close fails, and queries remain available.

### Delivery modes

- Required start, terminal, lifecycle, timeout, and cancellation behavior remains
  backward compatible.
- Best-effort construction without a diagnostic handler is rejected.
- Best-effort preparation, queue, sink, and worker failures produce exactly one
  diagnostic and do not change operation results or lifecycle state.
- Best-effort start failure still permits terminal event enqueue.
- Diagnostic-handler failure reaches the loop exception handler without becoming a
  second diagnostic or changing the operation.
- Native task cancellation propagates; cooperative cancellation and operation timeout
  still produce their distinct terminal event attempts.
- `SandboxSession` never invokes `flush()` or `close()`.

### Session and snapshots

- Every successful operation has one start and one terminal operation event.
- Sequence values are unique and strictly increasing; normal no-failure paths remain
  contiguous while failed attempts may leave gaps.
- Snapshot create and restore use exact post-commit, pre-completion ordering.
- Required snapshot-event failure leaves committed snapshot/state intact and emits
  `operation.failed`.
- Best-effort snapshot-event failure reports a diagnostic and still permits
  `operation.completed`.
- No policy or secret event is emitted by issue #20.
- `sandbox.created` and `sandbox.deleted` remain intentionally producer-less until a
  shared service/session event sequencer or separate service-event identity is approved.
- Default session events contain no file content, full command text, stdout/stderr,
  environment values, host paths, or secrets.
- The exact secret canary is absent from source/prepared events, canonical bytes,
  best-effort diagnostics, and in-memory query results for every lease outcome.

## Maintenance rule

New event types, attributes, redaction forms, delivery behavior, or retention policies
require a sensitivity review, exact boundary tests, and an update to this README in the
same change.
