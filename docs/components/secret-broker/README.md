# Secret Broker Design

**Status:** Proposed detailed design under the approved high-level architecture

## Purpose

The secret broker resolves opaque secret references into short-lived, operation-scoped
leases after policy approval. It prevents agent-visible requests, workspace state,
snapshots, errors, and events from carrying durable secret values.

## Responsibilities

- Resolve approved `SecretRef` values through an injected source.
- Bind each lease to a session, operation, command, and optional destination.
- Enforce expiry and maximum age.
- Revoke or dispose leases deterministically.
- Register values with output redaction without exposing them to event payloads.
- Surface missing, denied, expired, and source failures distinctly.

## Out of scope

- Deciding whether access is allowed; that belongs to policy.
- Persisting secret values.
- Automatically exposing the host environment.
- Writing secrets into files or snapshots.
- Providing a general dependency-injection container.

## Contract

```python
@dataclass(frozen=True)
class SecretRef:
    name: str


@dataclass(frozen=True)
class SecretAccessRequest:
    session_id: str
    operation_id: str
    secret_ref: SecretRef
    command_name: str | None
    destination: NetworkDestination | None
    max_lease_seconds: float


class SecretLease(Protocol):
    @property
    def value(self) -> SecretValue: ...
    @property
    def expires_at(self) -> datetime: ...
    async def close(self) -> None: ...


class SecretBroker(Protocol):
    async def lease(self, request: SecretAccessRequest) -> SecretLease: ...
```

`SecretValue` is a protected wrapper rather than a plain string. Conversion to bytes or
text happens only at the command boundary that needs it.

## Resolution flow

1. The session parses secret references without resolving them.
2. The policy engine evaluates reference, command, destination, and requested duration.
3. The session requests a lease using the effective policy limits.
4. The broker obtains the value from its configured source.
5. The value is registered with the operation's redactor.
6. The command receives only the required lease.
7. The lease closes in `finally`, including timeout and cancellation paths.

Secret lookup never occurs before policy approval.

## Secret references

References are names such as `secretRef:service-token`, not provider-specific resource
identifiers in the model-facing contract. A deployment maps names to environment,
Key Vault, cloud secret manager, or test sources outside the core.

Reference names:

- are validated and length-limited
- are safe to include in policy requests
- may appear in audit events
- never imply that the value exists or is authorized

## Lease rules

- Leases are non-serializable.
- Leases are not reusable across operations.
- Expired leases fail access immediately.
- The broker may return a shorter duration than requested.
- Closing is idempotent.
- The value wrapper avoids useful `repr` and string interpolation.
- The broker does not cache indefinitely unless an implementation has an explicit secure
  cache policy.

## Environment and files

The default in-memory executor does not place leases in the global process environment.
If a command needs an environment value, it receives an operation-local mapping.

Writing a resolved value into workspace files is denied by default. A future explicit
secret-materialization feature would need separate policy, redaction, snapshot exclusion,
and cleanup semantics.

## Redaction

The operation redactor receives the exact resolved byte and text representations needed
to remove values from:

- stdout and stderr
- command history
- errors
- event payloads
- trace attributes

Redaction is defense in depth. It does not replace access policy or least-privilege
leases.

## Failure semantics

Stable errors include:

- `SecretReferenceInvalid`
- `SecretDenied`
- `SecretNotFound`
- `SecretExpired`
- `SecretLeaseLimitExceeded`
- `SecretSourceUnavailable`
- `SecretLeaseClosed`

Errors may include the safe reference name but never the value.

## Test expectations

- Approved resolution returns a scoped lease.
- Denied requests never call the secret source.
- Missing and source-unavailable errors remain distinct.
- Expiry boundaries use an injected clock.
- Leases close after success, failure, timeout, and cancellation.
- Values never occur in results, events, errors, snapshots, or object representations.
- Destination-bound leases cannot be reused for another destination.
- Concurrent operations receive independent leases.

## Maintenance rule

Any new way for secret material to enter execution, files, environment, output, or
telemetry requires updates to this document, policy design, and event redaction tests.
