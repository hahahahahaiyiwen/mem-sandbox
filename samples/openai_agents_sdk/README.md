# OpenAI Agents SDK Samples

These repository samples show agents inspecting and revising files in a host-controlled
MemSandbox workspace. Provider-specific entry points share the same scenarios, lifecycle,
and exact host verification.

## Choose a path

| Goal | Documentation |
|---|---|
| Run a maintained live scenario | Continue with this sample guide, then select the [official OpenAI](./providers/openai/README.md) or [Azure OpenAI](./providers/azure_openai/README.md) provider. |
| Embed MemSandbox in an application | Follow the [reader-first `SandboxAgent` integration guide](../../packages/openai-agents/README.md#use-with-sandboxagent). |
| Understand or extend the adapter | Read the integration [engineering reference](../../packages/openai-agents/README.md#engineering-reference). |
| Inspect framework-neutral service composition | Read the [shared sample utilities](../shared/README.md) and their [composition root](../shared/service.py). |

The helpers under `samples` are repository examples, not installed
`mem_sandbox` public APIs. Production applications should copy the composition pattern
or inject their own public collaborators rather than importing `samples`.

## Run the document-review showcase

Configure either the [official OpenAI provider](./providers/openai/README.md) or
[Azure OpenAI provider](./providers/azure_openai/README.md), then run:

```console
uv run python -m samples.openai_agents_sdk.providers.openai --scenario document-review --inspect
```

or:

```console
uv run python -m samples.openai_agents_sdk.providers.azure_openai --scenario document-review --inspect
```

The scenario keeps two agent roles in one host-selected session:

1. The host seeds a source brief, stale Markdown draft, and review instructions.
2. An editor discovers discrepancies, reads the draft hash, and applies a guarded patch
   limited to permitted text.
3. A separate reviewer compares the corrected draft with the source files and writes a
   structured evidence report.
4. Host code verifies the source and instructions were preserved, the draft has the
   exact required content, and the review artifact links its findings to workspace paths.

Inspect the maintained outputs before the session is deleted:

```text
mem-sandbox:/workspace> cat /workspace/drafts/release-notes.md
mem-sandbox:/workspace> cat /workspace/review/findings.md
```

The workspace task itself needs no network or arbitrary execution. A live model run
still sends billable requests to the configured inference provider.

## Choose a scenario

`workspace-edit` remains the command-line default and smallest integration smoke test.
Use `document-review` for the representative end-to-end workflow.

| Task | Scenario | Verified artifact or state |
|---|---|---|
| Review and correct a document | `document-review` | Corrected draft plus path-linked findings |
| Check basic file-tool wiring | `workspace-edit` | One guarded status-file edit |
| Search supplied operational evidence | `incident-triage` | Incident report derived from seeded logs |
| Migrate related configuration | `config-migration` | Two updated configs plus migration report |
| Transform deterministic input data | `data-pipeline` | Sorted event-count artifact |
| Recover without bypassing policy | `policy-recovery` | Evidence-preservation report |
| Recover from a workspace quota | `quota-recovery` | Compact status artifact |
| Coordinate sequential roles | `multi-agent-handoff` | Shared plan, change, and review |
| Compare isolated alternatives | `snapshot-branching` | Selected fork restored from a shared snapshot |

## Purpose and boundary

This package owns sample behavior specific to the OpenAI Agents SDK and the MemSandbox
OpenAI integration. It lets multiple inference-provider entry points run the same SDK
runner and scenarios without duplicating sandbox behavior.

The package owns:

- scenario manifests, prompts, stages, policies, limits, and expected artifacts;
- `SandboxAgent` construction and `InMemorySandboxCapability` binding;
- SDK session and provider-client lifecycle;
- snapshot branching and host-side verification;
- shared provider command-line arguments;
- provider-client cleanup coordination.

It does not read provider credentials or construct an inference client. Those concerns
belong to nested provider packages:

- [`providers.azure_openai`](./providers/azure_openai/README.md)
- [`providers.openai`](./providers/openai/README.md)

The inspection CLI and MemSandbox service composition are
[SDK-independent shared utilities](../shared/README.md).

## Provider contract

`run_provider_sample` receives a factory that returns:

1. an asynchronous client with a `close()` method; and
2. an OpenAI Agents SDK `Model`.

The SDK runner therefore does not depend on Azure OpenAI, the official OpenAI API,
or an OpenAI-compatible base URL. A new provider package must prove that its model
adapter supports the tool-calling behavior required by the registered scenarios.

All provider entry points expose the same options:

```text
--scenario <name>
--list-scenarios
--inspect
--inspect-on-failure
```

Listing scenarios returns before provider configuration is loaded, so it requires no
credentials or network access.

## Design invariants

- The model receives exactly `execute`, `read_file`, `write_file`, and `apply_patch`.
- Provider code cannot select sandbox handles or alter scenario lifecycle behavior.
- Host verification, rather than the model's final prose, determines success.
- The `document-review` editor and reviewer share workspace state, not model
  conversation state or unverified approval prose.
- Every SDK session and backend handle is cleaned up on success, failure, or
  cancellation.
- SDK-independent inspection uses the same live MemSandbox session and never invokes a
  host shell.
- Required tests use deterministic `Model` doubles and do not call a live provider.

See the [scenario design](./scenarios/README.md) for scenario-specific ownership.

## Maintenance

Keep inference credentials, endpoint validation, and concrete model-client construction
in the nested provider packages. Keep SDK-independent inspection and service composition
in `samples.shared`. Changes to runner lifecycle or scenarios must update deterministic
tests under `tests/samples`.
