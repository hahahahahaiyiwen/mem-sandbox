# Azure OpenAI workspace-showcase evaluation: 2026-09-13

This directory retains the two one-shot live evaluations authorized in
[issue #76](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/76#issuecomment-5655176089).
No run was retried. The records are non-deterministic, non-gating,
provider- and machine-specific, and not directly comparable with uncontrolled
runs.

## Run identity

- Source commit: `9148d44ee281d3a6d5ccc3858ce117298ac128ff`
- Provider: `azure_openai`
- Deployment: `gpt-5.4-mini-global`
- Azure OpenAI API version: `2024-12-01-preview`
- OpenAI Agents SDK: `0.22.0`
- OpenAI Python SDK: `3.8.0`
- Python: `3.12.12`
- Host: Windows 11, AMD64
- Provider-reported monetary cost: unavailable for both runs

The `multi-agent-handoff` record reports `source_dirty=true` only because the
first run's untracked evidence directory already existed beneath the repository.
Both runs executed the same committed source without intervening code changes.

## Commands

The credential environment was loaded without printing values, then these
commands were run once each:

```console
uv run python -m evaluations.workspace_showcase \
  --provider azure_openai \
  --scenario document-review \
  --allow-billable-provider-run \
  --approval-reference https://github.com/hahahahahaiyiwen/mem-sandbox/issues/76#issuecomment-5655176089 \
  --output evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/document-review
```

```console
uv run python -m evaluations.workspace_showcase \
  --provider azure_openai \
  --scenario multi-agent-handoff \
  --allow-billable-provider-run \
  --approval-reference https://github.com/hahahahahaiyiwen/mem-sandbox/issues/76#issuecomment-5655176089 \
  --output evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/multi-agent-handoff
```

## Observations

| Scenario | Outcome | Exact verification | Tool behavior | Tokens |
|---|---|---|---|---:|
| `document-review` | Failed at host verification | The maintained runner rejected the final draft; artifact hashes remain `verification_incomplete` because scenario verification stopped before returning a result | 3 invalid patches, 1 policy-denied command, 0 unsupported classifications, 0 linked repairs | 55,256 |
| `multi-agent-handoff` | Succeeded | 3/3 artifacts matched: 2 output-correctness checks and 1 evidence-accuracy check | 2 invalid patches, 1 immediately linked successful repair, 0 unsupported classifications | 51,032 |

The document stages completed, but the final exact-byte check rejected
`/workspace/drafts/release-notes.md`. The record attributes the terminal boundary
to host verification and does not infer a more specific cause from model output.
The incomplete artifact entries do not assert that unchanged source artifacts
were modified; they mean the failed scenario did not return artifacts for
evaluation-owned hashing.

## Separated timings

All values below are sums from the raw nanosecond samples, converted to
milliseconds only for readability. End-to-end overlaps the other categories.

| Scenario | Seed | Read | Mutation | Execute | Snapshot persist/restore | Host verification | Cleanup | Provider model | End-to-end |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `document-review` | 9.683 ms | 12.524 ms | 7.157 ms | 0.842 ms | 0 ms | 2.215 ms | 7.767 ms | 33,720.171 ms | 34,239.157 ms |
| `multi-agent-handoff` | 1.926 ms | 5.676 ms | 4.954 ms | 0 ms | 0 ms | 1.564 ms | 4.006 ms | 17,750.388 ms | 18,010.503 ms |

Neither fixture exercises snapshot persistence or restoration, so those
categories are explicitly zero rather than folded into another phase. Both
workloads fit existing workspace limits, workspace operations were not the
observed end-to-end constraint, and no memory or snapshot budget trigger was
demonstrated. This evidence therefore does not authorize internal optimization
or reopen content offload.

See each scenario's `record.json` for structured raw observations and
`REPORT.md` for its generated summary.
