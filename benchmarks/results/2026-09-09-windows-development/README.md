# Workspace Scalability Reference

**Captured:** 2026-09-09

This run is the Milestone 6A short-duration workspace scalability reference. It is
approximate engineering evidence from an uncontrolled development workstation, not a
performance gate.

## Artifacts

- Raw samples:
  [`workspace-scalability-v2.json`](./workspace-scalability-v2.json)
- Generated report:
  [`workspace-scalability-v2.md`](./workspace-scalability-v2.md)
- Measurement contract:
  [`docs/product-validation/workspace-scalability.md`](../../../docs/product-validation/workspace-scalability.md)

The artifact contains 24 successful cases, 120 measured samples, 48 excluded warm-up
samples, no failures, and one correctness checksum per case. Its benchmark/runtime
source-tree hash is
`4c24f9a0321a32bf2f7083d5c5a325e0720f6aee7d7370a24f5e2f947a6aa688`.
The worktree was dirty because the benchmark implementation and documentation had not
yet been committed; the source-tree hash identifies the Python sources actually run.

## Environment

- Python: CPython 3.12.12
- Host: Windows 11 `10.0.22631`
- CPU: Intel64 Family 6 Model 106 Stepping 6, GenuineIntel
- Logical CPUs: 16
- Runner identity: `windows-development-workstation`
- Power configuration: `uncontrolled-development-workstation`
- Sampling: two warm-ups and five measured samples per case
- Network and external providers: none

## Reproduction

From the repository root:

```powershell
uv sync --all-groups
$env:MEM_SANDBOX_BENCHMARK_RUNNER = 'windows-development-workstation'
$env:MEM_SANDBOX_POWER_CONFIGURATION = 'uncontrolled-development-workstation'
uv run python -m benchmarks.runner `
  --tier scalability `
  --output benchmarks\results\<run>\workspace-scalability-v2.json
```

## Observations

Medians below are non-gating. Ratios compare each diagnostic profile with
`active_project`.

| Case | Active median | Content-heavy median / ratio | Node-heavy median / ratio |
|---|---:|---:|---:|
| Seed | 246.661 ms | 835.726 ms / 3.39x | 3,859.589 ms / 15.65x |
| Hot read | 0.036 ms | 0.107 ms / 2.95x | 0.036 ms / 0.98x |
| Same-size overwrite | 3.437 ms | 13.017 ms / 3.79x | 16.912 ms / 4.92x |
| Copy eight-file directory | 5.500 ms | 12.313 ms / 2.24x | 19.033 ms / 3.46x |
| Snapshot encode | 16.662 ms | 366.948 ms / 22.02x | 26.963 ms / 1.62x |

`content_heavy` holds 128 files and 16 directories constant while increasing logical
bytes from 512 KiB to 8 MiB. `node_heavy` holds 512 KiB constant while increasing files
from 128 to 512 and directories from 16 to 64.

The representative combined `quota_edge` profile (256 files, 32 directories, 8 MiB)
measured:

- 2,409.097 ms seed;
- 0.071 ms hot read;
- 17.004 ms same-size overwrite;
- 16.282 ms eight-file directory copy;
- 289.893 ms snapshot encoding;
- 10,964.5 KiB encoded snapshot;
- 8,237.4 KiB maximum retained Python allocation.

The retained-memory case is instrumented with `tracemalloc`; its elapsed time is not a
normal operation-latency measurement.

## Findings

**Primary mutation constraint:** the current whole-tree mutation pipeline is the first
operational scaling constraint. Every seed write and later mutation clones and
remeasures the full tree. Holding logical bytes constant while increasing node count 4x
raised seed time 15.65x, same-size overwrite 4.92x, and directory copy 3.46x. Holding
topology constant while increasing bytes 16x also raised those medians, which shows
whole-tree content hashing remains material. The public-boundary measurements do not
separate clone time from file hashing, directory hashing, and counter recomputation;
internal phase instrumentation is required before choosing among those optimizations.

**Material secondary constraints:** resident file bytes and portable snapshot encoding
scale with logical content. At 512 KiB, `active_project` retained 535.5 KiB; at 8 MiB,
`content_heavy` retained 8,215.5 KiB. With the same 512 KiB but 4x nodes, `node_heavy`
retained 601.1 KiB, so metadata is observable but secondary to content bytes for live
memory in this matrix. Snapshot size grew from 703.9 KiB to 10,943.9 KiB under the
content-controlled comparison, while the node-controlled snapshot was 766.8 KiB.
Content bytes therefore dominate retained Python allocation and portable snapshot size;
node traversal remains a smaller snapshot latency contributor.

**Not a current constraint:** complete hot reads remained between 0.036 and 0.107 ms in
the controlled profiles. This does not establish byte-range or provider-backed read
behavior.

## Uncertainty and unsupported conclusions

- Five samples on an uncontrolled Windows workstation support order-of-magnitude
  observations, not small percentage comparisons or cross-platform budgets.
- Python retained allocation is not process RSS and does not include every allocator or
  interpreter effect.
- Generated uniform files at depth two do not establish production percentiles.
- Fresh workspaces do not test multi-hour leak behavior, snapshot retention, or mutation
  history.
- The measurements do not prove that content offload is needed, select an offload
  threshold, or quantify provider-backed behavior.
- The exact time split among tree clone, file hash, directory hash, and counter
  recomputation remains unsupported without internal phase instrumentation.

The evidence supports optimizing or instrumenting whole-tree mutation work before
treating content offload as the primary scalability fix. Content offload remains
potentially relevant to logical capacity, live content residency, and provider-linked
snapshot size only when real deployment workloads require more capacity than the
current bounded profile.
