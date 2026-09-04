# Product Validation and Benchmark Design

**Status:** Milestone 4 direct reference implemented; OpenAI-first Milestone 5 proposed

## Purpose

This design turns the MemSandbox product thesis into executable evidence.

By the end of Milestone 5, MemSandbox should demonstrate that it:

1. supports deterministic, stateful agent execution across operations and sessions;
2. preserves the same behavior through supported agent-SDK adapters;
3. provisions its default in-memory sandbox with low and non-regressing latency;
4. uses reproducible comparative evidence before making a "fastest provisioning" claim.

Correctness and performance are separate gates. A fast sandbox that loses state is not
successful, and a correct sandbox cannot claim a provisioning advantage without measured
evidence.

## Product alignment

The product promise under test is:

- a stateful virtual workspace across tool calls, retries, snapshots, and sessions;
- deterministic command and file behavior over sandbox-owned state;
- framework-neutral core behavior exposed through thin adapters;
- effectively immediate provisioning for short-lived agent tasks;
- explicit limits and failures without host fallback.

Benchmark infrastructure must not enter the runtime hot path. The default core remains
dependency-free, in process, and constructible without a benchmark package, framework
SDK, model provider, network, container, or external service.

## Decision summary

Milestone 5 should deliver three related but distinct forms of evidence:

| Evidence | Question answered | Release role |
|---|---|---|
| Stateful execution conformance | Does state survive and behave identically through every supported surface? | Required correctness gate |
| Provisioning regression benchmarks | Is MemSandbox still fast, and did this change make it materially slower? | Required controlled baseline and regression gate |
| Comparative provisioning benchmarks | Is MemSandbox faster than named alternatives under a fair documented profile? | Required only for a comparative "fastest" claim |

Shared public CI is suitable for correctness and benchmark smoke tests. Stable performance
gates and comparative claims require a controlled runner because noisy shared machines
cannot establish reliable microsecond- or millisecond-scale latency conclusions.

## Claims and evidence matrix

| Product claim | Required evidence |
|---|---|
| Stateful across tool calls | One session performs ordered writes, reads, commands, patches, and state transitions |
| Stateful across sessions | Snapshot, close, resume into a new identity, verify, and continue |
| Safe retry and fork behavior | Repeated restore creates independent forks with identical starting state |
| Deterministic | Repeated scenarios produce the same normalized trace, hashes, revisions, and errors |
| Framework-neutral | Direct session and adapters produce equivalent normalized outcomes |
| Bounded | Exact limit tests cover workspace, output, timeout, snapshot, and benchmark workloads |
| No host fallback | Unsupported capabilities fail before host filesystem or shell access |
| Fast provisioning | Controlled cold, warm, adapter, first-operation, and resume measurements |
| Fastest provisioning | Reproducible comparison against named alternatives with capability differences disclosed |

## Terminology and timing boundaries

The suite uses explicit timing names rather than one ambiguous "startup time."

### Ready

A sandbox is ready only when:

- its session is in `RUNNING`;
- its handle or adapter session is published to the caller;
- ownership and lifecycle binding are complete;
- a first supported operation requires no hidden lazy session initialization.

Returning an object that performs material initialization on first use does not count as
ready.

### Provisioning metrics

| Metric | Start | End |
|---|---|---|
| `core_create_ready` | Immediately before `SandboxService.create` | A registered `RUNNING` session handle is returned |
| `adapter_create_ready` | Immediately before the adapter's public create/bind call | A framework-facing session is ready |
| `create_first_operation` | Before create or bind | A canonical first operation completes successfully |
| `cold_process_ready` | Before launching a fresh Python process | The child reports a ready session |
| `snapshot_create` | Before the host snapshot request | An immutable snapshot reference is returned |
| `resume_ready` | Before `SandboxService.resume` | A new registered `RUNNING` session is returned |
| `resume_first_operation` | Before resume | The first operation against restored state completes |
| `delete_complete` | Before host deletion | Registry removal and required cleanup complete |
| `burst_create` | Before a bounded batch of create requests | Every session is ready or the batch fails explicitly |

The primary product metric is warm in-process `core_create_ready` for an empty default
workspace. Cold process startup, seeded creation, adapter binding, and resume are reported
separately and must not be blended into one number.

### Adapter overhead

Adapter overhead is reported as both:

```text
adapter_create_ready - core_create_ready
adapter_create_ready / core_create_ready
```

The direct and adapter measurements use the same core build, options, fixture, process,
and runner. SDK import time, adapter construction, service construction, session
provisioning, and first operation are measured separately so an adapter cannot hide work
outside the reported interval.

## Suite architecture

The product-validation module owns the contracts consumed by scenarios and benchmark
cases. It does not depend directly on concrete SDK adapters.

