# Authority, Policy, Accounting, and Event Design for Artifact Exchange

**Design date:** 2026-09-15  
**Source baseline:** `c41dc34fc6711f84a8bbee1cf9814294cdbfa173`  
**Scope:** [#93](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/93), a
documentation-only design child for Milestone 8 planning.

## Design stance

Artifact exchange should remain consumer-driven. The current `virtual` profile exposes
bounded workspace and session behavior, not host filesystem, network, Git, tenant, or
remote execution authority. The [bounded artifact-exchange design](./artifact-exchange-design.md)
therefore describes future content-tree input, selected export, and input-bound change
reporting as conditional host-facing conveniences rather than model-controlled powers.

This document maps those candidate operations to authority, policy, resource accounting,
events, and behavior-seam evidence. It does not implement a policy engine, ledger,
profile registry, extension package, service/tenant mode, public API, or new capability.
All proposed mechanics require a linked
[workflow-blocker trigger](./workflow-blocker-assessment.md#review-triggers) and a
separate approved implementation issue.

## Authority invariants

- Default authority is denial for optional external capabilities.
- The host selects an immutable capability profile when creating or resuming a session.
- Resume may use an equal or narrower profile; serialized snapshots never widen grants.
- Model input, archive content, restored state, and `OwnerId` never grant authority.
- `OwnerId` remains provenance for logical ownership, not authentication or
  authorization.
- Component hard limits remain authoritative even when a policy or accounting layer
  allows an action.
- Host paths, credentials, signed URLs, private repository remotes, and sensitive
  queries stay outside core request and event payloads.

## Action-to-authority matrix

| Action | Current or conditional | Authority source | Policy/admission point | Resource owner | Event facts |
|---|---|---|---|---|---|
| Seed host-owned bytes with `CreateSandboxRequest.initial_files` | Current | Host application before sandbox creation | Constructor/service validation plus workspace limits | Workspace owns paths, bytes, quotas and hashes | Existing lifecycle events only; no content payload |
| Host `write_bytes` / `read_bytes` on known paths | Current | Host-held session reference | Existing `SessionPolicyEngine` operation decision, then workspace operation | Session owns deadline/events; workspace owns mutation/read | Existing operation status, path/hash/count metadata |
| Full portable archive export | Current | Host-held session reference | Session operation gate and workspace archive bounds | Workspace archive port | Existing operation status and bounded archive metadata |
| Conditional full archive restore | Current | Host-held session reference and optional expected revision/root hash | Session gate before workspace prepare/commit; stale identity rejects before publish | Workspace owns candidate validation and atomic publish | Existing operation status, revision/hash metadata |
| Content-tree publication | Conditional | Explicit host artifact-import grant selected at create/resume | Future session-owned import admission over workspace-prepared facts | Workspace owns candidate; session owns protected operation lifecycle | Resource kind, entry counts, bytes, result revision/hash, stable reason |
| Selected artifact export | Conditional | Explicit host artifact-export grant selected at create/resume | Future session-owned export admission before materialization | Workspace owns deterministic selection and hashes | Resource kind, path count, bytes, baseline/current identity |
| Input-bound change report | Conditional | Explicit host reporting grant and supplied baseline identity | Future session-owned report admission after baseline validation | Workspace owns comparison facts | Change counts, baseline/current revision/hash, stable reason |
| Repository retrieval or `.git` filtering | Conditional, separate issue | Host repository authority outside core | Future repository-import boundary, not generic archive parsing | Host/repository importer plus workspace candidate | Sanitized source class and publication outcome only |
| Cumulative cross-operation accounting | Conditional, separate issue | Host-selected accounting profile | Reservation before protected work, settlement on terminal path | Accounting boundary plus resource owner | Bounded usage and settlement status |

Current operations use the existing minimal policy seam and component limits. Conditional
operations should add only the narrow prepared facts their consumer needs; they should not
turn `PolicyRequest` into a speculative union of every future resource type.
The [conditional Git retrieval decision](./git-retrieval-decision.md) records the
repository-specific deferral and future authority requirements.

## Policy and prepared facts

`SessionPolicyEngine` remains the current fail-closed admission seam for session
operations. It should continue to receive immutable session, operation, path, operation
kind, requested limit and narrow context facts. It should not parse archives, command
strings, manifests, URLs, repository metadata, or filesystem trees.

Future artifact operations should follow this layering:

1. Construct typed request values at the session boundary.
2. Validate cheap structural facts and allocate the operation identity.
3. Ask session policy for operation admission and effective deadlines.
4. Ask the resource owner to prepare immutable facts without mutating live state.
5. Evaluate any resource-specific admission over those prepared facts.
6. Reserve required resources, if a concrete accounting consumer exists.
7. Publish or materialize through the owning workspace operation.
8. Settle usage and emit bounded terminal events.

A resource-specific policy port is justified only when the current policy request cannot
represent a real protected decision. For example, an artifact importer may need prepared
entry counts, total bytes, normalized paths, rejected metadata kinds, and target baseline
identity. Those facts should be owned by the workspace/import boundary, not recomputed in
policy from raw content.

## Accounting dimensions and deferrals

Accounting may deny or narrow work, but it never increases component ceilings.

| Dimension | Consumer | Measurement or bound | Settlement plan | Status |
|---|---|---|---|---|
| Workspace file bytes, node count, path length and archive bytes | Workspace | Existing `WorkspaceLimits` and codec checks | Enforced atomically by workspace operation | Current hard limit |
| Operation timeout and terminal reserve | Session | `OperationLimits` with effective timeout narrowing | Measured from operation start through terminal event | Current hard limit |
| Command output and command execution timeout | Command executor/session | Command limits and effective session budget | Terminal command result or timeout | Current hard limit |
| Materialized artifact input bytes | Future import facade | Sum of accepted content bytes before publication | Reserve before prepare/publish; release on pre-publication abort; settle on commit | Conditional #92/#93 |
| Materialized selected-output bytes | Future export facade | Sum of selected file bytes and encoded result bytes | Reserve before materialization; settle after result delivery decision | Conditional #92/#93 |
| Entry count for import/export/report | Future artifact facade | Count of normalized files/directories/changes | Reserve during prepare; settle terminal count | Conditional #92/#93 |
| Cumulative per-session transfer | Future accounting boundary | Sum of settled import/export/report materialization | Exact-once settlement keyed by operation ID; recovery by re-reading operation state | Deferred until consumer evidence |
| Per-owner or per-tenant budgets | Remote or multi-tenant service | Authenticated principal/tenant plus settled usage | Requires durable accounting store and replay/reconciliation semantics | Deferred outside current local profile |
| External network/execution resources | HTTP/Python milestones | Destination/runtime-specific time, bytes, concurrency, process resources | Resource owner reserves and settles around irreversible side effects | Separate milestone |

Unavailable measurements must be explicit. A future design should say "not measured" or
"bounded by component limit only" instead of recording zero or silently treating unknown
usage as free.

## Event design

Events remain observability records, not authority or source-of-truth state. Existing
events must stay content-free and bounded. Future resource events should be correlated
with one parent operation and use stable reason codes rather than raw provider
exceptions.

Allowed future artifact/resource event facts include:

- resource kind such as `artifact_import`, `artifact_export`, or `artifact_report`;
- profile identifier selected by the host;
- operation ID and parent operation ID;
- bounded entry counts and byte counts;
- baseline and current workspace revision/root hash;
- publication outcome and stable failure reason;
- settlement status when accounting exists.

Excluded by default:

- file content, patch content, archive payloads, stdout, stderr, and command text;
- host filesystem paths, credentials, signed URLs, tokens, headers, private remotes and
  sensitive query strings;
- secret values or raw provider exceptions;
- model-supplied provenance that implies permission.

Required audit/event availability must be checked before irreversible external side
effects. If a required start event fails, do not invoke the protected collaborator. If a
required terminal event fails after an authoritative workspace commit, surface the error
and require state re-read before retry.

## Failure and settlement semantics

| Case | Required behavior |
|---|---|
| Missing authority or optional grant disabled | Deny before prepared collaborator or external resource calls. |
| Invalid profile, policy configuration, prepared facts, or accounting configuration | Fail closed with a stable configuration/error category. |
| Policy evaluator failure | Fail closed; do not reinterpret exceptions as allow. |
| Accounting reservation denied | Do not publish or start irreversible external work. |
| Concurrent reservation conflict | Fail or wait according to a documented resource policy; never overdraw silently. |
| Pre-publication validation/cancellation/deadline failure | Leave workspace revision/root hash unchanged and release reservations. |
| Post-commit terminal event or cleanup failure | Report an error with unknown/committed outcome guidance; do not assume rollback or refund. |
| External work consumed before later failure | Settle measured consumed work; do not refund merely because publication failed. |
| Unknown terminal outcome | Re-read authoritative workspace/session/resource state before retry. |

Settlement should be exact-once per operation ID when durable accounting exists. Until
such a consumer exists, component limits and operation-local measurements are sufficient.

## Behavior-seam conformance matrix

Future implementation should test behavior at module seams with the real class under test
and small fakes/spies for collaborators:

| Case | Outcome assertions | Interaction assertions |
|---|---|---|
| Current virtual profile with no artifact grant | Optional artifact action is denied before workspace preparation. | Policy/resource collaborators after denial are not called. |
| Granted import happy path | Revision/root hash advance once; accepted paths, bytes and hashes match prepared facts. | Session admission, resource admission, reservation, workspace publish, settlement and events occur once in order. |
| Granted export happy path | Selected result is deterministic, bounded and host-destination-free. | Workspace materialization is called after admission/reservation only. |
| Input-bound report | Added/modified/deleted entries and empty directories are ordered with correct before/after hashes. | Baseline validation precedes report generation. |
| Bounds exact/one-over | Exact configured dimensions pass; one-over file/node/path/input/output/entry cases fail with unchanged state. | Denied paths release reservations and emit terminal failure facts. |
| Policy denial | Stable denied result and unchanged workspace identity. | No protected collaborator, broker, provider or publisher call occurs. |
| Invalid prepared facts | Stable invalid-request/configuration error. | Policy does not parse raw content; resource owner reports prepared-fact failure. |
| Cancellation/deadline | Pre-publish cancellation leaves state unchanged; post-publish completion is authoritative. | Terminal event and settlement reflect the documented outcome. |
| Required event failure | Start failure prevents mutation; terminal failure after commit is surfaced. | Start and terminal event paths are distinguishable. |
| Accounting race | Concurrent reservations cannot exceed budget. | One operation reserves/settles; the other denies, waits, or retries according to policy. |
| Provenance redaction | Events/results contain non-sensitive labels only. | Host paths, credentials, URLs and raw content never reach event sink or policy facts. |

These are planned tests, not passing evidence for this documentation-only issue.

## Acceptance disposition

No new capability is justified by this design alone. It records the missing consumer and
the constraints future issues must satisfy before implementing artifact facades,
cumulative accounting, repository retrieval, or remote profiles. Current workspace,
session, policy, event, and core hard limits remain authoritative.
