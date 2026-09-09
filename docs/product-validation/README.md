# Product Validation and Benchmark Design

**Status:** Three-driver conformance, benchmark harness, and non-gating reference implemented

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
| Provisioning measurements | How fast is MemSandbox on the named runner, and what should future regression work measure? | Required non-gating reference now; controlled gate only when introduced |
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
| `empty` | Primary provisioning path and per-session overhead | PR smoke, reference, and future controlled |
| `small_project` | Typical short agent task with a modest seeded tree | Reference and future controlled |
| `active_project` | Larger state, mutation, snapshot, and resume behavior | Future controlled |
| `quota_edge` | Near-limit accounting, snapshot, and memory behavior | Future scheduled or release |

Exact profile sizes are versioned with the benchmark suite. Changing a profile creates a
new profile version rather than silently rewriting historical results.

Milestone 6 workspace scalability uses the representative workload assumptions and
representative and controlled byte-versus-node matrix in
[Workspace Scalability Evidence](./workspace-scalability.md). It reuses
`active_project` and `quota_edge`, adds `content_heavy` with the active topology and 16x
bytes, and adds `node_heavy` with the active bytes and 4x files and directories. The two
added profiles are diagnostics, not new product defaults.

### Required benchmark cases

1. Empty core create and delete.
2. Empty adapter create, first operation, and delete.
3. Seeded create and first read/write operation.
4. Snapshot creation for each non-empty profile.
5. Resume to ready and resume through the first state-verifying operation.
6. Complete stateful reference scenario duration.
7. Bounded concurrent creation of multiple independent sessions.
8. Resident memory for an empty session and each fixture profile.
9. Adapter overhead relative to the direct driver.
10. Isolated workspace seed, hot read, same-size hot overwrite, directory copy, snapshot
    encoding, and retained-memory cases across the Milestone 6 diagnostic matrix.

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
- Run comparable controlled warm cases in multiple fresh processes so one long-lived
  interpreter does not define the result. Smoke and short reference artifacts may use
  one process because they are explicitly non-comparative and non-gating.

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
  exact operation file count and bytes
  encoded snapshot bytes where applicable
  raw successful samples
  failed sample count and error categories
  calculated statistics
  memory measurements
    retained Python allocation delta
    transient Python allocation peak
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
| Reference measurement | Milestone or design checkpoint | Short-duration approximate product metrics | Recorded, non-gating |
| Controlled regression | Scheduled, release, or performance-sensitive PR | Candidate versus approved baseline | Required only when automated performance gating is introduced |
| Comparative | Explicit product evaluation | Substantiate a scoped market claim | Required only before publishing that claim |

### Regression budgets

Absolute latency budgets must not be invented from smoke or reference measurements.
Milestone 5 records a short-duration non-gating reference. Controlled per-case budgets
are added only when the project introduces an automated release or regression gate.

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

## Implemented repository layout

The implementation uses:

```text
benchmarks/
  README.md
  profiles.py
  validation.py
  support.py
  drivers/
    direct.py
    openai.py
    core.py
  cases.py
  cold_worker.py
  runner.py
  schema.py
  reports.py
tests/
  benchmarks/
  conformance/
    product/
```

The implementation uses only the standard library for timing, statistics, subprocess
control, environment fingerprinting, and Python allocation peaks. No benchmark
dependency enters the runtime package. The benchmark package may import MemSandbox and
adapter packages; production modules never import the benchmark package.

The product driver contract is defined in `benchmarks.validation`, outside every runtime
adapter. OpenAI client/session lifecycle remains SDK-managed. The OpenAI sandbox driver
uses the provider's public `core_session` collaboration seam only for revisioned
operations, expected-hash preconditions, and domain-faithful results that the generic SDK
binary stream contract cannot express. The capability driver invokes only the four
model-facing tools.

Provider state version 1 was extended compatibly with defaulted `cwd` and
`approved_environment` fields. Newly persisted SDK state restores execution context
atomically with its portable workspace; older v1 payloads remain readable with the
historical root/empty defaults.

## Milestone 5 release requirements

