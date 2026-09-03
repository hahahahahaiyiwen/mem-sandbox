# Secret Broker Design

**Status:** Implemented in issue #20

## Purpose

The secret boundary resolves application-approved `SecretRef` values into bounded,
operation-scoped leases. A resolved value may be exposed only through an ephemeral
command-environment overlay for the approved execute operation.

The default `NoSecretBroker` remains the safe configuration and rejects every request.
Applications opt into functional leasing by injecting both a functional broker/source
and a policy implementation appropriate for the references they expose.

## Responsibilities

- Define opaque references, protected values, source, broker, lease, and limit contracts.
- Resolve a reference only after the session receives an explicit policy approval.
- Bound requested duration and total active/pending leases.
- Assign absolute UTC expiration from an injected clock.
- Return independent, idempotently closeable leases.
- Fail value access immediately after expiry or close.
- Keep value text and bytes out of errors and useful object representations.
- Release broker capacity exactly once on close or failed source resolution.

## Out of scope

- Authenticating callers or deciding application principal-to-reference access.
- Network destination binding before a network-capable command or adapter exists.
- Persisting, rotating, or versioning source values.
- Secret caches without a separately approved secure-cache policy.
- Writing resolved values into workspace files, paths, snapshots, or session state.
- Returning secret values through command results, events, diagnostics, or traces.
- Protecting values from arbitrary same-process debuggers, hostile collaborators, memory
  inspection, or traceback tooling configured to capture local variables.
- A general policy rule language or composed policy engine.

## Public contracts

The initial functional boundary remains text-oriented because the approved integration
target is an environment-variable overlay:

```python
@dataclass(frozen=True, slots=True)
class SecretRef:
    name: str


@dataclass(frozen=True, slots=True, repr=False)
class SecretValue:
    _value: str

    def reveal_text(self) -> str: ...
    def reveal_bytes(self) -> bytes: ...


class SecretSource(Protocol):
    async def resolve(self, secret_ref: SecretRef) -> SecretValue: ...


@dataclass(frozen=True, slots=True)
class SecretAccessRequest:
    session_id: SessionId
    operation_id: OperationId
    secret_ref: SecretRef
    command_name: str | None
    max_lease_seconds: float


class SecretLease(Protocol):
    @property
    def value(self) -> SecretValue: ...
    @property
    def expires_at(self) -> datetime: ...
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class SecretBrokerLimits:
    max_active_leases: int
    max_lease_seconds: float
    max_value_bytes: int


class SecretBroker(Protocol):
    async def lease(self, request: SecretAccessRequest) -> SecretLease: ...
```

`SecretRef` names remain bounded opaque application names, not provider resource IDs.
They are safe policy facts but do not imply existence or authorization.

`SecretValue`, concrete leases, ephemeral overlay values, command requests/results
containing expanded values, environment changes, and prepared command artifacts have
non-revealing representations. There is no implicit `str()` conversion for secret
material.

Issue #20 deliberately replaces the pre-functional `SecretValue.reveal()` method with
the explicit `reveal_text()` and `reveal_bytes()` methods. This is a pre-1.0 breaking
contract change that makes every materialization site searchable and testable.

`command_name` remains `None` in issue #20 because the session does not own command
parsing and the executor does not yet expose an approved prepared-command summary.
Command- or destination-specific authorization requires a later executor-owned prepared
artifact. Issue #20 authorizes the execute operation's declared references as a set.

## Functional source and broker

The first functional source is `MappingSecretSource`, an injected deterministic mapping
implementation for application composition and tests. It returns `SecretValue` and
reports missing references without including resolved material. The broker never reads
the host process environment implicitly.

Resolved text must encode as at least one and at most `max_value_bytes` UTF-8 bytes.
Empty, invalid UTF-8, or oversized source values fail with `SecretSourceValueInvalid`
before redactor or overlay construction. Short non-empty values are allowed but
intentionally cause conservative exact-match redaction and persistence rejection;
applications should use high-entropy values to avoid excessive false positives.

The process-local bounded broker is constructed with:

