# Workspace Showcase Live Evaluation

## Purpose and boundary

This module records opt-in, provider-backed observations for representative
OpenAI Agents SDK workspace scenarios. It is evaluation infrastructure, not a
runtime package, correctness gate, provisioning benchmark, or source of product
performance budgets.

The module owns:

- schema-versioned JSON records and generated Markdown summaries;
- provider/model, package, SDK, scenario-fixture, and environment metadata;
- provider-model usage and latency observation;
- workspace phase and tool-interaction timing;
- exact host-verification evidence represented by paths and hashes, never file
  contents;
- explicit live-run approval checks and result persistence on success, failure,
  or cancellation.

The maintained scenario runner continues to own sandbox allocation, stages,
snapshot lifecycle, exact byte verification, and cleanup. Its optional
instrumentation ports expose elapsed phase observations and stage-specific model
and capability factories without importing this evaluation module. Provider
packages continue to own credentials and client construction.

The older `evaluations/command_usability` corpus remains a separate historical
study of the direct tool surface. Its schema and results are not migrated into
this workspace-showcase record.

## Approval and execution

No command in this module may construct a provider client unless both conditions
are supplied:

1. `--allow-billable-provider-run`; and
2. `--approval-reference` identifying the maintainer approval recorded on the
   issue or pull request.

The approval reference is evidence, not an authorization service. The operator
remains responsible for account, model, region, data-governance, and cost
approval. Listing or validating records needs no credentials or network.

Representative commands, after approval, are:

```console
uv run python -m evaluations.workspace_showcase \
  --provider openai \
  --scenario document-review \
  --allow-billable-provider-run \
  --approval-reference <issue-comment-url> \
  --output evaluations/workspace_showcase/results/<run>/document-review
```

```console
uv run python -m evaluations.workspace_showcase \
  --provider openai \
  --scenario multi-agent-handoff \
  --allow-billable-provider-run \
  --approval-reference <issue-comment-url> \
  --output evaluations/workspace_showcase/results/<run>/multi-agent-handoff
```

`document-review` is the required document workflow.
`multi-agent-handoff` is the required additional guarded multi-stage workflow.
The provider is selected explicitly; the module also supports the maintained
Azure OpenAI provider composition.

## Record contract

Schema version 1 contains only allowlisted, bounded fields:

| Area | Recorded evidence |
|---|---|
| Run identity | schema version, UTC start time, outcome, approval reference |
| Source | commit, dirty flag, package and SDK versions |
| Environment | Python implementation/version, OS, architecture |
| Provider | provider kind, model/deployment, non-secret API version when applicable |
| Scenario | registry name, evaluation-fixture version, ordered stage outcomes |
| Verification | artifact path, expected/actual SHA-256, check kinds, match or stable error code |
| Interaction | ordered tool name, outcome, stable error code/category, unsupported flag, repair link |
| Timing | category, operation label, optional stage key, outcome, integer nanoseconds |
| Usage | model-call count, optional provider-supplied tokens and cost, completeness status |
| Interpretation | fixed limitations and failure attribution |

Raw prompts, model prose, tool arguments, tool output bodies, file contents,
provider endpoints, credentials, environment variables, exception messages, and
tracebacks are excluded. Failure records retain only an allowlisted attribution,
exception type, optional stage, and stable MemSandbox error code. Serialization
validates the schema and bounded text fields before writing.

Token counts are `null` with status `unavailable` when the provider response does
not supply them. The SDK represents omitted token fields with zero, so zero is
conservatively treated as unavailable rather than as a measured count. Each token
field is tracked independently; mixed field or call coverage is `partial`. Cost
follows the same rule and is recorded only through an injected provider cost
source. The maintained OpenAI and Azure OpenAI integrations do not currently
receive provider-reported monetary cost, so their cost status is `unavailable`.

## Timing semantics

All elapsed durations use `time.perf_counter_ns()` and remain raw integer
nanoseconds. The performance clock is injected for deterministic tests.

| Category | Boundary |
|---|---|
| `workspace_seed` | Before SDK client create through manifest materialization and a ready session |
| `workspace_read` | One model-facing MemSandbox read tool invocation |
| `workspace_mutation` | One model-facing write/patch or host checkpoint mutation |
| `workspace_execute` | One constrained command invocation |
| `snapshot_persist` | Before checkpoint stop/persist through durable in-process state availability |
| `snapshot_restore` | Before resume through an available live attachment or started replacement/fork |
| `host_verification` | Exact expected-byte reads and comparisons performed by the host |
| `cleanup` | SDK session/backend, service, or provider-client cleanup operation |
| `provider_model` | One provider model request only; tool execution is excluded |
| `end_to_end` | Provider construction through record-ready cleanup |

`end_to_end` overlaps its component categories and must not be summed with them.
Snapshot-store lookups and SDK bookkeeping may occur inside persist/restore
boundaries. These evaluation measurements are intentionally separate from the
model-free benchmark schema and Milestone 6 baseline.

## Verification and interpretation

Evaluation fixtures assign each expected artifact one or more checks:

- `output_correctness`;
- `unrelated_content_preservation`;
- `evidence_accuracy`.

Exact scenario verification still compares bytes. The persisted record stores
only hashes and match status. An immediately following successful retry of the
same tool in the same stage is linked as a repair; broader semantic repair is not
inferred from redacted tool payloads. Unsupported attempts are explicit even when
the count is zero. Run failures are attributed to model behavior, provider
integration, SDK behavior, MemSandbox behavior, host verification, cleanup, or
cancellation; the evaluator does not infer a more specific cause from provider
prose.

Every report must state that it is non-deterministic, non-gating, provider- and
machine-specific, and not directly comparable across uncontrolled runs.

## Maintenance

Changing the schema creates a new schema version. Changing scenario inputs,
expected artifacts, stage structure, or verification classifications increments
that scenario's evaluation-fixture version. New external dependencies are
constructor-injected behind interfaces owned by this module. Required tests use
fake models, clients, clocks, and cost sources and must not construct a live
provider client.
