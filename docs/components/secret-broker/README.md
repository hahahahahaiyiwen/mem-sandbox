# Secret Broker Design

**Status:** Milestone 3 no-secret broker implemented; functional secret leasing deferred

## Purpose

The current secret boundary rejects every lease request explicitly. A future secret
broker may resolve opaque references into short-lived, operation-scoped leases only
after a concrete authorization boundary, identity model, and redaction flow are approved.

## Deferred responsibilities

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
    session_id: SessionId
    operation_id: OperationId
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

Milestone 3 provides `NoSecretBroker`, which implements this boundary by rejecting every
lease request with the stable `SecretDenied` error. The public Milestone 3 session
operations carry no secret references and therefore never invoke the injected broker.

`SecretValue` is a protected wrapper rather than a plain string. Conversion to bytes or
text happens only at the command boundary that needs it. `SessionId` and `OperationId`
remain typed throughout policy and broker calls.

## Deferred resolution flow

The following flow is a future requirement, not current release behavior:

1. The session parses secret references without resolving them.
2. The policy engine evaluates reference, command, destination, and requested duration.
3. The session requests a lease using the effective policy limits.
4. The broker obtains the value from its configured source.
5. The value is registered with the operation's redactor.
6. The command receives only the required lease.
7. The lease closes in `finally`, including timeout and cancellation paths.

Secret lookup must never occur before explicit authorization. Functional resolution is
deferred with the composed-policy decision because the current sandbox has no caller
identity, destination, or command-bound lease requirement.

Re-evaluate this component only when an adapter or `SandboxService` use case identifies
the caller identity, protected secret reference, permitted command, optional destination,
and required lease lifetime. The current `NoSecretBroker` remains the authoritative
default until that design is approved.

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