```python
BoundedSecretBroker(
    source: SecretSource,
    *,
    limits: SecretBrokerLimits,
    clock: Clock,
)
```

Lease admission is atomic:

1. Validate the typed request and capture one UTC clock value.
2. Purge expired broker records and reserve one active-lease slot under the broker lock.
3. Resolve the reference outside the lock.
4. Release the reservation if resolution fails or the caller is cancelled.
5. Assign:

   ```text
   expires_at = accepted_at + min(
       request.max_lease_seconds,
       limits.max_lease_seconds,
   )
   ```

6. Return one independent lease that owns the reserved slot.

Pending source resolutions count toward `max_active_leases`; otherwise concurrent slow
source calls could exceed the configured bound. The broker does not hold its state lock
while awaiting the source.

The session sets `request.max_lease_seconds` from the remaining effective collaborator
budget after policy has narrowed the operation deadline. A lease therefore cannot
outlive the approved operation budget. The broker may shorten that duration further.

## Lease lifecycle

- Access before `expires_at` returns the protected value.
- Access at `clock.now() >= expires_at` raises `SecretExpired`.
- Access after close raises `SecretLeaseClosed`, even if the lease is also expired.
- Close is idempotent and safe under concurrent calls.
- Close decrements broker accounting exactly once in `finally`, even if a close callback
  reports a failure.
- One lease cannot be reused for another session or operation.
- Two approved operations receive independent lease objects, even for the same reference.
- A closed or expired lease is never returned to a cache or later operation.

The broker does not run an expiry worker. Expiration blocks value access immediately.
Lease admission lazily releases slots held by expired records, while explicit close
releases them eagerly. The session always closes leases, so normal operation cleanup
does not depend on lazy purge.

## Authorization and ordering

`SandboxSession` owns the enforcement order:

```text
validate request bindings
  -> emit operation.started
  -> policy evaluates the normalized reference set
  -> deny without broker/source calls
  -> acquire unique references in reference-name order
  -> construct the operation redactor and ephemeral overlay
  -> execute
  -> close every acquired lease in reverse order
  -> emit one terminal event
```

The minimal `PolicyRequest` gains only normalized `secret_refs`. This is the concrete
authorization fact required by issue #20; it does not introduce rule composition,
obligations, caller identity, command parsing, or destination policy.

`AllowAllPolicyEngine` approves the supplied references unchanged. It is safe only when
the application intentionally exposes every configured source reference. The
`NoSecretBroker` default still makes accidental materialization fail closed.

## Execute request and overlay

`SessionExecuteRequest` declares immutable bindings:

```python
@dataclass(frozen=True, slots=True)
class SessionSecretEnvironmentBinding:
    name: str
    secret_ref: SecretRef


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionExecuteRequest:
    command: str
    secret_environment: tuple[SessionSecretEnvironmentBinding, ...] = ()
    ...
```

Rules:

- Environment names use the command executor's existing name grammar.
- `PWD` is reserved and cannot be bound.
- `CommandLimits.max_secret_bindings`, defaulting to 16, bounds both binding count and
  unique references for one execute request.
- Duplicate environment names are rejected before operation admission. Multiple names
  may use one reference; policy evaluation and lease acquisition de-duplicate that
  reference while the overlay retains every binding.
- Bindings are normalized into deterministic environment-name order.
- Overlay values shadow the session's approved environment and same-plan
  `export`/`unset` changes while the operation is active.
- Overlay values are never returned as `EnvironmentChange` and never committed to
  `SandboxSession.environment`.
- Empty bindings preserve the current no-broker-call behavior.

The command executor receives the base `CommandEnvironment` and a separate
`CommandEnvironmentOverlay`; it never receives a merged persistent environment object.
The executor constructs a non-revealing `CommandEnvironmentView` for parsing and command
handlers. The view includes overlay names, but every model-visible output remains
redacted. The overlay resolves value access through the live lease so close and expiry
checks remain effective.

## Operation redaction and persistence guard

After all leases are acquired, the session constructs one operation-local
`ProtectedValueRedactor` from every exact text and UTF-8 byte representation. The
redactor is not added to the session dispatcher or retained after the operation.