Milestone 5 engineering evidence is complete when:

- the scripted stateful scenario passes through the direct session, OpenAI sandbox
  client/session, and OpenAI capability drivers;
- cold, warm, first-operation, snapshot, resume, memory, and adapter-overhead cases are
  executable;
- one short-duration reference artifact is captured for every required driver;
- the artifact is explicitly non-gating and records its runner assumptions;
- benchmark results include the correctness checksum and environment fingerprint;
- product wording is consistent with the available evidence;
- the issue #45 opt-in Azure OpenAI sample proves one real model can use the host-bound
  capability while required CI remains deterministic and network-free.

Comparative evaluation may occur after the Milestone 5 engineering exit, but a public
"fastest" claim remains blocked until that evaluation passes.

PydanticAI, Deep Agents, and other SDK drivers are added only with their own approved
adapter work. Their absence does not block the OpenAI-first Milestone 5 engineering exit.

The Azure sample is integration evidence, not benchmark evidence. Provider latency,
model behavior, credentials, and network availability must not enter conformance or
required benchmark gates.

Milestone 6 consumed the baseline and selected separately scoped phase instrumentation,
single-pass seeding, and immutable path-copying design as the direction that best
addresses the measured whole-tree mutation constraint. It preserves inline defaults and
defers content offload.

The Milestone 6 measurement contract, reproducible command, controlled comparisons, and
interpretation limits are maintained in
[Workspace Scalability Evidence](./workspace-scalability.md). The alternatives matrix,
boundary review, approved direction, and reconsideration triggers are maintained in the
[Workspace Scalability Decision](../components/workspace/scalability-decision.md).

## Non-goals

- Measuring model inference or provider API latency as sandbox provisioning.
- Requiring live LLM calls in CI.
- Optimizing benchmark-only code in the runtime package.
- Claiming operating-system isolation equivalence.
- Selecting arbitrary absolute latency targets before a baseline exists.
- Hiding setup in fixtures, global state, adapter constructors, or the first operation.
- Ranking products with different capabilities without disclosing those differences.
- Adding content offload or another runtime optimization inside the evidence milestone;
  implementation requires separately approved work.

## Implementation decisions

Resolved:

- benchmark artifact schema version 1 remains the immutable Milestone 5 historical
  contract; schema version 2 adds operation dimensions, encoded snapshot bytes, and
  retained Python allocation;
- generated Python profile version 1 defines `empty`, `small_project`,
  `active_project`, `quota_edge`, `content_heavy`, and `node_heavy` with exact recorded
  dimensions;
- `time.perf_counter_ns()` supplies raw integer samples;
- smoke uses one non-comparable sample and a bounded four-session burst;
- reference runs use two warm-ups and five measured in-process samples, with two fresh
  child interpreters per cold-process driver;
- p95 and p99 are withheld below 100 and 1,000 samples respectively;
- `tracemalloc` records Python allocation peaks without a new dependency;
- artifacts and Markdown summaries are generated under caller-selected,
  normally ignored `benchmark-results/` paths;
- milestone reference artifacts are retained under `benchmarks/results/`; the initial
  Windows development-workstation run contains 38 successful cases and no failures;
- the Milestone 6 decision preserves current inline quotas, treats lower process density
  as a host mitigation, prioritizes separately scoped internal tree work, and defers
  content offload until a measurable capacity trigger;
- comparative results remain separate and cannot authorize an unqualified product claim.

Deferred until automated performance gating is justified:

- designated runner identity, maintenance, and stable power configuration;
- measured runner noise and metric-specific budgets;
- authoritative paired-main versus stored-baseline policy;
- controlled artifact retention/publication;
- process RSS collection on that runner;
- fresh-process distribution and aggregation for controlled warm cases;
- controlled sample-count and minimum-duration defaults;
- the first comparative product matrix and refresh cadence.

## Maintenance rule

Changes to lifecycle readiness, stateful conformance, adapter equivalence, benchmark
timing boundaries, workload profiles, regression budgets, or comparative claim rules
must update this document and the corresponding Milestone 5 criteria.
