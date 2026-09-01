# Policy Engine Design

**Status:** Milestone 3 minimal contract implemented; complete policy behavior proposed

## Purpose

The policy engine decides whether a requested sandbox operation is allowed and under what
constraints. It is a deterministic decision component and does not perform the operation
itself.

## Responsibilities

- Evaluate operation identity, owner, path, command, resource, secret, and destination
  context.
- Return explicit allow or deny decisions.
- Narrow requested limits when policy requires stricter bounds.
- Explain decisions with stable reason codes safe for events and errors.
- Compose multiple focused policies with deterministic precedence.
- Remain independent of agent frameworks and concrete infrastructure.

## Out of scope

- Mutating workspace state.
- Executing commands.
- Resolving secret values.
- Formatting model-facing error messages.
- Treating policy approval as operating-system isolation.

## Contract

```python
@dataclass(frozen=True)
class PolicyRequest:
    session: SandboxIdentity
    operation: OperationKind
    path: SandboxPath | None
    command: CommandDescriptor | None
    secret_refs: tuple[SecretRef, ...]
    destination: NetworkDestination | None
    requested_limits: OperationLimits


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str
    effective_limits: OperationLimits
    obligations: tuple[PolicyObligation, ...] = ()


class PolicyEngine(Protocol):
    async def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...
```

The session must receive an explicit decision. `None`, exceptions interpreted as allow,
or silent defaults are prohibited.

## Milestone 3 minimal contract

The minimal vertical slice defines the subset required for the explicit allow-all engine:

```python
class OperationKind(StrEnum):
    EXECUTE = "execute"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    APPLY_PATCH = "apply_patch"
    READ_BYTES = "read_bytes"
    WRITE_BYTES = "write_bytes"
    STAT = "stat"
    LIST_ENTRIES = "list_entries"
    CREATE_SNAPSHOT = "create_snapshot"
    RESTORE_SNAPSHOT = "restore_snapshot"


@dataclass(frozen=True)
class PolicyRequest:
    session_id: SessionId
    operation_id: OperationId
    operation_kind: OperationKind
    path: SandboxPath | None
    command_name: str | None
    requested_limits: OperationLimits


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str
    effective_limits: OperationLimits
```

Lifecycle `start()` and `close()` are not policy operations and use no `OperationKind`.
The Milestone 3 `AllowAllPolicyEngine` returns `allowed=True`, reason code `allow_all`,
and unchanged effective limits. Milestone 4 extends these contracts additively with
identity context, command descriptors, secret references, destinations, obligations, and
additional effective resource limits.

`OperationKind` and `OperationLimits` are shared data values owned by
`mem_sandbox.core.operations`, avoiding a policy-to-session import cycle.

For Milestone 3 execute requests, `path` and `command_name` are `None`; command parsing
and descriptor-level authorization remain owned by the executor and the Milestone 4
command policy.

If policy narrows the timeout, the session recomputes the effective absolute deadline
from the original operation start time, not from the decision time. If the narrowed
deadline leaves no protected-operation budget, the session times out before invoking a
protected collaborator. The caller's original terminal-event reserve remains available
to emit the required timeout event.

Milestone 3 policy may narrow only `timeout_seconds`.
`terminal_event_reserve_seconds` must equal the requested value; a decision that changes
it is invalid and fails before protected collaborator invocation. Milestone 4 may add an
explicit reserve policy only together with terminal-delivery invariants.

## Focused policies

The default engine composes focused rules:

- session ownership and lifecycle
- allowed operation types
- path read/write permissions
- command allowlist and argument rules
- maximum timeout and output size
- workspace and file quotas
- secret reference allowlist
- secret age and lease duration
- destination or egress restrictions
- capability profile restrictions

Workspace quota checks remain authoritative in the workspace because a concurrent or
multi-step mutation may change actual usage after policy evaluation.

## Composition

Recommended evaluation behavior:

1. Validate request shape.
2. Evaluate mandatory security policies.
3. Evaluate capability and operation policies.
4. Intersect all effective limits.
5. Collect obligations.
6. Deny if any policy denies.
7. Return one stable primary reason plus optional diagnostic reason chain.

Deny overrides allow. An engine with no applicable policy uses an explicit configured
default, which should be deny for secrets, egress, host access, and unsupported commands.

## Obligations

An allow decision may require obligations such as:

- redact specified output tokens
- cap timeout or output below the request
- emit an audit-required event
- restrict a secret lease to one command and destination
- require an expected content hash
- mark the operation as host-approval-required

The session verifies that every obligation is supported before proceeding. Unsupported
obligations cause denial.

## Determinism

Given the same normalized request and policy configuration, the engine returns the same
decision. Time-dependent rules receive an injected clock and include the evaluated time
in decision metadata.

Policy configuration is immutable for a running operation. Host updates affect subsequent
operations only.

## Sensitive data

- Requests contain secret references, never secret values.
- Command descriptors may contain redacted argument forms.
- Decision reasons must be safe for logs and model-visible errors.
- Raw file content is not passed to policy unless a specific content policy owns that
  requirement.

## Failure semantics

Stable outcomes include:

- `PolicyAllowed`
- `PolicyDenied`
- `PolicyConfigurationInvalid`
- `PolicyEvaluationFailed`
- `PolicyObligationUnsupported`

Engine failure is not an allow decision. The session surfaces a policy evaluation error
and performs no protected operation.

## Test expectations

- Happy-path allows for every operation kind.
- Deny paths call no workspace, executor, or secret dependency.
- Deny precedence is independent of policy registration order.
- Effective limits are the strictest intersection.
- Boundary values cover path roots, timeouts, output limits, quotas, and lease ages.
- Secret and destination policies evaluate together.
- Injected-clock tests cover expiry exactly at boundaries.
- Reasons and events contain no secret values or unredacted protected arguments.
- Unsupported obligations deny before operation execution.

## Maintenance rule

New protected behavior requires a policy request field, focused rule, stable reason code,
and tests before it is enabled.