```python
class ProductValidationDriver(Protocol):
    async def create(self, request: ValidationCreateRequest) -> ValidationSession: ...
    async def resume(self, request: ValidationResumeRequest) -> ValidationSession: ...
    async def delete(self, session: ValidationSession) -> None: ...


class ValidationSession(Protocol):
    async def execute(self, request: ValidationExecuteRequest) -> NormalizedResult: ...
    async def read_file(self, request: ValidationReadRequest) -> NormalizedResult: ...
    async def write_file(self, request: ValidationWriteRequest) -> NormalizedResult: ...
    async def apply_patch(self, request: ValidationPatchRequest) -> NormalizedResult: ...
    async def snapshot(self) -> ValidationSnapshotRef: ...
```

Drivers translate only lifecycle and operation shapes:

```text
reference scenario
  -> product-validation driver
    -> direct SandboxSession
    -> OpenAI Agents SDK sandbox client/session
    -> OpenAI Agents SDK custom capability
```

Every driver produces a normalized trace containing domain-relevant outcomes:

- operation kind and stable result or error category;
- resulting cwd and approved environment changes;
- workspace revision after committed operations;
- root hash at snapshot checkpoints where the existing lifecycle contract exposes it;
- file content hashes and bounded returned content;
- snapshot reference presence and restored state metadata;
- lifecycle transitions and cleanup result.

Framework-specific object identities, timestamps, model messages, and formatting do not
participate in equivalence.

## Stateful execution conformance

The mandatory reference scenario is scripted and model-free. It validates the sandbox
and adapters without model variability, API credentials, network latency, or token cost.

Issue #21 implements the direct `SandboxService`/`SandboxSession` reference during
Milestone 4. Milestone 5 adapters reuse the same logical scenario through drivers and
compare their normalized traces with that direct reference. The direct test retains
public codec, store, gateway, and sink fixtures only to observe persisted hashes,
provenance, and events; runtime actions remain limited to public service/session methods.

The complete direct contract and category matrix are defined in
[Direct Core Conformance](../../tests/conformance/README.md). Generated component
invariants are defined separately in
[Property and Stateful Test Design](../../tests/property/README.md).

### Reference lifecycle scenario

Each driver performs the following logical sequence:

1. Create an empty sandbox and confirm it is usable.
2. Create directories and write deterministic text fixtures.
3. Execute commands that inspect files and change session cwd.
4. Read a bounded range and retain its content hash.
5. Apply a hash-guarded patch and verify the revision and file content-hash transition.
6. Attempt one stale mutation and verify the expected stable conflict.
7. Create a reference snapshot containing workspace, cwd, and approved environment
   state, and retain its revision and root hash.
8. Close the original live session, prove later session operations fail, and then delete
   its service handle because session close does not unregister the record.
9. Attempt an operation through the closed original session and verify the stable
   lifecycle rejection.
10. Resume the step 7 reference snapshot into a new session identity.
11. Verify files, hashes, snapshot root hash, cwd, environment, limits, and unsupported
   capability behavior.

   `CreateSnapshotResult.content_hash` is the canonical complete-session state hash. The
   workspace root hash used at checkpoints is obtained by loading and decoding the
   persisted snapshot through the retained public store and codec; the two hashes are not
   interchangeable.
12. Continue executing, reading, writing, and patching restored state.
13. Resume the same step 7 reference snapshot two more times and prove the sessions are
   independent forks. There is no separate fork API.
14. Delete every live handle, close the service, and clean retained snapshots through
   their independent store lifecycle.

The Milestone 4 direct suite adds one core-only assertion after step 7: mutate the live
session beyond the checkpoint, call `SandboxSession.restore_snapshot()` on the same
identity, and verify atomic workspace, cwd, environment, revision, root-hash, and
`snapshot.restored` behavior. This host lifecycle operation is not part of the common
adapter driver protocol, so later adapter equivalence compares the shared trace
projection while the direct suite retains the additional core assertion.

The shared scenario uses only the common lifecycle and four-operation surface supported
by tool/capability and workspace/backend adapters. Binary round trips, streaming, and
other richer backend capabilities remain adapter-specific conformance cases and do not
change the shared reference trace.

When policy, events, and secrets are available, the same scenario also verifies:

- policy denial occurs before collaborator mutation;
- events preserve required ordering and contain no secret or unbounded content;
- secret leases do not enter workspace, snapshot, result, error, or benchmark artifacts;
- unsupported behavior never falls through to a host shell or filesystem.

### Required equivalence

The direct session trace is the reference. Every supported adapter must match:

- final file bytes and content hashes;
- workspace revision after each committed mutation;
- root hash at the defined snapshot checkpoints;
- cwd and approved environment state at snapshot and resume boundaries;
- stable error categories and normal command exit status;
- fork independence and lifecycle cleanup;
- explicit unsupported-capability outcomes.

