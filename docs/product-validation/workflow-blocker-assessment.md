# Workflow Blocker Assessment and Next-Capability Decision

**Assessment date:** 2026-09-13

**Milestone:** 7

**Decision:** Defer new capability and SDK work until a reproducible
product-surface blocker meets a review trigger below.

> **Subsequent selection:** On 2026-09-20, the
> [current-source verification workflow](./current-source-verification-evidence.md)
> recorded a deterministic unsupported interaction, satisfied review trigger 3, and
> received explicit human approval to select controlled HTTP for bounded Milestone 9
> decomposition. This document remains the historical Milestone 7 assessment; the
> follow-up does not claim that networking is implemented.

## Purpose

This assessment classifies the blockers observed while completing the
workspace-first Milestone 7 workflows. It separates limitations of the current
MemSandbox product surface from host-environment failures, SDK/provider
integration friction, and non-deterministic model behavior.

The decision is intentionally narrower than the future roadmap. Milestones 8-12
remain design directions, not authorization to add host retrieval, sandbox-owned
networking, arbitrary execution, durable infrastructure, or another SDK adapter.

## Evidence standard and scope

Evidence is classified as:

- **Measured:** retained structured results or deterministic test outcomes.
- **Observed:** a concrete issue, pull request, or maintainer workflow reports
  the condition, but it is not a controlled product measurement.
- **Speculative:** research or roadmap material describes a possible need
  without a blocked user workflow.

The assessment uses:

