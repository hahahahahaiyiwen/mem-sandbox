# Command Usability Evaluation

This directory owns the executable, provider-dependent evaluation of the complete
MemSandbox command and file-tool surface. It is evidence for product design, not a
deterministic CI gate.

## Boundary

`run.py` gives each scenario an independent `SandboxSession` created through the public
`SandboxService` path. Models do not receive host tools. They emit one JSON action per
unfinished task, the runner invokes only `execute`, `read_file`, `write_file`, or
`apply_patch`, and the next model turn receives the real public result.

The versioned corpus in `corpus-v1.json` covers basic workspace mutation, recursive
discovery/search/counting, size ranking, JSON extraction, log aggregation, and exact
multiline creation. The log scenario expands a deterministic 148.5 KB fixture so the
evaluation exercises non-trivial in-memory pipeline materialization. Acceptance is
determined by corpus-owned workspace and answer oracles rather than model self-report.

## Metrics

Each run records:

- task completion and first-attempt parser/registry acceptance;
- tool-call and repair counts;
- model and tool latency;
- model token usage reported by GitHub Copilot CLI;
- response and tool-output bytes;
- stdout/stderr truncation;
- exact model, CLI, Python, platform, corpus version, and Git commit.
- corpus task, seed-file, total-byte, and largest-file dimensions.

Runs against an uncommitted worktree also record a deterministic hash of the evaluated
runtime, runner, corpus, and dependency files so the evidence is tied to exact content
rather than only the parent commit.

Tokens and provider latency are sample-level because one model session evaluates the
whole corpus. Tool calls, output bytes, truncation, failures, and completion remain
task-level.

## Running

From an installed development environment:

```powershell
python -m evaluations.command_usability.run `
  --output evaluations/command_usability/results/<run-name>
```

The default run uses two samples from GPT-5.6 Sol, Claude Sonnet 5, and Gemini 3.7 Flash.
`copilot` must already be authenticated. Results are committed only when the model list,
sample count, corpus, and environment fingerprint are complete.

The runner is intentionally outside `src/`: no model-provider or Copilot dependency
enters the runtime package. CI validates the model-free conformance/property suites and
does not make provider calls.

## Recorded baseline

The issue #21 baseline is committed under
[`results/2026-09-04`](./results/2026-09-04/REPORT.md). Across two samples from each of
the three model families, all 72 tasks completed, 70 of 72 first attempts passed parser
and registry admission, two rejected plans repaired successfully, and no tool output was
truncated.
