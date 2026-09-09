# Workspace Scalability Evidence

**Status:** Milestone 6A measurement contract

## Goal

Identify which current in-memory workspace cost grows first under intended agent workloads
before selecting an optimization or approving content offload. The evidence must separate
logical file bytes from node and path metadata, whole-tree mutation work, hash
recomputation, and snapshot encoding.

This document defines design assumptions rather than production telemetry. MemSandbox
does not yet collect customer workspace distributions. The assumptions are intentionally
bounded by the current default limits and must be revisited when anonymized deployment
data or a concrete host workload is available.

## Scope and acceptance

Milestone 6A:

- reuses the Milestone 5 `small_project`, `active_project`, and `quota_edge` definitions;
- adds only the controlled profiles needed to vary bytes and nodes independently;
- measures public `MemoryWorkspace` operations without model, adapter, command-parser,
  network, provider, or host-filesystem work;
- retains every raw integer-nanosecond sample, exact workload dimensions, a correctness
  checksum, Python allocation measurements, encoded snapshot bytes, source identity, and
  environment fingerprint;
- records conclusions, uncertainty, and unsupported claims alongside the raw artifact.

It does not select a storage provider, change runtime limits, implement content offload,
or establish a performance gate.

## Representative workload assumptions

The representative bands describe intended agent use. Session lifetime and snapshot
frequency are behavioral assumptions, not delays inserted into the benchmark. A
snapshot interval means "at a phase boundary or after approximately this many successful
mutations," whichever occurs first.

| Band | Logical bytes | Files / directories | Individual file size | Session lifetime | Snapshot frequency | Mutation and access pattern |
|---|---:|---:|---:|---|---|---|
| Short task (`small_project`) | 16 KiB | 16 / 4 | 1 KiB | 5-30 minutes | At handoff or completion; normally 0-2 | Repeated reads from a 1-4 file hot set; single-file writes |
| Active task (`active_project`) | 512 KiB | 128 / 16 | 4 KiB | 30-120 minutes | Phase boundary or about every 25 mutations | Reads concentrated in an 8-32 file hot set; full-file writes, patches, and occasional directory operations |
| Large bounded task (`quota_edge`) | 8 MiB | 256 / 32 | 32 KiB | 2-8 hours | Phase boundary or about every 25 mutations; retained snapshots are host-controlled | Hot-set reads plus occasional scans; mutations remain bounded to a file or subtree |

The distributions deliberately keep individual files well below the 4 MiB default file
limit. The current product is intended for source, configuration, logs, and generated
text artifacts, not unbounded media or dataset files. `quota_edge` is an upper workload
probe at half of the 16 MiB logical-byte limit; it is not a claim that typical sessions
operate near the quota.

Access assumptions affect the content-offload decision: reads are hot-set biased, while
snapshots cover the whole workspace. A future provider design therefore cannot infer
that all logical bytes are cold, and a benchmark that performs only sequential scans
would not represent intended use.

## Controlled diagnostic profiles

The scalability suite uses `active_project` as the shared baseline and adds two profile
version 1 diagnostics:

| Profile | Files | Directories | Logical bytes | File bytes | Role |
|---|---:|---:|---:|---:|---|
| `active_project` | 128 | 16 | 512 KiB | 4 KiB | Shared baseline |
| `quota_edge` | 256 | 32 | 8 MiB | 32 KiB | Representative combined upper probe |
| `content_heavy` | 128 | 16 | 8 MiB | 64 KiB | Same topology and node count; 16x logical and individual file bytes |
| `node_heavy` | 512 | 64 | 512 KiB | 1 KiB | Same logical bytes; 4x files and directories |

All content and paths are generated deterministically from profile name, version, and
file index. Each profile uses a uniform file size so total bytes, per-file bytes, and node
count are exact controlled variables rather than hidden distributions. `quota_edge`
checks the combined upper representative band; `content_heavy` and `node_heavy` are
controlled diagnostics, not new product defaults.

## Measurement cases

Each measured sample receives a fresh workspace. Fixture generation occurs before the
timer except in the retained-memory case, where generation must occur after
`tracemalloc` starts so live file bytes are represented.