The command executor owns a narrow protection port in its execution context:

```python
class CommandValueProtection(Protocol):
    def redact_text(self, value: str) -> str: ...
    def redact_bytes(self, value: bytes) -> bytes: ...
    def contains_protected_text(self, value: str) -> bool: ...
```

The executor and command handlers use it to enforce:

- redact each stage's stdout and stderr before pipeline forwarding, redirection, output
  collection, or result construction;
- reject protected expanded command names and arguments before command resolution or
  handler invocation;
- reject a redirection destination containing a protected value before workspace access;
- reject persistent environment changes containing a protected value;
- reject protected values used as workspace path operands before workspace access;
- ensure generated diagnostic messages are redacted before becoming results or errors;
- keep internal expanded command/prepared artifacts non-revealing through `repr`.

`ProtectedValueRedactor` remains owned by the event module. The session owns a small
operation adapter that wraps it and implements `contains_protected_text()` by comparing
the original and redacted values. The command executor depends only on its own
`CommandValueProtection` protocol and does not import the event module.

Redaction before pipeline forwarding intentionally prevents a later stage from encoding
or transforming a trusted handler's secret output. Generic protected arguments are
rejected before dispatch because a command could otherwise disclose derived facts
through matching, option parsing, exit status, or control flow. Exact matching is
defense in depth; the command path also rejects direct persistence surfaces rather than
assuming redaction alone is sufficient.

If an operation attempts to persist protected material, the executor returns the stable
normal command failure `PROTECTED_VALUE_REJECTED`. It does not silently drop an
environment change or write a redaction marker as if persistence had succeeded.

Any `export` or `unset` targeting an overlaid name is also rejected, even when the new
value is non-secret. This prevents an operation from mutating a shadowed base value whose
effective value during the plan was supplied by a lease.

`stdout_original_bytes`, `stderr_original_bytes`, and truncation flags are calculated
from the redacted stream accepted by the bounded collectors. They do not reveal the
original secret length.

## Cleanup and failure precedence

The session closes all acquired leases in reverse acquisition order in `finally` across:

- normal zero or non-zero command completion;
- policy-independent broker/source failure after earlier leases were acquired;
- command exception;
- operation timeout;
- cooperative cancellation;
- native task cancellation;
- required event failure after command completion.

Cleanup is interruption-resilient and continues after an individual close failure,
including failures outside the ordinary `Exception` hierarchy. An interruption of the
outer operation and the original operation failure remain primary; cleanup failures are
attached as safe exception notes. When cleanup is the only failure, the session raises
`SecretLeaseCleanupFailed` and does not commit transient cwd or environment changes.

Workspace mutations completed before a later command or cleanup failure follow the
existing command transaction semantics and may remain. Protected-value guards must
therefore run before any mutation that could persist secret material.

If a lease expires between plan units or stages, access raises `SecretExpired`. Earlier
non-secret workspace mutations remain under existing command semantics, while transient
cwd/environment changes are discarded and every lease is closed. The executor never
extends a lease or converts expiry into a normal success result.

## Stable failures

- `SecretReferenceInvalid`
- `SecretDenied`
- `SecretNotFound`
- `SecretExpired`
- `SecretLeaseLimitExceeded`
- `SecretSourceUnavailable`
- `SecretSourceValueInvalid`
- `SecretLeaseClosed`
- `SecretLeaseCleanupFailed`

Errors may include the safe reference name, environment name, operation ID, or stable
reason code. They never include source values, overlay values, expanded secret-bearing
arguments, or unredacted collaborator text.

## Behavior-first test matrix

### Values and source

- References accept exact boundary-length names and reject invalid syntax/types.
- `SecretValue`, source, lease, overlay, expanded request, and prepared-plan
  representations do not contain the canary.
- Text and UTF-8 byte reveals are exact and immutable.
- Missing and unavailable source failures remain distinct and value-free.
- The source is never called by `NoSecretBroker`.

### Broker admission and leases

- Exact active-count and maximum-duration boundaries succeed.
- The first active-count overflow fails with `SecretLeaseLimitExceeded`; a requested
  duration above the configured maximum is shortened to the exact maximum.