Adapters may expose different framework response objects, but their normalized trace may
not change core meaning.

### Deterministic replay

Each conformance run starts from the same generated fixture and identifier seed where
identity values are not part of the behavior under test. Repeated runs must produce the
same normalized logical trace.

Time, random handles, operation IDs, and framework-owned metadata are normalized or
excluded. File content, paths, revisions, hashes, errors, and operation ordering are not
normalized away.

### Real-agent acceptance

The Milestone 4
[command-usability evaluation](../../evaluations/command_usability/README.md) runs
representative tasks through pinned model versions and the public direct-session tool
surface. It measures tool selection, repair turns, tokens, output size, truncation, and
task completion. Its results are design evidence rather than a correctness or
provisioning regression gate because model behavior and provider latency vary
independently of MemSandbox.

## Benchmark workloads

Benchmark cases use versioned generated profiles. Every result records exact file count,
directory count, total bytes, largest file, depth, and snapshot size rather than relying
only on a profile name.

The initial profile set includes:

| Profile | Purpose | Required tier |
|---|---|---|
| `empty` | Primary provisioning path and per-session overhead | PR smoke and controlled |
| `small_project` | Typical short agent task with a modest seeded tree | Controlled |
| `active_project` | Larger state, mutation, snapshot, and resume behavior | Controlled |
| `quota_edge` | Near-limit accounting, snapshot, and memory behavior | Scheduled or release |

Exact profile sizes are versioned with the benchmark suite. Changing a profile creates a
new profile version rather than silently rewriting historical results.

### Required benchmark cases

1. Empty core create and delete.
2. Empty adapter create, first operation, and delete.
3. Seeded create and first read/write operation.
4. Snapshot creation for each non-empty profile.
5. Resume to ready and resume through the first state-verifying operation.
6. Complete stateful reference scenario duration.
7. Bounded sequential creation of multiple independent sessions.
8. Resident memory for an empty session and each fixture profile.
9. Adapter overhead relative to the direct driver.

Burst measurements report both throughput and per-session latency. They remain bounded
and must not exhaust the host or turn a benchmark failure into a machine-wide failure.

## Measurement methodology

### Clocks and samples

- Use `time.perf_counter_ns()` for in-process elapsed time.
- Record raw samples in integer nanoseconds.
- Run warm-up iterations that are excluded and identified in metadata.
- Do not delete statistical outliers after observing results.
- Report sample count, minimum, median, p95, p99 when supported by sample count, mean,
  and standard deviation.
- Do not report p95 from fewer than 100 measured samples.
- Do not report p99 from fewer than 1,000 measured samples.
- Run cold-process cases in fresh child interpreters.
- Run warm cases in multiple fresh processes so one long-lived interpreter does not
  define the result.

The implementation task selects sample counts that meet a documented minimum duration
and quantile sample requirement. Quick PR smoke runs may use fewer samples but must be
marked non-comparable.

### Environment control

Every comparable result records:

- benchmark schema and profile versions;
- MemSandbox commit and package version;
- adapter and framework versions;
- Python implementation and version;
- operating system and architecture;
- CPU model and logical core count;
- available memory;
- runner identity and power configuration when available;
- cold or warm mode;
- process count, concurrency, warm-ups, and measured samples;
- garbage-collector configuration;
- whether network or external providers were involved.

Controlled results use a designated runner with stable power and workload settings.
Candidate and baseline cases run on the same machine in alternating or randomized order.

### Memory

The suite reports Python allocation peaks and, on controlled runners, process resident
memory. Empty-session memory is measured over a bounded batch because one session may be
smaller than process and allocator noise.

Memory tools remain benchmark-only development dependencies. They are never imported by
the runtime package.

### Isolation of external variance

The required provisioning suite performs no model calls. Adapter benchmarks construct
and invoke the adapter with deterministic local fakes where the framework contract
allows it.

Network-backed model providers, hosted sandboxes, object stores, and telemetry exporters
are excluded from core regression metrics. If measured, their latency is labeled and
reported separately.

## Output and result schema

Each run emits a machine-readable, versioned JSON artifact and a generated human summary.
The artifact contains:

```text
run metadata
  environment fingerprint
  source commit
  dependency versions
  benchmark schema version
  profile versions

case result
  driver and adapter
  timing mode
  exact workload dimensions
  raw successful samples
  failed sample count and error categories
  calculated statistics
  memory measurements
  correctness checksum
```

Failed samples are never discarded silently. A case with an unexpected failure cannot
produce a passing latency result.

The correctness checksum binds the normalized scenario trace, not elapsed time or random
identifiers. Performance results are comparable only when workload, schema, and
correctness checksum are compatible.

## Execution tiers and gates

