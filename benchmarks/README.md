# Product Validation Benchmarks

This package owns model-free correctness and performance evidence. Runtime modules never
import it.

## Profiles

Profile schema version 1 defines:

| Profile | Files | Directories | Bytes | Largest file | Depth |
|---|---:|---:|---:|---:|---:|
| `empty` | 0 | 0 | 0 | 0 | 0 |
| `small_project` | 16 | 4 | 16 KiB | 1 KiB | 2 |
| `active_project` | 128 | 16 | 512 KiB | 4 KiB | 2 |
| `quota_edge` | 256 | 32 | 8 MiB | 32 KiB | 2 |

Changing any dimension requires a new profile version.

## Execution

PR smoke:

```text
python -m benchmarks.runner --tier smoke --output benchmark-results/smoke.json
```

Short-duration reference measurement:

```text
python -m benchmarks.runner --tier reference --output benchmarks/results/<run>/reference.json
```

Smoke uses one raw sample per case and is non-comparative. Reference uses two warm-ups
and five measured samples for in-process cases; cold process readiness uses two fresh
interpreters per driver. It is committed as approximate, non-gating engineering
evidence. Both implemented tiers preserve every integer nanosecond sample. P95 and p99
remain absent below 100 and 1,000 samples respectively.
Artifacts record both the current Git commit and a source-tree hash; reference runs from
an implementation worktree are explicitly marked dirty rather than being attributed
only to the checked-out base commit.

The suite performs no model calls, network requests, container operations, or hosted
provider work. Cold-process measurements use fresh local Python interpreters. Python
allocation peaks use `tracemalloc`; controlled runner integrations may additionally
record process RSS without adding a runtime dependency.

Controlled execution is intentionally deferred. Before adding that tier, the runner must
distribute warm cases across fresh processes, record the real process count, stabilize
power and workload conditions, and support paired candidate/baseline execution.

## Baselines and budgets

Shared CI validates the harness only. Reference artifacts document approximate behavior
on a named development machine but do not establish latency budgets.

A release-gating baseline must record
`MEM_SANDBOX_BENCHMARK_RUNNER` and `MEM_SANDBOX_POWER_CONFIGURATION`, run candidate and
baseline on the same controlled machine, and measure runner noise before approving a
relative budget plus absolute noise floor. Cases that cannot obtain a defensible budget
remain reported and explicitly non-gating.

Comparative product measurements are separate artifacts and must follow
`docs/product-validation/README.md`; the project does not make an unqualified "fastest
sandbox" claim.