- Empty and one-byte/maximum-byte/one-byte-over-maximum source values exercise the
  conservative value-validation boundaries.
- Pending slow source resolution consumes capacity.
- Failed or cancelled source resolution releases capacity exactly once.
- Admission lazily releases capacity held by expired records.
- Expiry succeeds one clock tick before the boundary and fails exactly at the boundary.
- Close is concurrent/idempotent, releases capacity once, and makes later access fail
  with `SecretLeaseClosed`.
- Concurrent leases for the same reference are independent.
- Injected clocks are used; tests never sleep for expiry.

### Policy and source ordering

- Policy receives sorted unique references and no values.
- Denial calls neither broker nor source.
- Policy failure is not interpreted as allow.
- A narrowed policy deadline narrows every requested lease duration.
- Empty bindings call neither broker nor source.
- A later lease failure closes every earlier lease and never invokes the executor.

### Execute overlay

- Approved bindings are visible internally through variable expansion, the merged
  command environment view, and a fake command-handler positive control. `env` may list
  the overlay name, but its value is `[REDACTED]` in externally visible output.
- Generic protected expanded command names, arguments, and redirection destinations fail
  before dispatch; only an explicitly trusted fake handler reads a leased value directly
  for positive-control coverage.
- Overlay values shadow base and same-plan environment changes without mutating either.
- `PWD`, duplicate names, and unsupported binding types fail before operation
  publication. Multiple bindings may share one reference.
- The exact `max_secret_bindings` boundary succeeds and the first excess binding fails
  before operation publication.
- Successful command environment changes that do not contain protected values still
  commit normally.
- Secret-derived `export`/persistent changes fail with
  `PROTECTED_VALUE_REJECTED` and do not commit.
- `export` and `unset` targeting an overlaid name fail without changing a pre-existing
  base value.
- Expired or closed lease access during parsing/dispatch fails with its distinct error.
- Mid-plan expiry preserves earlier non-secret workspace mutations, discards transient
  cwd/environment changes, and closes every lease.

### Persistence and observation canaries

Use one distinctive Unicode canary and assert its UTF-8 bytes are absent from:

- every workspace path and file payload after direct output, redirection, pipelines, and
  path-mutating commands;
- session cwd and approved environment before and after execution;
- snapshots and canonical snapshot payload bytes;
- `SessionExecuteResult`, `ExecuteResult`, normal command failures, raised errors,
  exception causes/notes, and core/default formatted tracebacks without local-variable
  capture;
- operation and lifecycle events, canonical event bytes, sink diagnostics, and event
  queries;
- command-history or prepared-plan state retained by the executor;
- `repr()` and `str()` of protected values, leases, overlays, command requests,
  contexts, prepared units, and tasks.

Positive controls prove the canary was actually resolved and visible to the command
handler. Output controls expect `[REDACTED]`, not the canary.

Include a short canary that is a substring of ordinary output to prove conservative
redaction remains leak-free even when it over-redacts.

### Cleanup

- Every lease closes after success, non-zero result, executor failure, timeout,
  cooperative cancellation, native cancellation, and terminal event failure.
- Cleanup continues in reverse order after one close fails.
- Cleanup continues after a lease close raises an arbitrary `BaseException`; unsafe
  details never become the surfaced cleanup failure.
- Concrete lease close failure still releases broker capacity exactly once.
- One cancelled waiter cannot orphan shared lease cleanup.
- Primary timeout/cancellation/failure remains primary with safe cleanup notes.
- Cleanup-only failure prevents cwd/environment commit and raises
  `SecretLeaseCleanupFailed`.

## Maintenance rule

Any new way for secret material to enter execution, paths, files, environment, output,
errors, telemetry, snapshots, or object representations requires updates to this
document and the cross-module canary tests in the same change.

Issue #20 is a foundation milestone: because the approved command profile has no network
or external-service command, leased values produce no externally visible capability
beyond proving protected access, redaction, rejection, expiry, and cleanup. Practical
destination-bound use remains deferred.