| Tier | Trigger | Purpose | Gating |
|---|---|---|---|
| Conformance | Every PR | Stateful behavior and adapter equivalence | Required |
| Benchmark smoke | Relevant PRs | Harness validity and gross failure detection | Required, non-comparative |
| Controlled regression | Scheduled, release, or performance-sensitive PR | Candidate versus approved baseline | Required for Milestone 5 baseline and releases |
| Comparative | Explicit product evaluation | Substantiate a scoped market claim | Required only before publishing that claim |

### Regression budgets

Absolute latency budgets must not be invented before the first controlled baseline.
Milestone 5 records approved per-case budgets after baseline collection.

Each budget defines:

- the primary statistic, normally median or p95;
- an allowed relative regression;
- a minimum absolute noise floor;
- required samples and repetitions;
- the controlled runner class;
- whether the gate compares with a stored baseline or paired `main` run.

Shared CI may enforce only a broad sanity ceiling. It must not fail a pull request for a
small difference that is below measured runner noise.

Adapter budgets separately constrain:

- adapter construction;
- adapter create-to-ready overhead;
- first-operation overhead;
- snapshot and resume translation overhead.

An adapter cannot pass by moving required initialization outside the measured create
interval and into the first operation.

## Comparative provisioning evaluation

An internal regression suite proves that MemSandbox is fast and remains fast. It does not
prove that MemSandbox is the fastest available product.

Before publishing a comparative claim, the evaluation must:

1. Name every compared product, version, configuration, and SDK.
2. Define equivalent readiness and first-operation boundaries.
3. Run the same logical workspace profile and supported operation.
4. Separate cold and warm results.
5. Use the same machine where products can run locally.
6. For hosted products, disclose region, network path, account tier, and service-side
   warm-pool behavior.
7. Randomize or alternate product execution order.
8. Publish sample counts, distributions, failures, and environment metadata.
9. Disclose differences in security boundary, persistence, filesystem compatibility,
   networking, and command capability.
10. Avoid comparing a logical in-process sandbox with a container or VM as if they
    provided identical isolation.

The acceptable claim is scoped:

```text
MemSandbox had the lowest measured create-to-ready latency among <named systems> for
<profile> in <cold or warm mode> on <environment>, measured on <date>.
```

The project must not publish an unqualified "fastest sandbox" claim. If comparative
evidence is absent, stale, or statistically indistinguishable, product language remains
"optimized for fast provisioning."

## Proposed repository layout

The implementation milestone may add:

```text
benchmarks/
  README.md
  profiles/
  drivers/
    direct.py
    openai_sandbox.py
    openai_capability.py
  cases/
    provisioning.py
    snapshot_resume.py
    stateful_scenario.py
  runner.py
  schema.py
  reports.py
tests/
  conformance/
    product/
```

Benchmark framework and memory-measurement packages, if approved, belong only to
development dependency groups. The benchmark package may import MemSandbox and adapter
packages; production modules never import the benchmark package.

## Milestone 5 release requirements

Milestone 5 is not complete until:

- the scripted stateful scenario passes through the direct session, OpenAI sandbox
  client/session, and OpenAI capability drivers;
- cold, warm, first-operation, snapshot, resume, memory, and adapter-overhead cases are
  executable;
- one controlled baseline artifact is captured for every required driver and profile;
- regression budgets and runner assumptions are documented;
- benchmark results include the correctness checksum and environment fingerprint;
- product wording is consistent with the available evidence.

Comparative evaluation may occur after the Milestone 5 engineering exit, but a public
"fastest" claim remains blocked until that evaluation passes.

PydanticAI, Deep Agents, and other SDK drivers are added only with their own approved
adapter work. Their absence does not block the OpenAI-first Milestone 5 engineering exit.

Milestone 6 consumes the baseline to decide whether workspace content offload or a
simpler optimization addresses a measured constraint.

## Non-goals

- Measuring model inference or provider API latency as sandbox provisioning.
- Requiring live LLM calls in CI.
- Optimizing benchmark-only code in the runtime package.
- Claiming operating-system isolation equivalence.
- Selecting arbitrary absolute latency targets before a baseline exists.
- Hiding setup in fixtures, global state, adapter constructors, or the first operation.
- Ranking products with different capabilities without disclosing those differences.
- Adding content offload or another optimization before measurements identify a need.

## Open implementation decisions

The implementation issue must resolve:

- the designated controlled runner and how it is maintained;
- exact versioned workspace profile dimensions;
- minimum samples and duration for each case;
- approved metric-specific regression budgets;
- whether paired base-versus-candidate runs or stored baselines are authoritative;
- the benchmark framework and process-memory tool, if any;
- result artifact retention and publication location;
- the first comparative product matrix and refresh cadence.

## Maintenance rule

Changes to lifecycle readiness, stateful conformance, adapter equivalence, benchmark
timing boundaries, workload profiles, regression budgets, or comparative claim rules
must update this document and the corresponding Milestone 5 criteria.
