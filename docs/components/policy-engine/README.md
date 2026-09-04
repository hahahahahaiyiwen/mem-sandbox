# Policy Admission Design

**Status:** Minimal admission seam implemented; issue #31 path-mutation facts designed

## Decision

MemSandbox will not implement the previously proposed composed policy engine in the
current release path.

The in-memory sandbox has no authenticated caller or in-core owner authorization
context, network destination, host-process execution, or shared persistent backend.
Logical `OwnerId` values are provenance only. Issue #20 adds one concrete protected
action: an execute operation may request typed secret references for an ephemeral
environment overlay. This reopens only the facts required to approve or deny those
references; it does not justify a general operation/path/command rule engine.

The essential sandbox guarantees remain enforced by the modules that own them:

- the workspace owns path confinement, quotas, atomic publication, and snapshot
  validation;
- the command executor owns its registry, accepted grammar, pipeline admission, command
  and output limits, and prohibition of host-shell fallback;
- the session owns lifecycle, serialization, deadlines, cancellation, and collaborator
  ordering;
- the default secret boundary rejects every request explicitly, while an opt-in
  functional broker resolves only references approved through this seam.

These guarantees do not depend on a configurable policy engine and must not be weakened
when policy is reconsidered.

## Retained minimal seam

The existing `SessionPolicyEngine` port, `PolicyRequest`, `PolicyDecision`, and
`AllowAllPolicyEngine` remain in place. The session continues to require an explicit
decision before invoking its protected operation collaborator:

```python
@dataclass(frozen=True)
class PolicyRequest:
    session_id: SessionId
    operation_id: OperationId
    operation_kind: OperationKind
    path: SandboxPath | None
    command_name: str | None
    requested_limits: OperationLimits
    secret_refs: tuple[SecretRef, ...] = ()
    path_mutation: PathMutationPolicyContext | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PathMutationPolicyContext:
    create_parents: bool = False
    exist_ok: bool = False
    recursive: bool = False
    missing_ok: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str
    effective_limits: OperationLimits


class SessionPolicyEngine(Protocol):
    async def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...
```

The explicit seam preserves dependency injection and fail-closed session ordering
without committing the project to a speculative policy language. `path_mutation`
contains the typed options for native directory creation and removal, allowing policy to
distinguish recursive or idempotent operations without parsing commands or accepting raw
dictionaries. It is `None` for all other operation kinds. `secret_refs` contains
the unique references declared by an execute request, sorted by `SecretRef.name`, and
never contains resolved values or environment overlay material. The default empty tuple
preserves existing positional construction and non-execute behavior.

The current `AllowAllPolicyEngine` returns `allowed=True`, reason code `allow_all`, and
unchanged effective limits. Applications that inject a functional secret broker must
decide whether allow-all is appropriate for every reference exposed by their source.
`NoSecretBroker` remains the fail-closed default.

This narrow contract creates one permitted data-contract dependency from
`mem_sandbox.policy` to `mem_sandbox.secrets.SecretRef`. The policy module does not
depend on broker, source, lease, value, or command-executor implementations.

Lifecycle `start()` and `close()` are not policy operations. A custom implementation may
still deny an operation or narrow its timeout through the existing contract, but the
project does not yet advertise a general authorization framework.

## Current invariants

- The session must receive an explicit decision. `None`, exceptions interpreted as
  allow, and silent defaults are prohibited.
- A denial occurs before the workspace, command executor, snapshot store, or secret
  broker operation.
- Secret references are policy facts; secret values are never policy facts.
- Native path-mutation options are policy facts; workspace state and file content are not.
- Denied secret-bearing execute requests call neither broker nor source.
- Multiple bindings of one reference are normalized into one sorted policy fact.
- Policy may narrow only `timeout_seconds`.
- `terminal_event_reserve_seconds` remains session-owned and must be preserved.
- A narrowed timeout is measured from the original operation start, not from completion
  of policy evaluation.
- The minimal seam is not an operating-system isolation boundary and does not make
  same-process memory safe from arbitrary host code.

## Why composed policy is deferred

A correct composed engine would currently require abstractions that have no proven
consumer:

- prepared command summaries that represent pipelines, filesystem effects, and arguments
  whose values may change after earlier `cd` or `export` commands;
- workspace-owned prepared patch artifacts so policy can observe every affected path
  without duplicating patch parsing;
- composite operation, command, workspace, file, pipeline, and secret-lease limit
  propagation;
- typed obligation enforcement and new policy-specific failure semantics;
- deterministic precedence across operation, path, command, argument, secret, and
  destination rules.

Building those boundaries before a real authorization requirement exists would constrain
the workspace, executor, session, and adapter designs based on hypothetical use cases.
The first framework adapter and `SandboxService` should provide evidence about where
authorization belongs and what identity and resource facts are actually available.

## Re-evaluation triggers

Open a new design issue before enabling any of the following beyond issue #20:

- multi-owner or multi-tenant `SandboxService` authorization;
- secret authorization based on parsed arguments, command stages, network destinations,
  owners, tenants, or external approval obligations;
- network destinations or egress-capable commands;
- arbitrary host process, Python, container, or remote execution;
- shared persistent workspaces or snapshots across authorization boundaries;
- per-owner or per-tenant command capability profiles;
- externally required audit, compliance, or approval obligations.

The re-evaluation must begin with concrete authorization questions, such as whether one
owner may resume another owner's snapshot or whether a command may lease a named secret
for a specific destination. It must not begin by assuming that a generic rule-composition
framework is required.

## Requirements for a future policy design

If a trigger occurs, the design must:

1. Identify the authority and authenticated identity at the boundary where the decision
   is made.
2. Keep sandbox invariants in their owning modules rather than moving them into policy.
3. Use prepared artifacts owned by the command executor or workspace instead of
   duplicating parsers in the session or policy module.
4. Define deterministic fail-closed behavior for invalid configuration, evaluation
   failure, unsupported obligations, and missing facts.
5. Prove that denials invoke no protected collaborator.
6. Add happy, denied, dependency-failure, and exact-boundary tests for every enabled
   policy-aware behavior.
7. Keep secret values and unredacted protected data out of requests, decisions, events,
   and model-visible errors.

## Maintenance rule

Do not expand the current policy contracts merely because a future feature might need
authorization. Issue #20 is the narrow exception because it identifies the protected
action and enforcement point: an execute operation's declared reference set is evaluated
before broker/source access. Command, argument, destination, owner, or tenant facts still
require a separate approved design.