| Case | Timed boundary | Access or mutation shape | Primary evidence |
|---|---|---|---|
| `workspace_seed` | First public write to final committed seed write | Sequential creation of every generated file | Provisioning, repeated tree copy, and repeated hash cost |
| `workspace_read_hot` | Start of one complete read to returned immutable bytes | One deterministic middle file | Full-file hot-read sensitivity to individual file size |
| `workspace_overwrite_hot` | Start of one conditional write to committed result | Same-size replacement of one deterministic middle file | Whole-tree clone and hash recomputation under constant mutation size |
| `workspace_copy_directory` | Start of one copy request to committed result | One directory containing eight files | Subtree-copy amplification plus whole-tree commit work |
| `workspace_snapshot_encode` | Start of workspace export to encoded snapshot value | Whole workspace | Snapshot traversal, content hashing/base64, canonical JSON, and encoded size |
| `workspace_retained_memory` | Same seed boundary as `workspace_seed` | Whole live workspace retained after fixture references are released | Python retained-allocation delta and transient peak |

The artifact records `operation_file_count` and `operation_bytes` in addition to seeded
profile dimensions. Snapshot cases also record `snapshot_bytes`.
Memory fields retain the maximum observed value across successful measured samples.
The retained-memory case runs with `tracemalloc` enabled; its elapsed time is
instrumented diagnostic time and is not comparable with normal `workspace_seed`
latency.

## Reproduction

Run from a clean worktree with Python 3.12 and the locked environment:

```console
uv sync --all-groups
set MEM_SANDBOX_BENCHMARK_RUNNER=<runner-name>
set MEM_SANDBOX_POWER_CONFIGURATION=<power-description>
uv run python -m benchmarks.runner ^
  --tier scalability ^
  --output benchmarks/results/<run>/workspace-scalability-v2.json
```

On PowerShell, set the two environment variables with `$env:<NAME> = '<value>'`.
The runner writes the raw JSON artifact and a generated Markdown table. Reference
defaults are two excluded warm-ups and five retained samples per case. Reference results
are non-gating and must not be compared across different source-tree hashes or
environment fingerprints as if they were controlled experiments.

## Captured reference

The 2026-09-09 Windows development-workstation
[raw artifact and analysis](../../benchmarks/results/2026-09-09-windows-development/README.md)
contain 24 successful cases, 120 measured samples, 48 excluded warm-ups, environment and
source fingerprints, exact dimensions, encoded snapshot sizes, retained-allocation
measurements, and correctness checksums.

The evidence identifies the whole-tree mutation pipeline as the primary operational
scaling constraint. With logical bytes held at 512 KiB, increasing files and directories
4x raised seed median 15.65x, same-size overwrite 4.92x, and directory copy 3.46x.
Increasing bytes 16x with topology fixed raised those medians 3.39x, 3.79x, and 2.24x,
respectively, so repeated full-tree content hashing is also material.

Resident Python allocation and portable snapshot size are meaningful secondary
constraints. Increasing content from 512 KiB to 8 MiB with topology fixed raised maximum
retained allocation from 535.5 KiB to 8,215.5 KiB and encoded snapshot size from
703.9 KiB to 10,943.9 KiB. Increasing nodes 4x at the same logical bytes raised retained
allocation only to 601.1 KiB and snapshot size to 766.8 KiB.

The public-boundary suite cannot split the mutation result among tree cloning, file
hashing, directory hashing, and counter recomputation. Internal phase instrumentation is
the required next measurement before selecting a mutation optimization. The evidence
also does not establish production percentiles, process RSS, long-session leakage, or a
need for content offload.

## Interpretation rules

- Compare `active_project` with `content_heavy` to estimate sensitivity to resident and
  repeatedly hashed file bytes while holding topology constant.
- Compare `active_project` with `node_heavy` to estimate sensitivity to tree and metadata
  volume while holding logical bytes constant.
- Use overwrite results for the common whole-tree commit path. Directory-copy results
  include additional subtree-copy work and must not be attributed only to commit cost.
- Use retained Python allocation as process-local evidence, not total process RSS.
  Allocator behavior, interpreter internals, and benchmark objects remain part of the
  measurement uncertainty.
- Use encoded snapshot bytes and latency together. Base64 and canonical envelope growth
  can make snapshot cost material even when live workspace retention is acceptable.
- Do not infer long-session leakage from fresh-workspace samples. A soak or lifetime
  study requires a separate issue if deployment evidence indicates that need.

Content offload is supported only if resident content bytes are a material primary
constraint for representative workloads. If tree cloning, whole-tree hashing, metadata,
or snapshot encoding dominates first, those costs require independent treatment and
content offload must remain deferred.