- deterministic scenario and integration work from
  [#71](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/71),
  [#72](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/72),
  [#73](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/73),
  [#74](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/74), and
  [#75](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/75);
- the approved live-evaluation work in
  [#76](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/76) and its
  [retained Azure OpenAI results](../../evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/README.md);
- installed-package verification recorded by
  [PR #80](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/80),
  [PR #81](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/81),
  [PR #83](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/83), and
  [PR #84](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/84);
- the existing
  [integration research](../AGENT_SANDBOX_INTEGRATION_RESEARCH.md), which is
  design input rather than user-demand evidence.

No user or collaborator report supplied to #77 identifies a blocked workflow
that requires a second SDK, sandbox-owned HTTP, or external Python execution.
That missing evidence is part of the decision rather than an implicit negative
finding about those future capabilities.

## Blocker register

| Category | Evidence and frequency | Severity and affected workflow | Current workaround | Required authority | Classification and conclusion |
|---|---|---|---|---|---|
| Host retrieval or artifact exchange | Package downloads from `files.pythonhosted.org` failed during local clean-install validation in four milestone changes ([#72/PR #80](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/80), [#73/PR #81](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/81), [#75/PR #83](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/83), and [#76/PR #84](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/84)). | Medium validation friction on the affected workstation; it did not block repository CI or a MemSandbox workspace task. | Run clean-install rows in CI or provide dependencies through an operator-controlled package source or wheelhouse. | Host/operator network and package-source authority outside the sandbox. | **Observed.** This is host dependency retrieval, not evidence for model-visible networking or a missing workspace import/export operation. |
| Parsing or calculation | The deterministic conformance work completed all maintained scenarios, while the live document run recorded invalid patch attempts rather than a missing parser or calculator ([#75](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/75), [document report](../../evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/document-review/REPORT.md)). | No demonstrated product blocker. One live run failed, but the retained record cannot infer a new operation from redacted model input. | Keep exact host verification and deterministic model-double coverage; improve model instructions or tool use independently. | No additional sandbox authority is justified. | **Measured absence for maintained deterministic scenarios; observed model/tool-use failure live.** Do not select external execution for this evidence. |
| Test or runtime execution | Repository tests and package validation run in the host/CI environment. No maintained workspace scenario required arbitrary repository tests or external code execution ([#71](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/71), [#75](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/75)). | No demonstrated product blocker. | Continue using host-owned CI for repository execution and the constrained virtual command profile for workspace tasks. | A future executor would require an explicit execution grant and an external isolation profile. | **Measured absence in current scenarios.** Do not infer that bounded Python would support arbitrary test suites. |
| SDK or provider integration friction | Installed OpenAI Agents SDK `0.22.0` and `0.22.2` probes passed in CI ([#73](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/73)). Provider monetary cost was unavailable in both approved live runs ([result set](../../evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/README.md)). | No SDK compatibility blocker; low operational evidence limitation for cost reporting. | Preserve `unavailable` rather than inventing cost and retain the exercised SDK matrix. | Provider-owned usage/pricing data or an injected host cost source, not core workspace authority. | **Measured.** The OpenAI adapter is usable; cost visibility remains provider-specific integration friction. |
| Model behavior | `document-review` completed both stages but failed exact host verification after three invalid patches and one policy-denied command; `multi-agent-handoff` succeeded after one linked patch repair ([document report](../../evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/document-review/REPORT.md), [handoff report](../../evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/multi-agent-handoff/REPORT.md)). | High for the failed one-shot document run, but non-deterministic and non-gating; the other guarded workflow passed 3/3 artifact checks. | Retain failures, use deterministic conformance as the correctness gate, and require host verification. A new billable retry requires new approval. | Model/provider selection and billable-run approval remain host responsibilities. | **Measured live observation.** It does not justify widening sandbox capabilities. |
| Second SDK demand | The integration research identifies viable seams, but #77 received no user or collaborator workflow, target SDK/version, maintenance owner, or compatibility requirement. | Evidence missing; no current workflow is blocked. | Continue with the framework-neutral core and first OpenAI adapter. | A focused adapter issue requires external demand plus normal dependency and maintenance review. | **Speculative.** Do not create a second-SDK issue from research alone. |

## Decision

Select exactly one disposition:

> **Defer new capability work.**

The observed workflows do not establish a missing MemSandbox operation:

- deterministic workspace and SDK scenarios pass;
- the successful live workflow used the current four-tool surface;
- the failed live workflow is attributable only to model/tool use and final host
  verification at the retained evidence level;
- package retrieval failures occurred in the host development environment;
- provider cost availability does not block workspace execution; and
- no external evidence supports a second SDK.

Accordingly, this assessment does not advance Milestone 8, 9, 10, 11, or 12
implementation and does not create a new integration issue.

## Review triggers

Reopen next-capability selection only when at least one trigger has a linked,
reproducible workflow:

1. A maintained scenario or user workflow cannot obtain or return bounded
   artifacts through current host seeding and verified output collection.
2. A required calculation, parser, or test/runtime operation cannot be
   represented by the constrained command surface and the host cannot
   reasonably own it.
3. A live or deterministic record classifies a required interaction as
   unsupported by MemSandbox rather than as model behavior, policy denial, or
   provider failure.
4. A user or collaborator names a second SDK, supported version range,
   integration seam, blocked workflow, and expected maintenance owner.
5. Deployment evidence requires process-loss recovery, shared live sessions, or
   remote workers rather than current same-process snapshots and replacement
   sessions.

Repeated package-CDN failure alone does not satisfy these triggers unless a
product workflow, rather than contributor environment setup, is blocked.

## Requirements if a trigger is met

| Direction | Authority and policy | Limits and accounting | Provider and validation | Documentation |
|---|---|---|---|---|
| Host-controlled artifact exchange | Host-only import/export grant; no model-selected host path or remote URL; archive and path validation | File, byte, archive, and cumulative transfer limits | Injected host transfer boundary; deterministic round-trip, rejection, cleanup, and provenance tests | Current profile, artifact ownership, and unsupported remote retrieval |
| Controlled HTTP | Explicit connected profile; destination, DNS, redirect, proxy, TLS, credential, and retry policy | Request, response, time, concurrency, and cumulative network budgets | Injected HTTP/resolver/credential providers; SSRF, redirect, secret, audit, and failure tests | Network authority, supported methods, destination limits, and unknown-outcome behavior |
| External Python | Explicit execution grant and named security profile; network disabled initially | CPU/time, memory, output, workspace transfer, process, and cleanup accounting | Host-selected external backend; isolation, cancellation, atomic publication, provenance, and conformance tests | Trust tier, runtime/package limits, no-shell boundary, and provider responsibilities |
| Durable snapshots | Host-selected persistence provider and explicit snapshot authority; no implicit live-session or remote-worker authority | Snapshot size, retention, reachability, retry, garbage-collection, and cumulative storage accounting | Versioned provider identity and compatibility; encryption plus cross-process recovery, expiry, integrity, and missing/corrupt-data tests | Durable content and session metadata boundaries, recovery guarantees, permanent-loss behavior, and host-owned conversation/runner state |
| Shared live sessions or remote workers | Authenticated principal and tenant authorization; exclusive leases, fencing, heartbeat/expiry, and versioned remote protocol only after external-execution conformance | Cross-process and per-tenant cumulative budgets, worker capacity, cancellation, orphan cleanup, and audit accounting | Authenticated and encrypted control/data channels; ownership, crash-recovery, stale-worker, cancellation, cleanup, and local-conformance tests | Ownership topology, trust boundaries, protocol compatibility, recovery/failure behavior, provenance, and audit responsibilities |
| Second SDK | Separate optional adapter with no framework types in core and no implicit host capabilities | Existing workspace quotas plus adapter lifecycle ownership | Named SDK/version range, consumer-owned model/provider, installed-artifact compatibility matrix, and scenario tests | Installation, public imports, permissions, unsupported defaults, and maintenance policy |

These are prerequisites for a future focused issue, not implementation
authorization from this assessment.
