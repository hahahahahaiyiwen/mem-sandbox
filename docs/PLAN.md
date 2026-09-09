# MemSandbox Implementation Plan

**Status:** Active post-Milestone-5 roadmap
**Current focus:** Milestone 6 workspace scalability evidence and decision are complete.
Milestones 0 through 5 are complete; future workspace optimizations and external
capabilities remain separately scoped, unimplemented, and disabled by default.

**Approved design inputs:** [High-Level Design](./HIGH_LEVEL_DESIGN.md),
[Workspace Design](./components/workspace/README.md), and
[Command Executor Design](./components/command-executor/README.md). The workspace
scalability outcome is recorded in the
[Workspace Scalability Decision](./components/workspace/scalability-decision.md).
Approved future directions are detailed in
[Controlled Network Egress](./components/network-egress/README.md) and
[External Execution and Python Runtime](./components/external-execution/README.md).

## 1. Recommendation

Start with the workspace, then build the command executor on top of it.

The order matters:

```text
domain foundations
  -> workspace MVP
  -> command executor MVP
  -> minimal SandboxSession vertical slice
  -> complete workspace/executor behavior
  -> snapshots, events, and SandboxService
  -> framework adapters
  -> workspace scalability evidence
  -> external capability profiles, authority, and resource accounting
  -> controlled outbound HTTP
  -> external Python execution
  -> conditional durable/remote operation and additional integrations
```

The workspace is the state model and consistency boundary. The command executor consumes
workspace interfaces, so implementing both independently or in parallel would force the
executor to guess path, mutation, quota, and error semantics.

Do not wait until every filesystem feature and shell command is complete before composing
a session. After the workspace and a small command set work, build one vertical slice
through the four agent-facing operations. That validates the architecture before the
implementation becomes large.

Milestones 0 through 5 followed that sequence and are complete. Future work must preserve
the current virtual profile rather than gradually turning its constrained interpreter
into a host shell. External storage, networking, repository transfer, and code execution
are separate capabilities with separate authority, limits, events, and failure
semantics.

## 2. Implementation principles

- Write or update behavior tests before implementation.
- Work in red -> green -> refactor increments.
- Keep the core importable without an agent SDK.
- Use constructor injection for every collaborator.
- Keep interfaces beside their consuming module.
- Use domain models rather than dictionaries at boundaries.
- Store files as bytes; add explicit UTF-8 text helpers.
- Keep model-facing reads bounded even though internal binary reads are complete.
- Reject unsupported behavior instead of using the host filesystem or shell.
- Serialize every public operation within a session and every direct workspace operation
  until a later issue explicitly introduces concurrent reads.
- Use one coarse workspace state lock in version 1; do not introduce object-level locks,
  MVCC, or internal optimistic retries.
- Preserve snapshot compatibility through explicit schema versions.
- Keep the default virtual profile free of host filesystem, process, and network
  authority.
- Select optional capabilities through immutable host configuration; model input may
  narrow granted behavior but cannot enable a capability or increase a limit.
- Route every external dependency through a focused constructor-injected boundary.
- Reserve and settle cumulative resources in addition to enforcing per-operation limits.
- Keep full POSIX compatibility outside this plan. Evaluate MCP, remote transports, and
  additional agent SDKs only after their underlying core boundaries are proven.

## 3. Proposed project structure

The project identity is:

- display name: **MemSandbox**
- GitHub repository: `hahahahahaiyiwen/mem-sandbox`
- distribution name: `mem-sandbox`
- import package: `mem_sandbox`
- future CLI command: `mem-sandbox`
- local orchestration checkout: `repos/mem-sandbox`

```text
pyproject.toml
src/
  mem_sandbox/
    core/
      models.py
      errors.py
      session.py
      service.py
    workspace/
      models.py
      paths.py
      reader.py
      mutator.py
      memory.py
      patching.py
      snapshot_codec.py
    commands/
      models.py
      parser.py
      registry.py
      executor.py
      context.py
      builtins/
    policy/
    secrets/
    events/
    snapshots/
    adapters/
      tool_capability/
      workspace_backend/
tests/
  unit/
  integration/
  conformance/
```

Avoid a global `protocols.py`. Each consuming module owns the narrow protocols it needs.

## 4. Milestone 0: foundation and decisions

### Goal

Create the package, quality gates, and shared conventions needed to implement the
workspace without introducing an agent-framework dependency.

### Work

#### 0.0 Repository bootstrap

- [x] **0.0.1** Create `hahahahahaiyiwen/mem-sandbox`, clone it at
  `repos/mem-sandbox`, and configure `origin`.
- [x] **0.0.2** Add the initial `README.md` and Python `.gitignore`.
- [x] **0.0.3** Add `mem-sandbox` to the orchestration `RESOURCE-MAP.yml` with its local
  path, GitHub URL, Python language, active status, and worktree convention.
- [x] **0.0.4** Add an MIT `LICENSE`.
- [x] **0.0.5** Add `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `SECURITY.md` with
  repository-specific development and vulnerability-reporting guidance.
- [x] **0.0.6** Expand the repository README with the MemSandbox tagline, project
  boundary, logical-sandbox security statement, current status, and development commands.
- [x] **0.0.7** Move the approved Python design documents into `mem-sandbox/docs`,
  replace orchestration indexes with links, and mark retained copies as archival.
- [x] **0.0.8** Adopt short-lived issue branches, retire the local `dev` branch after
  verifying it has no unique commits, and document the intended protected-`main`
  workflow. Server-side enforcement is deferred while this private personal repository
  is on a GitHub plan that does not provide branch protection.

#### 0.1 Project decisions

- [x] **0.1.1** Use **MemSandbox** as the display name, `mem-sandbox` for the repository
  and distribution, and `mem_sandbox` for the Python import package.
- [x] **0.1.2** Support Python 3.12 or later.
- [x] **0.1.3** Release the project under the MIT license.
- [x] **0.1.4** Use uv, Hatchling, pytest with async support, Ruff, and Pyright.
- [x] **0.1.5** Use protected `main` plus short-lived issue branches; do not retain a
  long-lived `dev` integration branch.
- [x] **0.1.6** Run CI on Windows and Ubuntu using Python 3.12 and the latest stable
  Python release, initially Python 3.14.
- [x] **0.1.7** Make `mem-sandbox/docs` authoritative for the approved design documents
  and retain links from the orchestration repository.
- [x] **0.1.8** Use `/workspace`, one-based inclusive text ranges, and initially
  serialized session operations.
- [x] **0.1.9** Start at version `0.1.0` and use semantic versioning for public releases.
- [x] **0.1.10** Keep the repository private until a later explicit visibility decision.

#### 0.2 Package scaffold

- [x] **0.2.1** Create `pyproject.toml`, the `src/mem_sandbox` package tree, and the
  `tests/unit`, `tests/integration`, and `tests/conformance` trees.
- [x] **0.2.2** Configure Hatchling as the build backend and add complete PyPI project
  metadata for `mem-sandbox` version `0.1.0`.
- [x] **0.2.3** Configure pytest with async support, Ruff formatting and linting, and
  Pyright strict type checking.
- [x] **0.2.4** Configure uv development dependency groups, commit `uv.lock`, and keep
  the initial production dependency set limited to the Python standard library.
- [x] **0.2.5** Add GitHub Actions for Windows and Ubuntu with Python 3.12 and 3.14,
  running package build, formatting, linting, type checking, and tests.

#### 0.3 Shared domain conventions

- [x] **0.3.1** Define immutable session, operation, snapshot, and revision identifiers.
- [x] **0.3.2** Define constructor-injected clock and identifier-generator protocols
  beside the modules that consume them.
- [x] **0.3.3** Define the stable domain error taxonomy, including invalid request,
  not found, conflict, quota, policy denial, timeout, cancellation, unsupported behavior,
  and internal failure.
- [x] **0.3.4** Define immutable request and result conventions without introducing a
  global interface or protocol module.

#### 0.4 Foundation tests

- [x] **0.4.1** Add a package import test that runs with no agent SDK installed.
- [x] **0.4.2** Add tests for identifier equality, error-category stability, and injected
  clock behavior.
- [x] **0.4.3** Add a dependency-boundary test that fails if core imports an agent SDK.

### Approved decisions

- `mem-sandbox` distribution with the `mem_sandbox` import package.
- Python 3.12 or later.
- MIT license.
- uv with Hatchling for environment, dependency, and build workflows.
- `pytest` with async support for behavior tests.
- Ruff for formatting and linting, with Pyright in strict mode.
- Protected `main` with short-lived issue branches.
- Windows and Ubuntu CI on Python 3.12 and 3.14.
- `mem-sandbox/docs` as the authoritative design location.
- `/workspace`, one-based inclusive text ranges, and serialized session operations.
- Initial version `0.1.0` using semantic versioning.
- Private repository visibility until a later explicit decision.
- Server-side `main` protection deferred until the repository becomes public or its
  GitHub plan supports protection for private repositories.
- Frozen dataclasses or similarly immutable request/result models.
- No production dependency beyond the standard library during the workspace milestone.

All Milestone 0 decisions are resolved.

### Exit criteria

- [x] **0.9.1** `uv sync --all-groups` and `uv build` succeed from a clean checkout.
- [x] **0.9.2** `uv run python -c "import mem_sandbox"` succeeds.
- [x] **0.9.3** `uv run pytest tests/unit/foundation -q` passes.
- [x] **0.9.4** `uv run ruff format --check src tests`, `uv run ruff check src tests`, and
  `uv run pyright src tests` pass.
- [x] **0.9.5** The dependency-boundary test proves that core imports and runs without
  OpenAI, PydanticAI, LangChain, MCP, or another agent SDK installed.
- [x] **0.9.6** `git remote get-url origin` returns
  `https://github.com/hahahahahaiyiwen/mem-sandbox.git`, and the orchestration resource
  map resolves the same repository.
- [x] **0.9.7** The repository contains its approved license and OSS contribution,
  conduct, security, and development documentation.
- [x] **0.9.8** Required GitHub checks pass on Windows and Ubuntu for Python 3.12 and
  3.14.
- [x] **0.9.9** No long-lived `dev` branch remains, and the pull-request-only workflow is
  documented. GitHub enforcement is explicitly deferred under the private-repository
  limitation recorded in `0.0.8`.

## 5. Milestone 1: workspace MVP

### Goal

Build a deterministic, quota-safe in-memory filesystem that supports the first useful
agent scenario without reading from or writing to the host filesystem.

### Work

#### 1.1 Path value object

- [x] **1.1.1** Write failing tests for POSIX normalization, the `/workspace` root,
  relative resolution against a supplied cwd, and case-sensitive comparison.
- [x] **1.1.2** Implement `SandboxPath` without converting through host-native path
  rules.
- [x] **1.1.3** Add rejection tests and implementation for untrusted `..`, NUL, empty
  paths, oversized paths, oversized segments, and invalid segments.
- [ ] **1.1.4** Run the same path case table on Windows and non-Windows CI workers.

#### 1.2 Node and metadata model

- [x] **1.2.1** Write tests for empty roots, nested directories, arbitrary file bytes,
  immutable metadata views, and rejected special node types.
- [x] **1.2.2** Implement `DirectoryNode` and `FileNode` with arbitrary byte content.
- [x] **1.2.3** Implement immutable metadata views, total byte accounting, node counting,
  workspace revision, and content hashes.
- [x] **1.2.4** Reject symbolic links and all unsupported special node types explicitly.

#### 1.3 Read behavior

- [x] **1.3.1** Write tests for `stat`, stable lexical directory listing, complete binary
  reads, and complete UTF-8 text reads.
- [x] **1.3.2** Implement `stat`, directory listing, and binary reads with deterministic
  ordering and typed errors.
- [x] **1.3.3** Implement UTF-8 text reads that fail explicitly on invalid UTF-8 without
  affecting binary reads.
- [x] **1.3.4** Implement one-based inclusive line-range reads with response byte and
  line limits.

#### 1.4 Core mutation behavior

- [x] **1.4.1** Write atomicity tests proving failed mutations leave nodes, counters,
  hashes, and revision unchanged.
- [x] **1.4.2** Implement create-directory behavior, including explicit parent handling
  and already-exists errors.
- [x] **1.4.3** Implement atomic complete-file writes for both create and replace paths.
- [x] **1.4.4** Implement file removal, empty-directory removal, and recursive-directory
  removal.

#### 1.5 Quotas, revisions, and hashes

- [x] **1.5.1** Write exact-minimum, exact-maximum, one-below, and one-above tests for
  file bytes, total bytes, node count, path length, segment length, and patch bytes.
- [x] **1.5.2** Enforce every quota under the same atomic mutation boundary.
- [x] **1.5.3** Increment the workspace revision exactly once for each successful
  mutation and never for a failed mutation.
- [x] **1.5.4** Compute deterministic file, directory, and root hashes and test that
  identical states produce identical hashes.

#### 1.6 Copy and move

- [x] **1.6.1** Write tests for file and directory copy, overwrite conflicts, descendant
  conflicts, quota failures, and source-equals-destination behavior.
- [x] **1.6.2** Implement atomic copy through workspace-owned operations.
- [x] **1.6.3** Implement atomic move without exposing an intermediate missing or
  partially copied state.

#### 1.7 Context-aware patching

- [x] **1.7.1** Select and document one explicit context-aware patch format for core.
- [x] **1.7.2** Write tests for successful patches, malformed patches, missing context,
  stale `expected_hash`, quota failure, and multi-file atomicity.
- [x] **1.7.3** Implement hash-guarded patching so every failed patch leaves the workspace
  unchanged.

#### 1.8 Workspace snapshot codec

- [x] **1.8.1** Select and document a deterministic, versioned, non-pickle workspace
  snapshot encoding.
- [x] **1.8.2** Write tests for deterministic bytes, integrity failures, unsupported
  versions, bounded decode, malformed input, and atomic restore.
- [x] **1.8.3** Implement workspace export with an explicit schema version and integrity
  hash.
- [x] **1.8.4** Implement atomic restore with no shared mutable nodes between source and
  restored workspaces.

### Executable acceptance scenario

Implement `tests/integration/workspace/test_workspace_round_trip.py` with this sequence:

1. Create `/workspace/src`.
2. Write `/workspace/src/app.py` and `/workspace/README.md`.
3. List `/workspace` and assert lexical ordering.
4. Read lines 1-2 from `app.py`.
5. Patch `app.py` using its expected hash.
6. Copy `src` to `backup`.
7. Export the workspace.
8. Mutate the live workspace.
9. Restore the exported workspace.
10. Assert the original files, root hash, counters, and revision state.

### Exit criteria

- [x] **1.9.1** `python -m pytest tests/unit/workspace -q` passes.
- [x] **1.9.2** `python -m pytest tests/integration/workspace/test_workspace_round_trip.py -q`
  passes.
- [ ] **1.9.3** The Windows and Linux path-test matrix passes with identical expected
  outcomes.
- [x] **1.9.4** Atomicity tests prove every failed write, remove, copy, move, patch, quota
  check, and restore preserves the complete prior state.
- [x] **1.9.5** Snapshot round-trip tests reproduce the same files, counters, root hash,
  and revision state without shared mutable nodes.
- [x] **1.9.6** `docs/components/workspace/README.md` describes the implemented
  paths, ranges, quotas, atomicity, hashing, and snapshot encoding.

## 6. Milestone 2: command executor MVP

### Goal

Execute a small deterministic command language entirely through workspace ports, with no
host shell, host executable lookup, or host filesystem access.

### Work

#### 2.1 Execution contracts

- [x] **2.1.1** Write contract tests for `ExecuteRequest`, `ExecuteResult`,
  `CommandRequest`, `CommandResult`, and `CommandExecutionContext`.
- [x] **2.1.2** Implement immutable execution models that distinguish normal non-zero
  command exits from executor failures.
- [x] **2.1.3** Implement bounded stdout and stderr collectors with deterministic,
  observable truncation.
- [x] **2.1.4** Keep command requests explicitly stdin-aware, with empty stdin in the
  first wave, so later bounded pipelines do not require a handler-contract rewrite.

#### 2.2 Tokenizer and parser

- [x] **2.2.1** Write tokenizer tests for whitespace, single quotes, double quotes,
  escapes, argv construction, and approved environment expansion.
- [x] **2.2.2** Implement tokenization separately from parsing and execution-plan
  evaluation.
- [x] **2.2.3** Write parser tests for `;`, `&&`, `>`, and `>>`.
- [x] **2.2.4** Implement explicit parse errors for pipelines, `||`, input redirection,
  heredocs, background jobs, command substitution, and incomplete input.

#### 2.3 Command registry

- [x] **2.3.1** Write tests for deterministic lookup, explicit aliases, duplicate names,
  and registry immutability after startup.
- [x] **2.3.2** Implement a constructor-injected registry of focused command handlers.
- [x] **2.3.3** Generate agent-facing command descriptions only from registered command
  metadata.
- [x] **2.3.4** Include stdin acceptance in command metadata; every first-wave command
  rejects non-empty stdin.

#### 2.4 First command wave

- [x] **2.4.1** Define focused workspace reader and mutator ports owned by the command
  executor module.
- [x] **2.4.2** Write behavior tests for `pwd`, `cd`, `ls`, and `cat`.
- [x] **2.4.3** Implement `pwd`, `cd`, `ls`, and `cat` through workspace ports.
- [x] **2.4.4** Write behavior tests for `echo`, `mkdir`, `touch`, and `rm`.
- [x] **2.4.5** Implement `echo`, `mkdir`, `touch`, and `rm` through workspace ports.

#### 2.5 Sequencing and redirection

- [x] **2.5.1** Write execution-plan tests for unconditional `;` sequencing and
  success-dependent `&&` sequencing.
- [x] **2.5.2** Implement sequencing with deterministic exit-code propagation.
- [x] **2.5.3** Write tests for create/truncate redirection with `>` and append
  redirection with `>>`, including quota and target-type failures.
- [x] **2.5.4** Implement redirection as atomic workspace mutations.

#### 2.6 State, timeout, cancellation, and output

- [x] **2.6.1** Write tests proving cwd and approved environment changes are returned
  explicitly rather than hidden inside command handlers.
- [x] **2.6.2** Implement execution-context state transitions for cwd and approved
  environment variables.
- [x] **2.6.3** Write timeout and cancellation tests at command and complete-plan
  boundaries.
- [x] **2.6.4** Implement cooperative cancellation and a complete-plan timeout that
  prevents later mutations after timeout is reported.
- [x] **2.6.5** Write and implement exact-boundary output-limit behavior for stdout and
  stderr.

### Executable acceptance scenario

Implement `tests/integration/command_executor/test_first_command_wave.py` to execute:

```sh
mkdir -p /workspace/project
cd /workspace/project
echo "hello" > message.txt
cat message.txt
ls
rm message.txt
```

Run the scenario twice from identical workspace snapshots and assert identical command
results, output, state transitions, workspace hashes, and final workspace state.

### Exit criteria

- [x] **2.9.1** `python -m pytest tests/unit/command_executor -q` passes.
- [x] **2.9.2**
  `python -m pytest tests/integration/command_executor/test_first_command_wave.py -q`
  passes.
- [x] **2.9.3** Tests prove every first-wave command operates only through injected
  workspace ports.
- [x] **2.9.4** Every unsupported syntax family returns the documented typed error before
  mutation.
- [x] **2.9.5** Timeout and cancellation tests prove no later mutation occurs after the
  reported terminal result.
- [x] **2.9.6** `docs/components/command-executor/README.md` describes the
  implemented grammar, commands, state transitions, timeout, cancellation, and output
  limits.
- [x] **2.9.7** Tests prove command handlers receive explicit empty stdin while pipeline
  syntax remains unsupported by the MVP parser.

## 7. Milestone 3: minimal `SandboxSession` vertical slice

### Goal

Prove that workspace and command executor compose into the approved four-operation
agent-facing contract without exposing concrete core components.

### Work

#### 3.1 Session lifecycle and coordination

- [x] **3.1.1** Write tests for explicit `CREATED`, `RUNNING`, `CLOSING`, `CLOSED`, and
  `FAILED` lifecycle transitions, including no same-object restart.
- [x] **3.1.2** Implement session identity and explicit lifecycle-state enforcement.
- [x] **3.1.3** Write concurrency tests for close admission cutoff, queued-operation
  timeout/cancellation, active-operation completion, and exclusive restore.
- [x] **3.1.4** Serialize operations behind one cancellation-safe async coordination
  gate with an end-to-end deadline covering queue wait through required terminal event
  delivery. Requests default to 30 seconds and reserve one second for terminal delivery;
  non-positive collaborator budget fails before invocation.
- [x] **3.1.5** Define lifecycle event ordering and bounded, cancellation-resilient
  `start()`/`close()` behavior, including exactly-once resource-scope cleanup and
  `CLOSED` state after close-event, cleanup, or close-timeout failure. Waiting for the
  active operation uses that operation's deadline; close's lifecycle budget begins after
  gate acquisition.
- [x] **3.1.6** Add `src/mem_sandbox/session/` to the dependency-boundary guard and keep
  the session module free of agent-framework and infrastructure imports.

#### 3.2 Agent-facing operations

- [x] **3.2.1** Define stable session-owned domain requests and results for `execute`,
  `read_file`, `write_file`, and `apply_patch`, using explicit `SessionExecute*` names to
  avoid collision with command-executor contracts. Add shared `OperationKind` and
  `OperationLimits` data contracts under `mem_sandbox.core.operations`.
- [x] **3.2.2** Write behavior tests for each operation through `SandboxSession` using
  fake collaborators.
- [x] **3.2.3** Implement the four operations by orchestrating constructor-injected
  workspace and command-executor interfaces.
- [x] **3.2.4** Add host-only binary read, binary write, stat, and list operations without
  exposing them in the default agent tool profile.

#### 3.3 Explicit minimal collaborators

- [x] **3.3.1** Define the minimal `OperationKind`, policy request, decision, and
  effective-limit contracts, then implement an allow-all engine whose decision is
  explicit and observable.
- [x] **3.3.2** Implement a no-secret broker that rejects all secret resolution
  explicitly.
- [x] **3.3.3** Implement a required-delivery no-op event sink that explicitly accepts
  and discards events.
- [x] **3.3.4** Inject borrowed behavior collaborators plus one session-owned
  `SessionResourceScope`; do not use per-dependency ownership flags, `None` checks, or
  catch-and-ignore behavior.
- [x] **3.3.5** Define the minimal immutable Milestone 3 lifecycle, operation-start, and
  terminal event envelope with explicit operation kind; assign monotonic sequence
  numbers in the session and test required-delivery failures.

#### 3.4 Session snapshots

- [x] **3.4.1** Define deterministic session snapshot state for workspace, cwd, approved
  environment, compatibility metadata, and source-session provenance; exclude random
  snapshot identity and creation time from the state hash. Pin Milestone 3 session and
  capability compatibility versions to `1`.
- [x] **3.4.2** Implement the deterministic session snapshot codec, an in-memory snapshot
  store, and session snapshot creation. Verify the session payload hash before restore
  preparation and enforce the default 64 MiB session-payload bound.
- [x] **3.4.3** Implement exclusive session restore through a workspace-prepared
  immutable candidate that validates the required cwd before live state changes. Extend
  the workspace snapshot port and owning workspace README with prepare/commit behavior.
- [x] **3.4.4** Write tests for source-session provenance, opaque references, missing and
  incompatible snapshots, atomic restore failure, and successful round-trip. Application
  authorization remains outside the package boundary. Cover duplicate snapshot
  identifiers and explicit workspace-revision rewind across restore.

#### 3.5 Vertical-slice conformance

- [x] **3.5.1** Build one framework-free scenario that uses only the public
  `SandboxSession` interface.
- [x] **3.5.2** Assert files, cwd, approved environment, hashes, revisions, operation
  ordering, snapshot restoration, and stable errors.

The no-op collaborators are real implementations with explicit allow/no-secret/no-event
semantics. They are not `None` checks or silent exception swallowing.

### Executable acceptance scenario

Implement `tests/integration/session/test_minimal_vertical_slice.py` to:

1. Write a small project.
2. Read a bounded range.
3. Execute commands that inspect it.
4. Apply a hash-guarded patch.
5. Create a snapshot.
6. Mutate and restore.
7. Verify final files, cwd, environment, hashes, and operation ordering.

### Exit criteria

- [x] **3.9.1** `python -m pytest tests/unit/session -q` passes.
- [x] **3.9.2**
  `python -m pytest tests/integration/session/test_minimal_vertical_slice.py -q` passes.
- [x] **3.9.3** Public-session tests use no concrete workspace or executor types.
- [x] **3.9.4** Restore-concurrency tests prove that snapshot restore is exclusive,
  validates cwd before publish, and leaves all session state unchanged on failure.
- [x] **3.9.5** End-to-end timeout, cancellation, and close-cutoff tests prove no queued
  or later mutation leaks past the terminal result.
- [x] **3.9.6** The vertical slice imports and runs without an LLM or agent-framework
  dependency.
- [x] **3.9.7** `docs/components/sandbox-session/README.md` and
  `docs/components/snapshot-store/README.md` match the implemented behavior.
- [x] **3.9.8** Dependency-boundary tests cover `mem_sandbox.session` and prove the
  vertical slice has no agent-framework dependency.

## 8. Milestone 4: complete core behavior

### Goal

Complete the framework-neutral core after the vertical slice has proved its boundaries,
including the remaining command profile, events, snapshots, and process-local service
behavior. Composed policy and functional secrets are deferred until a concrete trust
boundary exists.

### Work

#### 4.1 Workspace and command completion

- [x] **4.1.1** Write tests and implement POSIX-shaped `head`, `tail`, `grep`, `find`,
  `wc`, `sort`, and `uniq`, including standard stdin/file operands, explicit `-`, common
  approved options, recursive grep, deterministic path rendering, and stable normal
  non-zero statuses.
- [x] **4.1.2** Write tests and implement POSIX-shaped `cp` and `mv` through existing
  workspace ports. Support multiple sources for an existing directory destination,
  compatible file overwrite, and recursive directory copy while explicitly rejecting
  directory-tree merge behavior the workspace cannot publish atomically.
- [x] **4.1.3** Write tests and implement `env`, `export`, `unset`, and constrained
  command expressions through the same parser and command registry. Also align touched
  first-wave commands with approved common behavior, including `cat` stdin, `echo -n`,
  and `ls -1`/`-a`/multiple paths. Do not add `sh`, script-file execution, multiline
  shell input, heredocs, or arbitrary code execution.
- [x] **4.1.4** Add native file-info or search APIs only when an approved adapter
  requirement cannot use the existing operations efficiently.
- [x] **4.1.5** Treat familiar POSIX/Bash behavior as the default for each supported
  command. Reject unimplemented behavior explicitly rather than adding host fallbacks,
  misleading output, or undocumented partial emulation. Document and evaluate every
  deliberate compatibility deviation.
- [x] **4.1.6** Write tests and implement the
  [approved pipeline semantics](./components/command-executor/README.md#approved-milestone-4-pipeline-semantics):
  parse higher-precedence `|` into immutable pipeline stages; admit only registered
  pipeline-safe non-mutating commands; execute stages sequentially through bounded UTF-8
  stdin and stdout; use the rightmost non-zero status; preserve stderr in stage order;
  permit descriptor-approved redirection only on the final stage; apply that redirection
  atomically for every normal result, including non-zero results; and return a structured
  non-zero pipeline-limit result rather than pass truncated intermediate data. Preserve
  `;` and `&&` behavior around that result. Add an explicit stdin-connection marker,
  stage-count limit, per-intermediate limit, and aggregate-materialization limit. Do not
  use host processes or concurrent stage execution.
- [x] **4.1.7** Run an executable multi-model command-usability evaluation before the
  first framework adapter, including acceptance, repair turns, tool calls, tokens,
  latency, output size, and truncation.

#### 4.2 Policy admission decision

- [x] **4.2.1** Retain the explicit Milestone 3 `SessionPolicyEngine` seam,
  `PolicyRequest`, `PolicyDecision`, and `AllowAllPolicyEngine`.
- [x] **4.2.2** Defer composed operation, path, command, argument, limit, obligation,
  secret-reference, and destination policy until a concrete authorization boundary
  exists. Issue #20 later introduced only reference-set admission before source access;
  composed secret or destination policy remains deferred.
- [x] **4.2.3** Document that workspace, command, lifecycle, deadline, and no-host-
  fallback guarantees remain authoritative invariants in their owning modules rather
  than configurable policy rules.
- [x] **4.2.4** Record the re-evaluation triggers and require a new approved design issue
  before enabling multi-owner authorization, secrets, network access, host execution, or
  shared persistent state.

#### 4.3 Event sink

- [x] **4.3.1** Define derived event identity, complete lifecycle/operation/snapshot
  categories, per-session sequence behavior, and explicitly classified immutable
  attributes.
- [x] **4.3.2** Write the approved
  [behavior-first event matrix](./components/event-sink/README.md#behavior-first-test-matrix)
  for canonical payload bounds, sensitivity admission, structural redaction, deterministic
  collection, delivery modes, snapshot ordering, timeout, cancellation, and failure
  behavior. Policy and secret events remain deferred with those features.
- [x] **4.3.3** Implement structural redaction and deterministic canonical payload
  validation before concrete sink invocation. Reject `SECRET` attributes, require
  explicit opt-in for `PROTECTED`, and keep default payloads free of file content, full
  command text, stdout/stderr, environment values, host paths, and secrets.
- [x] **4.3.4** Implement the process-local in-memory sink with exact count and aggregate
  payload-byte limits, atomic overflow rejection without eviction, per-session monotonic
  sequence validation, deterministic queries, and owner-controlled idempotent
  flush/close.
- [x] **4.3.5** Implement immutable per-session `REQUIRED` or `BEST_EFFORT` dispatch.
  Keep required delivery as the default. Require a synchronous diagnostic handler for
  best effort, use an owner-managed serial queue so failures do not alter session
  outcomes, and route handler failures to the asyncio loop exception handler.
- [x] **4.3.6** Add the session post-commit event hook and emit
  `snapshot.created`/`snapshot.restored` before `operation.completed`, using the terminal
  event budget and preserving already committed state on required delivery failure.

#### 4.4 Snapshot store completion

- [x] **4.4.1** Write the approved snapshot-store behavior matrix for creator provenance,
  count and byte limits, store-assigned absolute expiration, explicit purge,
  compatibility, integrity, and concurrent accounting.
- [x] **4.4.2** Introduce snapshot draft/persisted types, migrate the session snapshot
  store port and existing construction sites, and implement process-local count/byte
  limits plus store-assigned expiration.
- [x] **4.4.3** Implement explicit compatibility and integrity checks before restore
  mutation begins.

#### 4.5 Secret boundary

- [x] **4.5.1** Retain typed secret references and the explicit no-secret broker that
  rejects every lease request.
- [x] **4.5.2** Defer functional secret resolution, authorization, expiry, and redaction
  behavior with the composed-policy decision.
- [x] **4.5.3** Re-open secret design only for a concrete protected use and enforcement
  point. Issue #20 identifies execute-time reference access and lease lifetime, retains
  application-owned caller authorization, and explicitly defers command/destination
  binding.
- [x] **4.5.4** Implement the functional source, bounded broker, protected value,
  operation-scoped lease, exact expiry, active-count accounting, cleanup, and stable
  secret errors.
- [x] **4.5.5** Extend execute admission with immutable environment-to-reference
  bindings, policy-visible reference facts, an ephemeral command overlay, and protected
  output/persistence guards, including pre-dispatch rejection of generic protected
  command arguments that could expose derived facts through command semantics.
- [x] **4.5.6** Add the cross-module secret-canary matrix for workspace paths/content,
  session/snapshot state, results, errors, events, diagnostics, representations, traces,
  timeout, cancellation, and cleanup.

#### 4.6 Core conformance

- [x] **4.6.1** Implement the
  [public direct-core reference scenario](../tests/conformance/README.md) through
  `SandboxService` and `SandboxSession`: create, mutate, hash-guarded patch, stale
  rejection, snapshot, same-identity in-place restore, close, resume, continue,
  repeated-resume forks, and cleanup. Normalize unstable identifiers, timestamps, and
  durations while preserving identity relationships, revisions, hashes, errors, event
  ordering, and provenance.
- [x] **4.6.2** Cover direct public-operation behavior by category: happy path,
  policy/safety denial, dependency failure, exact and one-over boundary,
  timeout/cancellation, deterministic replay, and unsupported behavior before mutation
  with no host fallback.
- [x] **4.6.3** Add Hypothesis 6.x to the development dependency group, register bounded
  local/CI/extended profiles, use deterministic CI generation on every supported
  Python/OS job, and retain shrunk counterexamples as focused regression tests.
- [x] **4.6.4** Implement the
  [generated path and workspace models](../tests/property/README.md) for confinement,
  UTF-8 limits, quota accounting, revisions, hashes, mutation atomicity, copy/move, and
  deterministic workspace export.
- [x] **4.6.5** Implement generated workspace-snapshot, snapshot-store, and pure session
  codec properties for canonical round trip, restore isolation, integrity,
  compatibility, expiry, purge, count, and byte-capacity behavior.
- [x] **4.6.6** Implement generated command grammar, pipeline, redirection, limit, and
  twin-execution models without private parser tokens, host shell comparison, custom
  randomness, timing generation, or unbounded operation sequences.
- [x] **4.6.7** Update every affected component `README.md` in the same change as its
  generated or conformance behavior and keep the direct trace reusable by later adapter
  drivers.

#### 4.7 Sandbox service and session factory

- [x] **4.7.1** Write the approved service behavior matrix for provenance-preserving
  create/resume, lookup, delete, factory cleanup, independent forks, and concurrent
  registry behavior. Application authorization and automatic live-session expiration
  remain out of core.
- [x] **4.7.2** Implement the process-local `SandboxService`, opaque handles,
  owner provenance, service shutdown, and exactly-once registry cleanup.
- [x] **4.7.3** Implement the service-owned session-factory, snapshot-gateway, decoder,
  and runtime ports. Assemble borrowed shared collaborators, the session resource scope,
  and a post-session dispatcher scope in the required close order.
- [x] **4.7.4** Preserve creator and source-session provenance without treating either as
  authorization. Document that applications own principal-to-handle and
  principal-to-snapshot-reference access control.

### Exit criteria

- [x] **4.9.1** `python -m pytest tests/unit tests/integration -q` passes.
- [x] **4.9.2** `python -m pytest tests/conformance/test_direct_session.py -q` passes.
- [x] **4.9.3** `python -m ruff check evaluations src tests` and
  `python -m pyright evaluations src tests` pass.
- [x] **4.9.4** The minimal explicit admission seam remains covered and no current
  release behavior depends on a speculative composed policy engine.
- [x] **4.9.5** Functional secret materialization follows the issue #20 approved
  policy-before-source, operation-local overlay, lease cleanup, persistence guard, and
  canary-redaction contract.
- [x] **4.9.6** Unsupported behavior tests prove failure occurs before mutation and
  without host fallback.
- [x] **4.9.7** The dependency-boundary test still proves no agent-framework dependency
  is present in core.
- [x] **4.9.8** Pipeline tests prove precedence, pipeline-safe producer/consumer
  admission, fixed pipefail status, final-only stdout, ordered stderr,
  descriptor-gated final-stage-only redirection, structured limit-failure sequencing,
  and exact intermediate, aggregate, timeout, and cancellation boundaries.
- [x] **4.9.9** `HYPOTHESIS_PROFILE=ci python -m pytest tests/property -q` passes with
  bounded deterministic generation and no host-resource access.
- [x] **4.9.10** The direct conformance trace and dedicated secret-canary suite together
  prove snapshot state/root-hash relationships, same-identity in-place restore, resume
  identity, fork isolation, event correlation, policy-before-collaborator ordering,
  secret non-persistence, and complete cleanup through public contracts only.

## 9. Milestone 5: native integrations

### Goal

Deliver the first native integration through the OpenAI Agents SDK while preserving core
ownership of filesystem, command, lifecycle, snapshot, policy, secret, and event behavior.
Prove both integration levels through one SDK before extracting cross-framework helpers or
committing to additional framework adapters.

### Work

#### 5.1 OpenAI contract and package boundary

- [x] **5.1.1** Support `openai-agents>=0.22,<0.23`, test exactly `0.22.0`, and verify
  `BaseSandboxClientOptions`, `SandboxSessionState`,
  `BaseSandboxClient`, `BaseSandboxSession`, `Capability`, `SandboxRunConfig`, and
  `SnapshotBase` against that version.
- [x] **5.1.2** Add `mem_sandbox.integrations.openai_agents` behind an optional
  integration extra and prove importing or constructing the core does not import or
  require `openai-agents`. Add a module `README.md` recording boundary ownership and
  maintenance rules.
- [x] **5.1.3** Record the beta compatibility policy: fail contract tests on changed
  abstract methods, state fields, lifecycle ordering, serialization, capability binding,
  or snapshot semantics; support only the documented pinned range.
- [x] **5.1.4** Audit the implemented public core against the pinned contract. Reuse the
  existing binary read/write, stat/list, lifecycle, and snapshot behavior; add only the
  smallest owning-module ports needed for native directory mutation or portable workspace
  persistence. Do not implement filesystem or archive semantics inside the adapter.

#### 5.2 OpenAI Agents SDK sandbox client and session

Issues #31 and #32 added the required public directory and portable archive seams. Issue
#33 delivered the initial provider types, SDK wrapper, session operations, manifest
profile, archive bridge, live reattachment, and snapshot-backed replacement path. Issue
#34 completes lifecycle ownership and state/resume hardening rather than reimplementing
those foundations.

- [x] **5.2.1** Implement immutable client options and registered, JSON-serializable
  session state containing only the process-local handle hint, expected core session
  identity, explicit provider-state schema version, provider configuration, SDK
  manifest/snapshot models, and portable archive metadata. Exclude live services,
  sessions, collaborator objects, resolved secrets, host credentials, and accepted host
  paths. Reject unknown or unsupported provider-state fields before any service call.
- [x] **5.2.2** Complete the implemented sandbox client/session lifecycle over one
  injected `SandboxService` and exactly one service handle per provider session. Cover
  create, running, repeatable `stop()`/`aclose()`, idempotent delete, state
  serialization, safe live reattachment, snapshot-backed replacement resume, ownership
  transfer, failure cleanup, and concurrent lifecycle calls.
- [x] **5.2.3** Define and implement a versioned manifest support matrix. Accept only
  synthetic files, directories, and configuration that map losslessly to public core
  behavior. Reject non-empty manifest environment, users, groups, host/local paths,
  mounts, ports, PTY, Git entries, symlinks, devices, and executable hooks before core
  allocation or mutation unless separately implemented and approved.
- [x] **5.2.4** Implement complete binary stream translation and the portable workspace
  persistence/hydration bridge with bounded input, decompressed-size, entry-count, and
  path validation owned by the appropriate core boundary.
- [x] **5.2.5** Override inherited POSIX-assuming behavior required by the supported
  profile, including native path validation and directory operations. Disable or replace
  shell-based fingerprinting and never fall back to `sh`, a host process, or the host
  filesystem.
- [x] **5.2.6** Complete the remaining lifecycle and state conformance tests. Issue #33
  already covers the pinned SDK contract, wrapper delegation, binary round-trip,
  manifest atomicity, snapshot persistence/hydration, basic live and replacement resume,
  unsupported features, cancellation, timeout, and no-host fallback. Issue #34 adds
  corrupt/incompatible state, startup and resume cleanup, repeated-resume forks,
  repeatable SDK cleanup, idempotent backend deletion, primary-versus-cleanup failure
  precedence, and concurrent client lifecycle coverage.

##### Issue #34 lifecycle ownership contract

- The host owns the injected `SandboxService` lifetime; the adapter client must not close
  that shared service implicitly. Each returned provider session owns one opaque service
  handle and one public core `SandboxSession`.
- Every `create()` and `resume()` result remains the SDK instrumentation wrapper. A newly
  allocated handle is transferred to the returned provider session only after adapter
  construction succeeds.
- SDK `stop()` is persistence-only. SDK `aclose()` performs the SDK stop/shutdown and
  dependency lifecycle but does not delete the service handle. `client.delete()` is the
  authoritative backend-release operation and must be safe after prior or concurrent
  deletion.
- A handle allocated for create or replacement resume must be deleted if adapter
  construction or startup fails. Cleanup failure must be attached as secondary context
  without replacing the primary construction, startup, persistence, or resume failure.
  Failed startup also closes the session-scoped SDK dependency clone and its owned
  resources. A failed live-reattachment start preserves the pre-existing handle because
  that attempt allocated no new backend resource.
- Live reattachment is attempted only after complete state and manifest validation.
  A resolved handle is reusable only when the returned public `SandboxSession.session_id`
  matches the expected core session identity serialized with the handle. A mismatch is
  treated as an unavailable original session: do not attach to or delete the unrelated
  session, and use snapshot-backed replacement. `SandboxNotFound` selects the same
  replacement path; all other lookup failures propagate without creating a divergent
  workspace. The identity pair is a consistency check, not an authorization credential.
- Resume must not mutate the caller's state object. Concurrent or repeated resumes from
  the same immutable serialized payload create independent replacement sessions when the
  original process-local handle is unavailable; a still-live handle intentionally
  reattaches to the same core session.
- Replacement resume requires a restorable snapshot. If the original handle is absent or
  mismatched and the configured snapshot is unavailable, fail before allocation rather
  than returning an empty manifest-based workspace as a successful resume. Until
  replacement `start()` successfully hydrates that snapshot, `stop()`/`aclose()` must
  preserve the original durable snapshot and metadata rather than publishing the
  manifest-only replacement workspace.
- Multiple live resumes are wrapper aliases over the same service-owned core session,
  not forks. Core operation serialization remains authoritative, and deletion through
  any alias removes the shared handle so later operations through every alias fail.
- MemSandbox's `FactorySnapshotStore` remains the persistence extension point. The
  OpenAI integration provides a serializable `SnapshotBase` bridge that resolves the
  live store through SDK `Dependencies` and stores the portable workspace archive under
  a distinct format/schema. The bridge serializes only its type, snapshot identifier,
  dependency key, and owner provenance; it never serializes the live store or clock.
  Unchanged persistence reuses the current snapshot, while changed workspaces retain
  immutable historical snapshots until store expiration or quota policy removes them.
  `NoopSnapshot` remains valid when recovery is not required; explicitly supplied
  third-party SDK snapshot providers remain caller-owned extensions, never implicit
  adapter fallbacks.
- Concurrent create, resume, stop/`aclose()`, and delete paths must converge on one
  authoritative ownership outcome without leaked handles, duplicate cleanup, or
  success-shaped recovery from a failed operation. Snapshot preflight uses a temporary
  dependency clone and closes it so caller-owned SDK dependency factories cannot leak
  resources into the client template.

#### 5.3 OpenAI sandbox capability

- [x] **5.3.1** Implement an adapter-owned OpenAI `Capability` exposing exactly
  `execute`, `read_file`, `write_file`, and `apply_patch` through the bound in-memory
  sandbox session.
- [x] **5.3.2** Map existing domain requests, results, correctable errors, terminal
  denials, dependency failures, timeouts, and cancellation without adding a second
  framework-neutral schema layer.
- [x] **5.3.3** Replace the default OpenAI shell/filesystem capability set for the first
  profile. Do not advertise `sh -lc`, PTY, arbitrary shell, image viewing, or another SDK
  feature until its complete semantics pass explicit conformance.
- [x] **5.3.4** Run the shared model-free conformance scenario through the capability and
  assert the same normalized domain outcomes, revisions, file hashes, snapshot root
  hashes, restored state, fork isolation, and cleanup as direct `SandboxSession`.
- [x] **5.3.5** Add one deterministic runner integration test proving capability
  cloning/binding, trusted session selection, tool registration, safe output/error
  translation, and cleanup without a live model or provider network call.

#### 5.4 Additional agent SDKs

- [x] **5.4.1** Keep PydanticAI, LangChain Deep Agents, Microsoft Agent Framework,
  Google ADK, and other SDK adapters outside the Milestone 5 completion gate.
- [x] **5.4.2** With the OpenAI conformance and product-validation evidence from 5.6
  complete, review actual duplication and missing semantics before selecting the next
  SDK. The issue #45 live sample is not required for this structural review.
- [x] **5.4.3** No additional SDK adapter is selected in Milestone 5. Each future
  adapter requires a separate approved design/implementation issue. Extract shared
  helpers only from behavior proven identical by at least two adapters; do not create a
  global framework abstraction in anticipation of future SDKs.

The issue #37 review found no framework-neutral adapter behavior to extract. The current
OpenAI package contains SDK-owned state, lifecycle, manifest, snapshot, capability, and
error translation. Reusable filesystem, command, lifecycle, archive, policy, secret, and
event behavior already resides behind public core interfaces. Product-validation
normalization remains test/benchmark infrastructure rather than a runtime adapter
abstraction. No second SDK is selected by Milestone 5.

#### 5.5 Deferred integrations

- [x] **5.5.1** Keep MCP, HTTP/OpenAPI, A2A, local subprocesses, Docker, and hosted
  sandbox providers outside this implementation phase. OpenAI's provider interface is in
  scope only for the process-local MemSandbox client.
- [x] **5.5.2** No deferred integration is added. Each future integration requires a new
  approved design decision and support matrix before implementation.

The production package has no imports, dependencies, providers, clients, or transport
abstractions for those deferred integrations. `openai-agents` remains the only optional
framework dependency, isolated to `mem_sandbox.integrations.openai_agents`. Future
integration work must start from a concrete SDK contract and its own issue rather than
generalizing the OpenAI adapter speculatively.

#### 5.6 Product validation and benchmark baseline

- [x] **5.6.1** Implement the model-free
  [stateful execution reference scenario](./product-validation/README.md#stateful-execution-conformance)
  through a product-validation driver owned outside framework adapters.
- [x] **5.6.2** Run the same normalized create, mutate, snapshot, close, resume,
  continue, fork, and delete scenario through the direct session, OpenAI sandbox
  client/session driver, and OpenAI capability driver.
- [x] **5.6.3** Implement versioned cold, warm, create-to-ready, first-operation,
  snapshot, resume, burst, memory, and adapter-overhead benchmark cases without model or
  provider network calls.
- [x] **5.6.4** Emit versioned machine-readable result artifacts containing raw samples,
  environment fingerprints, workload dimensions, failures, statistics, and correctness
  checksums.
- [x] **5.6.5** Capture the first short-duration, non-gating reference measurement with
  raw samples and environment metadata. Defer controlled regression budgets until the
  project introduces an automated performance gate.
- [x] **5.6.6** Keep comparative benchmarks separate from CI regression gates and require
  the documented methodology before publishing any "fastest provisioning" claim.

#### 5.7 Trust-boundary re-evaluation

- [x] **5.7.1** Review the implemented OpenAI boundary for concrete authority introduced
  by caller-to-handle mapping, serialized session state, manifests, environment values,
  path grants, snapshots, or future mount/network options.
- [x] **5.7.2** The supported profile introduces no new protected action beyond the
  implemented core policy and secret contracts, keep application-owned authorization and
  continue deferring composed policy.
- [x] **5.7.3** No current re-evaluation trigger exists. If a future trigger appears,
  open a new design issue that defines authority, identity, protected actions,
  enforcement ownership, prepared artifacts, fail-closed behavior, and behavior-first
  coverage before implementation.

##### Issue #37 trust-boundary decision

The OpenAI adapter is a process-local translation boundary, not an authentication or
multi-tenant authorization boundary.

| Surface | Current authority and enforcement | Decision |
|---|---|---|
| Caller-to-handle mapping | The application constructs the client, selects `owner_id`, and controls serialized state. The service accepts an opaque process-local handle; live reattachment additionally requires the serialized core session identity to match. | Handle/session matching prevents accidental collision but is not authorization. Applications must authorize access before passing state to the adapter. |
| Serialized provider state | Pydantic validation rejects unknown fields, malformed identities, invalid execution context, incomplete archive metadata, and unsupported profile versions. State contains no live service, session, credential, secret, or host-path object. Model-issued virtual `cd`/`export` changes may influence persisted cwd/environment and are revalidated before atomic replacement restore. | Treat serialized state and snapshot identifiers as application-controlled bearer data even though part of the execution context is model-influenced. They are never model tool inputs. |
| Manifest and environment | The complete manifest profile is validated before allocation or mutation. Only bounded synthetic `File` and `Dir` entries are accepted; every other entry type and unsupported manifest field fails closed. Environment, users, groups, host path grants, custom mount behavior, PTY, and ports are unsupported. | Synthetic files/directories add no authority. Session environment changes remain virtual; secret overlays stay operation-scoped and non-persistent. |
| Model-facing capability | The SDK clones and binds the capability to a host-selected live provider session. The four tool schemas contain no handle, session selector, lifecycle, snapshot, policy, secret, shell, PTY, mount, port, or network field. Execute timeout and output inputs may only narrow fixed 30-second and 256-KiB profile ceilings; policy may further narrow timeout. | The model can request only bounded core operations on the already-bound session. A returned session ID is correlation metadata, not a service lookup capability. |
| Snapshots | The application supplies the snapshot store/dependencies and controls serialized state. The adapter checks the application-asserted owner tag together with format, schema, size, payload hash, revision, and root hash before restore. | Owner fields detect inconsistent state but are not independently authenticated principals. Shared or cross-tenant stores remain a future authorization trigger. |
| Future mounts, network, host process, and shared persistence | Every such OpenAI SDK surface is currently rejected before host access or core mutation. | Enabling any surface introduces new resources or destinations and requires a separate behavior-first trust design. |

No composed policy engine is justified for the current single-application,
process-local profile. Workspace confinement, quotas, command admission, lifecycle,
snapshot validation, explicit policy decisions, and secret leasing remain enforced by
their owning modules. Re-evaluation is mandatory before multi-owner authorization,
shared persistent snapshots/workspaces, egress, arbitrary execution, external approval
obligations, or per-tenant capability profiles.

#### 5.8 Azure OpenAI agent sample

- [x] **5.8.1** Add the issue #45 sample using an Azure OpenAI model with the pinned
  OpenAI Agents SDK, `SandboxAgent`, and `InMemorySandboxCapability`.
- [x] **5.8.2** Keep live Azure execution opt-in while required CI uses a deterministic
  model double with no credentials or network calls.
- [x] **5.8.3** Prove host-selected session binding, bounded stateful workspace mutation,
  host-side result verification, and explicit cleanup without host filesystem, shell,
  process, mount, port, or lifecycle authority in model inputs.
- [x] **5.8.4** Document endpoint/authentication configuration, tracing behavior,
  supported command semantics, resource ownership, expected output, limitations, and
  troubleshooting.

Issue #45 uses a sample-local composition root rather than adding a production service
builder. The runnable path requires explicit `AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, and `AZURE_OPENAI_DEPLOYMENT`
environment variables and supports public `*.openai.azure.com` and
`*.cognitiveservices.azure.com` resource endpoints.
The application constructs `AsyncAzureOpenAI` plus `OpenAIChatCompletionsModel`; the
sample runner depends only on the SDK `Model` interface and an injected `SandboxService`
so required tests use a deterministic model double.

The bounded task creates `/workspace/demo/report.txt`, reads its content hash, applies a
guarded patch from `status=pending` to `status=complete`, and reads the result. Host code
then verifies the exact final bytes through the SDK session. Tracing is disabled by
default, live execution is opt-in and billable, and all SDK session/backend,
MemSandbox-service, and Azure-client resources are explicitly released on success,
failure, or cancellation.

The endpoint validation also accepts public `*.cognitiveservices.azure.com` Azure OpenAI
resource endpoints. After verification, the live entry point attaches a constrained
interactive command loop to the same host-selected session so a user can inspect or
temporarily modify the in-memory state before cleanup; the loop never invokes a host
shell.

The sample is organized as a shared scenario runner plus registered task definitions.
`--inspect` enters the CLI after successful verification, while `--inspect-on-failure`
enters before cleanup when a scenario fails. Registered scenarios cover workspace
editing, incident triage, atomic configuration migration, deterministic data pipelines,
policy and quota recovery, multi-agent handoff, and host-owned snapshot branching.

Issue #7 moved SDK-independent inspection and service composition to `samples/shared`.
Its credential-free module entry point creates an empty in-memory session so readers can
try the constrained CLI before configuring a model provider.
OpenAI Agents SDK application lifecycle, runner behavior, and SDK-shaped scenarios live
under `samples/openai_agents_sdk`. Nested `providers/azure_openai` and
`providers/openai` entry points construct the Azure Chat Completions and official OpenAI
Responses models respectively. Both providers share arguments, verification,
inspection, and cleanup behavior. Required tests construct clients without sending
requests and execute scenarios through a deterministic SDK `Model`, so no inference
endpoint or credentials are required in CI.

### Exit criteria

- [x] **5.9.1** `python -m pytest tests/conformance -q` passes for the direct session,
  OpenAI sandbox client/session, and OpenAI capability drivers.
- [x] **5.9.2** The OpenAI capability produces the same normalized domain outcomes,
  revisions, file hashes, snapshot root hashes, restored state, fork behavior, and
  cleanup as the direct-session reference scenario.
- [x] **5.9.3** The OpenAI sandbox client/session passes pinned SDK contract, lifecycle,
  binary stream, manifest atomicity, state serialization, snapshot round-trip,
  resume/fork, unsupported-feature, timeout/cancellation, and no-host-fallback tests.
- [x] **5.9.4** Adapter dependency tests prove framework packages are isolated from core.
- [x] **5.9.5** The OpenAI integration `README.md` records its supported SDK range,
  exact tested version, beta compatibility policy, manifest/capability support matrix,
  ownership model, unsupported behavior, and conformance results.
- [x] **5.9.6** The stateful reference scenario produces equivalent normalized outcomes,
  file hashes, revisions, snapshot root hashes, lifecycle rejection, restored state,
  fork behavior, and cleanup through the direct and both OpenAI drivers.
- [x] **5.9.7** The benchmark suite reports cold and warm provisioning, first operation,
  snapshot, resume, burst, memory, backend-adapter overhead, and capability-adapter
  overhead for the direct and OpenAI drivers with exact workload and environment metadata.
- [x] **5.9.8** A committed short-duration reference artifact reports direct/OpenAI
  metrics and is explicitly non-gating. Any future release-gating budget requires a
  controlled runner, measured noise floor, and documented limitation for noisy cases.
- [x] **5.9.9** Product documentation uses only performance claims supported by current
  artifacts; an unqualified "fastest sandbox" claim is prohibited.
- [x] **5.9.10** PydanticAI, Deep Agents, MCP, and other deferred integrations are not
  required for Milestone 5 completion, and no production abstraction exists solely for a
  deferred SDK.
- [x] **5.9.11** The Azure OpenAI sample in issue #45 completes one documented stateful
  task through the four-tool capability, verifies the final in-memory workspace from the
  host, keeps live provider calls outside required CI, and documents secure configuration
  and cleanup.

Milestone 5 is complete at merge commit
`6303be1bfea09b35db0d1b5728398738f6ced5f8`. The child-issue reconciliation,
cross-platform CI matrix, benchmark reference, trust-boundary outcome, and final scope
are recorded on tracking issue
[#7](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/7).

## 10. Milestone 6: workspace scalability decision

**Prerequisite:** Milestone 5 is complete.

This milestone is a post-release decision checkpoint, not a commitment to implement
content offload.

### Goal

Revisit [workspace content offload](./components/workspace/content-offload/README.md)
using representative workload data and measured performance while preserving the product
priority of fastest provisioning.

### Non-goals

- Implementing a host-filesystem workspace or projecting virtual paths onto host paths.
- Assuming that externalizing file bytes solves metadata, tree-copy, hash, or snapshot
  costs.
- Persisting live sessions or adding remote workers.
- Selecting a storage provider before the workload, retention, and ownership
  requirements are known.
- Starting implementation merely because a deferred design already exists.

### Work

#### 6.1 Evidence and baseline

- [x] **6.1.1** Extend the Milestone 5 product-validation baseline only where additional
  workspace-size profiles or mutation measurements are needed for the scalability
  decision.
- [x] **6.1.2** Record representative workload distributions for logical workspace
  bytes, individual file bytes, node count, session lifetime, snapshot frequency, and
  file access patterns.
- [x] **6.1.3** Determine whether the primary scaling constraint is resident file bytes,
  tree copying, hash recomputation, metadata volume, snapshot encoding, or another
  measured cause.

#### 6.2 Design review

- [x] **6.2.1** Reviewed the deferred design against workspace, session, command, event,
  snapshot-store, service, and policy boundaries; internal tree work remains
  workspace-owned.
- [x] **6.2.2** Compared quotas, process density, bulk seeding, structural sharing,
  cached subtree hashes, incremental accounting, and content offload in the
  [workspace scalability decision](./components/workspace/scalability-decision.md).
- [x] **6.2.3** Deferred content offload and approved separately scoped internal phase
  instrumentation, single-pass seeding, and combined immutable path-copying tree design
  as the preferred follow-up direction.
- [x] **6.2.4** Kept immutable file-content placement, durable snapshot/session metadata,
  host filesystem projection, and remote execution explicit and independently
  unapproved.

#### 6.3 Follow-up planning

- [x] **6.3.1** Not applicable: no content-offload implementation is approved; future
  internal or offload work requires a separate milestone or issue with explicit
  performance, memory, correctness, and conformance criteria.
- [x] **6.3.2** Not applicable: no prototype or provider is approved. The conditional
  content-port and provider-linked snapshot design remains reference material only.
- [x] **6.3.3** Recorded measurable capacity triggers, provider-readiness requirements,
  and the next review point in the
  [decision](./components/workspace/scalability-decision.md#content-offload-reconsideration).

### Exit criteria

- [x] **6.9.1** Baseline measurements and representative workload assumptions are
  documented.
- [x] **6.9.2** The content-offload decision and rationale are recorded in the workspace
  documentation.
- [x] **6.9.3** No content-offload implementation starts without a separately approved
  milestone based on the review evidence.

## 11. Milestone 7: external capability foundations

**Prerequisite:** Milestone 6 has recorded its scalability decision. Content offload is
deferred; any future reconsideration and implementation remains independently scoped.

### Goal

Define the shared authority, extension, accounting, and host-workspace exchange
foundations required before networking or arbitrary external execution is enabled.
Prove those foundations first with host-controlled repository import and result export,
which do not grant a model host Git, host filesystem, or network access.

### Non-goals

- Implementing a generic policy language or one god interface for every resource.
- Allowing models to install, discover, configure, or enable extension packages.
- Treating third-party in-process extension code as untrusted.
- Enabling network access, host processes, or arbitrary Python in this milestone.
- Turning repository import into an agent-controlled clone operation.
- Introducing tenant identity by treating the current provenance-only `OwnerId` as an
  authenticated principal.

### Work

#### 7.1 Security profiles and extension composition

- [ ] **7.1.1** Define host-selected `virtual`, `connected`,
  `trusted-host-execution`, and `isolated-execution` profiles with accurate security
  claims. The current network-free and process-free virtual profile remains the default.
- [ ] **7.1.2** Define immutable capability grants selected during create or resume.
  Snapshot state does not carry authority, and model requests may only narrow a grant.
- [ ] **7.1.3** Extend trusted command/profile composition through constructor injection.
  Do not add runtime package installation, implicit entry-point discovery, a service
  locator, or host-shell fallback.
- [ ] **7.1.4** Let extension descriptors declare their required resource kinds and
  bounded model-facing description. Registry construction rejects missing grants,
  duplicate names, incompatible versions, and unsupported profile combinations.
- [ ] **7.1.5** Define extension compatibility and conformance metadata without placing
  agent-framework or provider types in core.

#### 7.2 Concrete authority and policy re-evaluation

- [ ] **7.2.1** Re-evaluate the minimal session policy seam using the now-concrete
  protected actions: repository exchange, network requests, credential attachment, and
  external execution.
- [ ] **7.2.2** Keep invariant enforcement in its owning component. Add focused
  resource-specific policy contracts rather than moving URL parsing, runtime isolation,
  workspace validation, or quota logic into a general policy engine.
- [ ] **7.2.3** Require immutable prepared artifacts owned by the parser or resource
  boundary whenever admission needs parsed facts. Policy must not duplicate command,
  URL, archive, or Python parsing.
- [ ] **7.2.4** Define fail-closed behavior for missing facts, evaluator failure, invalid
  configuration, unsupported obligations, and required-audit failure before a protected
  collaborator is invoked.
- [ ] **7.2.5** Keep caller authentication and principal-to-handle authorization in the
  application. Introduce authenticated principal and tenant contracts only if a later
  remote or multi-tenant profile requires them.

#### 7.3 Unified resource accounting

- [ ] **7.3.1** Define typed accounting scopes for one operation and one session, with
  later service or tenant scopes able to narrow them without changing component-owned
  hard limits.
- [ ] **7.3.2** Define distinct accounting modes for retained capacity, cumulative
  consumption, active concurrency leases, rate windows, and observed peaks. Do not
  convert unrelated resources into one undocumented scalar credit.
- [ ] **7.3.3** Cover logical and resident workspace bytes, materialized bytes, snapshot
  bytes, content-provider operations and bytes, repository transfer, HTTP requests and
  transferred/decompressed bytes, execution wall and CPU time, memory, process count,
  output, scratch space, secret leases, and active concurrency.
- [ ] **7.3.4** Define atomic reservation or lease acquisition appropriate to each
  resource mode before protected or externally billable work, exact-once settlement of
  measured usage, and exact-once release of unused capacity on every success, denial,
  failure, timeout, cancellation, and cleanup path.
- [ ] **7.3.5** Keep hard component limits authoritative. Accounting may deny or narrow
  work but cannot increase workspace, command, provider, network, or backend limits.
- [ ] **7.3.6** Represent unavailable backend measurements explicitly rather than as
  zero. A profile cannot claim enforcement for a dimension its backend cannot measure or
  bound.
- [ ] **7.3.7** Define parent-child attribution so a network request inside external
  execution is visible in both contexts without double-charging the same session,
  service, or tenant budget.
- [ ] **7.3.8** Define resume semantics: a resumed session receives a new session ledger
  and current host grant, while any application, owner, or tenant ledger persists only
  in its external authority boundary, never through snapshot state.
- [ ] **7.3.9** Add bounded usage, reservation, settlement, and denial events without
  exposing content, host paths, credentials, or provider-specific exceptions.

#### 7.4 Host-controlled repository ingestion and export

- [ ] **7.4.1** Define a host-only import request that accepts a bounded content tree or
  portable workspace archive, not an arbitrary host path or model-supplied repository
  URL.
- [ ] **7.4.2** Reuse workspace-owned archive preparation and atomic restore. Reject path
  escapes, duplicate normalized paths, symbolic or hard links, devices, sockets,
  unsupported metadata, invalid names, repository control directories such as `.git`,
  and quota overflow before publication.
- [ ] **7.4.3** Define result export as a complete bounded archive, selected artifact
  set, or revision/root-hash-bound diff. Export never invokes host Git or chooses a host
  destination.
- [ ] **7.4.4** Record bounded source and result provenance without serializing host
  checkout paths, repository credentials, remote URLs containing secrets, or Git
  configuration.
- [ ] **7.4.5** Keep clone, fetch, push, credential helpers, and remote mutation outside
  this host-only boundary. Revisit a typed VCS capability after controlled egress and
  destination-bound secrets are implemented.

#### 7.5 Conformance and documentation

- [ ] **7.5.1** Add fake capability, policy, accounting, and repository-transfer
  collaborators so required tests remain deterministic and network-free.
- [ ] **7.5.2** Test default denial, grant narrowing, reservation races, cumulative
  exhaustion, exact-once settlement, import atomicity, export integrity, cancellation,
  required-event failure, and cleanup.
- [ ] **7.5.3** Generate capability descriptions from the configured profile and prove
  that snapshots, adapters, or model inputs cannot silently widen authority.

### Exit criteria

- [ ] **7.9.1** The default virtual profile has exactly its existing host-free authority.
- [ ] **7.9.2** Optional capabilities are immutable, host-selected, accurately described,
  and composed without framework dependencies or dynamic model-controlled loading.
- [ ] **7.9.3** Resource reservation and settlement are typed, cumulative, atomic, and
  proven for every terminal path.
- [ ] **7.9.4** Host-controlled repository import and export preserve workspace
  containment, quotas, hashes, revisions, and atomicity without granting host Git,
  filesystem, or network authority.
- [ ] **7.9.5** Policy re-evaluation records which decisions belong to the session and
  which remain authoritative at each resource boundary.

## 12. Milestone 8: controlled outbound HTTP

**Prerequisite:** Milestone 7 capability grants, focused policy decisions, resource
accounting, and audit facts are complete.

Detailed design:
[Controlled Network Egress](./components/network-egress/README.md).

### Goal

Add a default-deny outbound HTTP boundary shared by trusted virtual-command and typed-tool
adapters, with destination-aware secrets, SSRF defenses, cumulative budgets, and bounded
audit behavior.

### Non-goals

- A transparent socket API, unrestricted internet profile, or host network inheritance.
- Running a host `curl` process or shell.
- Raw TCP, UDP, WebSockets, listeners, tunnels, inbound ports, or arbitrary protocols.
- Browser automation, persistent cookies, model-controlled proxies or TLS roots, or
  unrestricted file downloads.
- Claiming library-level controls can contain arbitrary guest code.

### Work

#### 8.1 Gateway and profile

- [ ] **8.1.1** Define an async framework-neutral `OutboundHttpGateway` using immutable
  request, response, limits, usage, context, and stable error models.
- [ ] **8.1.2** Keep networking absent when no connected profile is configured. A resumed
  snapshot receives only the current host-selected equal or narrower grant.
- [ ] **8.1.3** Support bounded HTTP and HTTPS first with `GET` and `HEAD` as the baseline
  method grant and no ambient proxy, credential, cookie, cache, or host-client
  configuration. State-changing methods require explicit host policy.
- [ ] **8.1.4** Implement a fake gateway and conformance driver before one real bounded
  transport.

#### 8.2 Destination policy and SSRF controls

- [ ] **8.2.1** Canonicalize scheme, hostname, effective port, and URL structure in the
  network boundary. Reject URL user information and unsupported forms.
- [ ] **8.2.2** Apply fail-closed scheme, hostname, port, method, and request-class
  policy before DNS so denied names cannot become a resolver-based exfiltration channel.
- [ ] **8.2.3** Resolve through a controlled CNAME policy and classify every final IPv4
  and IPv6 result. Block loopback, private, link-local, multicast, reserved, unspecified,
  and cloud metadata destinations by default.
- [ ] **8.2.4** Bind connections to admitted resolution results while preserving normal
  TLS hostname validation. Define and test DNS-rebinding behavior.
- [ ] **8.2.5** Re-run normalization, admission, resolution, resource reservation, and
  credential selection for every redirect. Define exact redirect method and credential
  forwarding behavior.
- [ ] **8.2.6** Enforce host-controlled schemes, destinations, ports, methods, header
  classes, TLS, and proxy behavior. Missing facts or unsupported obligations deny before
  transport access.
- [ ] **8.2.7** Perform no implicit transport retry. Admit, reserve, credential, and
  audit every future retry independently, and require an approved idempotency contract
  before retrying a state-changing method.

#### 8.3 Secrets, accounting, and events

- [ ] **8.3.1** Add host-owned credential routes that select approved secret references
  only after destination admission. The model never supplies raw values or arbitrary
  references.
- [ ] **8.3.2** Bind each credential lease to one operation and approved origin. Remove
  or recompute sensitive headers across redirects and close every lease under the
  operation deadline.
- [ ] **8.3.3** Reserve and settle request attempts, body bytes, wire-response bytes,
  decompressed bytes, redirects, concurrency, duration, credential leases, and
  session-wide transfer.
- [ ] **8.3.4** Add bounded child resource events for outcome, destination class, method,
  status, redirects, bytes, duration, budget, and credential-route identifier. Exclude
  full URLs, queries, headers, bodies, raw errors, and secret values by default.
- [ ] **8.3.5** Extend secret canaries through request construction, transport,
  redirection, output, workspace writes, snapshots, errors, events, diagnostics, and
  object representations.
- [ ] **8.3.6** Accept a required request-start event before transport. If terminal audit
  fails after a request may have reached the remote service, report an explicit
  audit-after-side-effect outcome with unknown remote state and never retry
  automatically.

#### 8.4 Command and typed-tool adapters

- [ ] **8.4.1** Add one structured HTTP tool and one virtual command over the same
  gateway and normalized conformance suite.
- [ ] **8.4.2** Prefer a product-specific `fetch` or `http` command first. If the command
  is named `curl`, define and test a recognizable bounded subset without config files,
  arbitrary protocols, proxies, Unix sockets, host paths, credential files, or insecure
  TLS.
- [ ] **8.4.3** Let an optional bounded output-file operation publish through a narrow
  workspace mutator. Denial, timeout, cancellation, overflow, transport failure, or
  invalid destination leaves the target unchanged.
- [ ] **8.4.4** Label remote content as untrusted and bound any model-visible headers or
  body independently of transport limits.

#### 8.5 Security and conformance

- [ ] **8.5.1** Test URL canonicalization, IDNA behavior, IP literals, mixed DNS answers,
  DNS rebinding, redirects, IPv4/IPv6 address classes, proxy isolation, TLS validation,
  decompression, budgets, cancellation, audit failure, and cleanup.
- [ ] **8.5.2** Keep required CI deterministic and public-network-free through fake
  resolution and transport. Live endpoint tests remain optional.
- [ ] **8.5.3** Prove trusted command/tool implementations receive no raw socket, host
  process, host environment, or credential capability through their MemSandbox
  contracts.

### Exit criteria

- [ ] **8.9.1** A default sandbox exposes and performs no networking.
- [ ] **8.9.2** Every connection and redirect is admitted against its normalized and
  resolved destination before transport or credential access.
- [ ] **8.9.3** Destination-bound credentials, output, errors, events, and persistence
  pass the complete secret-canary suite.
- [ ] **8.9.4** Per-request and cumulative session budgets prevent unbounded request,
  transfer, decompression, duration, and concurrency use.
- [ ] **8.9.5** The command and typed tool are adapters over one gateway and neither
  invokes a host shell, host `curl`, or independent HTTP client.
- [ ] **8.9.6** Documentation states that arbitrary external code requires system-level
  egress enforcement below the guest.
- [ ] **8.9.7** State-changing requests are never retried implicitly, and audit failure
  after a possible remote side effect reports unknown remote outcome truthfully.

## 13. Milestone 9: external Python execution

**Prerequisites:** Milestone 7 is complete. Milestone 8 is required before any execution
profile receives network access; the first Python profile remains network-disabled.

Detailed design:
[External Execution and Python Runtime](./components/external-execution/README.md).

### Goal

Run a bounded Python program stored in the virtual workspace through a host-selected
external backend while preserving explicit security tiers, atomic workspace publication,
resource limits, cancellation, cleanup, and provenance.

### Non-goals

- Executing arbitrary Python in the MemSandbox application process.
- Describing a host subprocess, AST filter, import restriction, or container name alone
  as secure containment.
- A full shell, PTY, REPL, notebook, debugger, detached service, or inbound port.
- Transparent host-directory mounts or inherited host environment and credentials.
- Agent-controlled runtime images, package indexes, `pip install`, native builds, or
  mutable shared environments.
- Repository cloning as an implicit execution side effect.

### Work

#### 9.1 Backend contract and security profile

- [ ] **9.1.1** Define async framework-neutral execution requests, results, limits,
  usage, runtime provenance, operation context, stable errors, and a narrow
  `ExternalExecutionBackend` port.
- [ ] **9.1.2** Keep the backend language-neutral while defining Python as the first
  immutable runtime profile.
- [ ] **9.1.3** Document and expose the backend security classification. A
  `trusted-host-execution` subprocess is a development profile and makes no
  hostile-code or multi-tenant containment claim.
- [ ] **9.1.4** Select one isolated reference backend from representative workload,
  cross-platform, startup, package, isolation, network-control, and maintenance
  evidence. Record its exact threat assumptions.
- [ ] **9.1.5** Reject execution when the selected backend cannot enforce every hard
  limit or egress property required by its advertised profile.

#### 9.2 Workspace transfer and publication

- [ ] **9.2.1** Capture input revision and root hash and export one deterministic bounded
  portable workspace archive under the serialized session operation.
- [ ] **9.2.2** Transfer into a backend-owned workspace root without exposing a
  model-selected host path. Reject links, devices, sockets, path escapes, duplicates,
  unsupported metadata, and quota overflow.
- [ ] **9.2.3** Collect a complete bounded candidate workspace after normal process
  termination and validate it through workspace-owned archive preparation.
- [ ] **9.2.4** Atomically publish a valid candidate after exit code zero or non-zero.
  Publish nothing after timeout, cancellation, forced termination, infrastructure
  failure, malformed output, transfer failure, accounting failure, or failed required
  pre-commit audit.
- [ ] **9.2.5** Preserve a revision precondition so future session concurrency cannot
  cause silent lost updates.
- [ ] **9.2.6** Measure the complete-copy baseline before considering incremental diffs,
  RPC filesystems, FUSE, 9P, shared content providers, or copy-on-write optimization.

#### 9.3 Isolation, resources, and lifecycle

- [ ] **9.3.1** Enforce wall time, CPU, memory, process count, stdout, stderr, scratch,
  workspace output, transfer, network, and concurrency limits outside agent control.
- [ ] **9.3.2** Reserve worst-case resources before backend allocation and settle actual
  usage exactly once on every terminal path. Missing measurements remain unavailable,
  not zero.
- [ ] **9.3.3** Define one cancellation-resilient lifecycle from allocation through
  process-tree termination, candidate collection, publication decision, backend release,
  accounting settlement, and terminal event delivery.
- [ ] **9.3.4** Prohibit detached processes and prove complete backend resource
  reclamation after success, non-zero exit, denial, timeout, cancellation, failure, and
  cleanup error.
- [ ] **9.3.5** Keep provider handles, host paths, raw runtime errors, and cleanup
  internals outside model-visible results and events.

#### 9.4 Python runtime, network, secrets, and provenance

- [ ] **9.4.1** Define a host-owned runtime with exact Python version, immutable image or
  environment identity, fixed package-set or lock digest, deterministic locale/encoding
  where supported, and an explicit environment allowlist.
- [ ] **9.4.2** Start with execution of a workspace script and bounded arguments.
  Interactive stdin, `python -c`, package installation, and mutable runtimes require
  separate evidence and approval.
- [ ] **9.4.3** Keep network disabled by default. A connected execution profile exists
  only when the backend enforces the Milestone 8 grant below the guest; passing a Python
  helper object is not enforcement.
- [ ] **9.4.4** Do not copy the current command secret overlay into guest code. Design
  any execution secret grant separately, bind it to runtime and destination policy, and
  prefer brokered host operations over revealing reusable values.
- [ ] **9.4.5** Record execution ID, backend and adapter version, security profile,
  runtime/image/package digests, input and output hashes and revisions, limits, measured
  usage, network-policy identifier, publication outcome, and cleanup outcome as bounded
  provenance.
- [ ] **9.4.6** State explicitly that reproducible configuration does not make arbitrary
  Python deterministic.

#### 9.5 Command, tool, and conformance surfaces

- [ ] **9.5.1** Add a constrained virtual `python` command and a typed `run_python` tool
  over the same execution boundary.
- [ ] **9.5.2** If using the `python` command name, publish exact supported CLI behavior
  and do not imply access to the host interpreter, REPL, package manager, or full
  environment.
- [ ] **9.5.3** Add fake-backend conformance for output, files, exit status, publication,
  provenance, accounting, events, cancellation, and cleanup before backend-specific
  tests.
- [ ] **9.5.4** Run the same representative Python workspace scenario through the direct
  session, command adapter, typed-tool adapter, trusted-host development backend if
  shipped, and isolated reference backend.
- [ ] **9.5.5** Extend secret and host-boundary canaries through guest input, environment,
  files, output, snapshots, events, exceptions, provider diagnostics, and
  representations.

### Exit criteria

- [ ] **9.9.1** Arbitrary agent-supplied Python never executes inside the MemSandbox
  process and remains absent from the default profile.
- [ ] **9.9.2** Every backend exposes accurate isolation, runtime, package, and limit
  provenance without overstating a subprocess or container boundary.
- [ ] **9.9.3** Workspace transfer is bounded and integrity-checked, and returned changes
  follow the documented atomic publication matrix.
- [ ] **9.9.4** Hard backend limits and cumulative session accounting cover all required
  resource dimensions and terminal paths.
- [ ] **9.9.5** Session deletion, timeout, and cancellation leave no running process or
  retained per-execution resource.
- [ ] **9.9.6** Networking is disabled or enforced below the guest through the controlled
  egress grant.
- [ ] **9.9.7** Command and typed-tool adapters produce equivalent normalized results
  through the same backend contract.

## 14. Milestone 10: durable state and remote operation

**Prerequisites:** Milestone 6 supplies the content-placement decision; Milestone 7
supplies authority and accounting; Milestone 9 supplies external-execution lifecycle
semantics before remote workers are considered.

### Goal

Decide and, only with demonstrated deployment demand, implement the durable state and
remote-worker boundaries needed to resume work across processes or hosts.

### Non-goals

- Assuming content offload alone persists complete live sessions.
- Turning a host directory into a transparent sandbox mount.
- Adding multi-tenancy without authenticated principals, authorization, quotas, and
  tenant-safe retention.
- Committing to a hosted MemSandbox service before a concrete deployment use case exists.

### Work

#### 10.1 Durable content and snapshots

- [ ] **10.1.1** Before depending on content offload for remote operation, satisfy the
  Milestone 6 reconsideration trigger and deliver a separately approved provider
  conformance milestone.
- [ ] **10.1.2** Define durable snapshot and session-metadata stores separately from
  immutable file-content placement.
- [ ] **10.1.3** Specify provider identity, compatibility, encryption, retention,
  reachability, garbage collection, corruption, retry, timeout, and permanent-loss
  behavior.
- [ ] **10.1.4** Prove cross-process resume without eagerly materializing content where
  provider-linked snapshots are supported.

#### 10.2 Ownership and recovery

- [ ] **10.2.1** Introduce authenticated principals and tenant authorization before
  sharing stores, handles, snapshots, budgets, or workers across trust boundaries.
- [ ] **10.2.2** Define exclusive session leases, fencing, heartbeat/expiry, idempotent
  operations, crash recovery, and orphan cleanup.
- [ ] **10.2.3** Extend cumulative accounting and audit export across process and tenant
  boundaries without trusting worker-supplied identity or usage blindly.

#### 10.3 Remote execution

- [ ] **10.3.1** Define a versioned remote worker protocol only after the local external
  execution contract passes conformance.
- [ ] **10.3.2** Authenticate and encrypt control, workspace, result, event, and
  cancellation channels.
- [ ] **10.3.3** Preserve the same workspace publication, failure, resource, egress,
  provenance, and cleanup semantics across local and remote backends.

### Exit criteria

- [ ] **10.9.1** The deployment requirement and selected durable/remote scope are
  documented; unused service abstractions are not added speculatively.
- [ ] **10.9.2** Content placement, snapshot durability, live-session ownership, and
  remote execution remain distinct contracts.
- [ ] **10.9.3** Approved durable state survives process loss with explicit integrity,
  retention, and recovery behavior.
- [ ] **10.9.4** Approved remote execution preserves local conformance and uses
  authenticated ownership, fencing, cumulative accounting, and complete audit.

## 15. Milestone 11: ecosystem and extension maturity

**Prerequisites:** Select only surfaces backed by completed core behavior. A second SDK
does not block networking or execution, and a transport does not redefine domain models.

### Goal

Prove that mature workspace and external-capability contracts can support additional
agent frameworks, transports, and repository-aware workflows without framework leakage
or weaker security semantics.

### Non-goals

- Supporting every framework, transport, hosted provider, VCS, or shell feature.
- Extracting a shared framework abstraction before two real adapters prove it.
- Letting MCP, HTTP, or an SDK become the core domain model.
- Implementing typed VCS by invoking an unrestricted host Git process.

### Work

#### 11.1 Additional agent SDK

- [ ] **11.1.1** Select PydanticAI, LangChain Deep Agents, or another SDK from current
  adoption, extension fit, maintenance cost, and user evidence.
- [ ] **11.1.2** Implement only the tool/capability or workspace/backend surface justified
  by that SDK and run the shared stateful conformance scenario.
- [ ] **11.1.3** Extract framework-neutral sample scenarios only where the second
  integration proves the abstraction.

#### 11.2 Repository-aware capability decision

- [ ] **11.2.1** Evaluate whether host-controlled import/export is sufficient for agent
  workflows before adding model-visible VCS operations.
- [ ] **11.2.2** If justified, design typed status, diff, commit, branch, fetch, and push
  operations with separate local-state and remote-mutation authority.
- [ ] **11.2.3** Route remote VCS access through controlled egress and
  destination-bound credentials. Do not expose credential helpers, host Git
  configuration, host checkout paths, or arbitrary Git subprocess arguments.

#### 11.3 Protocol and extension packaging

- [ ] **11.3.1** Re-evaluate MCP and an HTTP/OpenAPI control plane against implemented
  lifecycle, identity, streaming, cancellation, event, and authorization requirements.
- [ ] **11.3.2** Separate model-facing tools from host administrative lifecycle and
  policy endpoints.
- [ ] **11.3.3** Define extension package compatibility, optional dependency ownership,
  support policy, capability discovery by the host, and conformance certification.
- [ ] **11.3.4** Keep untrusted or model-controlled extension installation unsupported.

#### 11.4 Product and operational evidence

- [ ] **11.4.1** Extend benchmarks to external provider, network, repository-transfer,
  and execution profiles without weakening the fast dependency-free virtual baseline.
- [ ] **11.4.2** Add deterministic replay/provenance evidence where recorded workspace
  hashes, runtime versions, policy outcomes, usage, and external-input identities make
  reconstruction possible.
- [ ] **11.4.3** Publish a compatibility and security-profile matrix that distinguishes
  logical, connected, trusted-host, container/managed, VM/microVM, and WASM claims.

### Exit criteria

- [ ] **11.9.1** At least two agent SDK integrations pass shared conformance without
  framework imports in core.
- [ ] **11.9.2** Any typed VCS surface preserves host-path isolation, destination-bound
  credentials, explicit remote-mutation authority, and atomic workspace semantics.
- [ ] **11.9.3** Any transport preserves domain errors, lifecycle, authorization,
  cancellation, limits, events, and model-versus-host authority.
- [ ] **11.9.4** Extension and profile compatibility are versioned, tested, and
  accurately documented.

## 16. Checklist execution rules

- Checklist IDs use `milestone.area.task`; for example, `1.3.4` is milestone 1, workspace
  read area 3, task 4.
- Area `9` is reserved for milestone exit criteria.
- Checklist IDs are stable. Reword a task without renumbering it unless its milestone or
  component ownership changes.
- Each work item is issue-sized by default. Split an item only when it cannot be completed
  with focused tests and one component-document update.
- Complete checklist items in order within an area unless an explicit dependency permits
  safe parallel work.
- Mark a work item complete only when its tests pass and the affected component
  `README.md` reflects the behavior.

## 17. Test strategy

### Unit tests

Use simple in-memory fakes for collaborators. Verify outcome and interactions at each
interface seam.

Required paths for every behavior:

- happy path
- policy or safety block
- dependency failure
- minimum and maximum boundary
- cancellation or timeout where applicable

### Integration tests

- Workspace plus command executor.
- Session plus all in-memory components.
- Snapshot round-trip.
- Adapter plus fake framework boundary when possible.
- Host-controlled repository archive import and result export.
- Network gateway plus fake resolver, policy, credential, transport, event, and
  accounting boundaries.
- External execution plus fake backend, workspace transfer, publication, cleanup, and
  accounting boundaries.

### Conformance tests

One behavior suite runs against:

- direct `SandboxSession`
- OpenAI Agents SDK sandbox client/session
- OpenAI Agents SDK custom capability

PydanticAI capabilities and LangChain Deep Agents backends remain future candidates and
are added to the shared suite only after separate adapter decisions and implementations.

Future external profiles add conformance drivers only after their direct domain boundary
passes:

- controlled HTTP gateway, virtual command, and typed tool;
- external execution service, virtual Python command, and typed run-Python tool;
- trusted-host and isolated backends with distinct security assertions;
- local and remote backends if Milestone 10 approves remote execution.

The expected workspace hashes and domain outcomes remain the same. The complete
create-through-resume lifecycle and normalized trace are defined in
[Product Validation and Benchmark Design](./product-validation/README.md).

### Product benchmarks

The product benchmark suite measures provisioning separately from correctness:

- shared CI runs conformance and benchmark smoke cases;
- controlled runners establish release baselines and regression budgets;
- comparative runs are required only for scoped market claims.

No benchmark dependency is imported by the production package, and no required
provisioning benchmark performs a model or provider network call.

### Property and state-machine tests

Use generated operation sequences for:

- path normalization
- quota accounting
- write/remove/copy/move invariants
- snapshot round-trips
- parser tokenization
- deterministic execution
- resource reservation and settlement
- repository archive validation and diff integrity
- URL normalization, destination classification, and redirect admission
- external-execution publication and cleanup state machines

## 18. Decision gates

Milestone 0 decisions are resolved. Later implementation decisions remain open until
their referenced checklist task begins.

| Decision | Selection or recommendation | Task | Status |
|---|---|---|---|
| Distribution/import name | `mem-sandbox` / `mem_sandbox` | `0.1.1` | Resolved |
| Python baseline | Python 3.12+ | `0.1.2` | Resolved |
| OSS license | MIT | `0.1.3` | Resolved |
| Project toolchain | uv, Hatchling, pytest, Ruff, Pyright | `0.1.4` | Resolved |
| Branch workflow | Protected `main` plus issue branches | `0.1.5` | Resolved |
| CI support matrix | Windows and Ubuntu; Python 3.12 and 3.14 | `0.1.6` | Resolved |
| Authoritative design location | `mem-sandbox/docs` | `0.1.7` | Resolved |
| Workspace/session defaults | `/workspace`; one-based inclusive; serialized | `0.1.8` | Resolved |
| Workspace consistency | Per-operation linearizability; one session gate and one coarse workspace state lock; no MVCC | `1.4` | Resolved |
| Workspace metadata | Deterministic kind, size, hash, and revision only | `1.2` | Resolved |
| Default workspace quotas | 4 MiB/file, 16 MiB total, 10,000 nodes, bounded paths, reads, patches, and snapshots | `1.5` | Resolved |
| Initial versioning | `0.1.0` with semantic versioning | `0.1.9` | Resolved |
| Repository visibility | Remain private until a later explicit decision | `0.1.10` | Resolved |
| Server-side branch protection | Defer while private plan lacks support | `0.0.8` | Deferred |
| Patch format | Constrained UTF-8 multi-file unified diff with atomic application | `1.7.1` | Resolved |
| Snapshot encoding | Canonical UTF-8 JSON, sorted entries, base64 bytes, SHA-256 integrity | `1.8.1` | Resolved |
| First command profile | Exact `pwd`, `cd`, `ls`, `cat`, `echo`, `mkdir`, `touch`, and `rm` profile | `2.4.2` | Resolved |
| Command grammar | Quote-aware `;`, `&&`, `>`, and `>>`; approved variable expansion; no implicit shell features | `2.2` | Resolved |
| Command failures | Structured exit 127/2/1 results; exceptions reserved for executor failures | `2.1` | Resolved |
| Command redirection | Output-only commands; atomic replace and workspace-owned append | `2.5` | Resolved |
| Command output | Independent 256 KiB stream caps with deterministic UTF-8 truncation | `2.6.5` | Resolved |
| Timeout state | 30-second plan timeout; keep committed files and discard transient cwd/environment on timeout | `2.6` | Resolved |
| Pipeline semantics | Bounded sequential UTF-8 transformations between registered pipeline-safe commands; fixed pipefail; final-stage-only redirection; no shell emulation | `4.1.6` | Resolved |
| Composed policy engine | Retain the explicit allow-all admission seam; revisit only for a concrete owner, secret, network, host-execution, or shared-state trust boundary | `4.2` / `5.7` | Deferred |
| Service ownership | Preserve application-supplied `OwnerId` as provenance; applications own principal-to-handle and snapshot-reference authorization | `4.7` | Resolved |
| Session lifetime | No automatic live-session expiry; explicit deletion owns process-local cleanup | `4.7` | Resolved |
| Snapshot expiration | Require an explicit store-level default TTL; the store assigns absolute expiry and performs lazy/explicit purge | `4.4` | Resolved |
| Service lifecycle events | Keep `sandbox.created`/`sandbox.deleted` producer-less until shared sequencing or separate service identity is approved | `4.7` | Deferred |
| Product validation | Stateful cross-adapter conformance plus a non-gating reference measurement; controlled evidence is required before regression gates and comparative evidence before a scoped "fastest" claim | `5.6` | Resolved |
| First framework adapter | OpenAI Agents SDK custom capability plus sandbox client/session | `5.2` / `5.3` | Resolved |
| First live agent sample | Official OpenAI and Azure OpenAI models through the OpenAI Agents SDK and host-bound MemSandbox capability; live calls remain opt-in | `5.8` / `#7` / `#45` | Resolved |
| Workspace mutation scaling | Prioritize phase instrumentation, single-pass seeding, and an immutable path-copying tree with cached subtree summaries in separately approved work | `6.2` / `6.9` | Resolved |
| Workspace content offload | Deferred after Milestone 6; reconsider only on a measured capacity trigger with host budgets and provider lifecycle requirements | `6.3` / `6.9` | Deferred |
| External capability profiles | Preserve `virtual` as the default; add host-selected `connected`, `trusted-host-execution`, and `isolated-execution` profiles without snapshot- or model-driven widening | `7.1` | Planned |
| Unified resource accounting | Use typed operation/session scopes with worst-case reservation and exact-once settlement; keep component hard limits authoritative | `7.3` | Planned |
| Repository ingestion and export | Host-controlled bounded archive/tree import plus revision/hash-bound diff, archive, or artifact export; no host path or Git authority | `7.4` | Planned |
| Network egress | Default-deny HTTP/HTTPS through one host-owned gateway with destination policy, SSRF controls, destination-bound credentials, cumulative budgets, and audit | `8` | Planned |
| First network command | Prefer `fetch` or `http`; use `curl` only for a documented compatible subset; never invoke a host executable | `8.4` | Open |
| External execution | Run agent-supplied code only through a host-selected external backend with explicit security classification and atomic workspace publication | `9` | Planned |
| First external runtime | Immutable bounded Python workspace-script profile with package installation and networking disabled initially | `9.4` | Planned |
| First isolated backend | Select from measured workload and threat-model evidence; a trusted host subprocess is not an isolation candidate | `9.1.4` | Open |
| Durable and remote operation | Require a concrete deployment need, authenticated ownership, fencing, retention, recovery, and local-contract conformance | `10` | Deferred |
| Typed VCS capability | Evaluate only after host-controlled repository exchange and controlled egress are proven | `11.2` | Deferred |
| Second agent SDK | Select from user and ecosystem evidence after the OpenAI integration; do not pre-build a framework abstraction | `11.1` | Deferred |

## 19. First usable release closure

The first usable release was completed by Milestone 5:

- [x] A host can create, use, and close an in-memory session.
- [x] The four default agent operations work through `SandboxSession`.
- [x] Paths and mutations are deterministic, atomic, and quota-safe.
- [x] The virtual command executor supports the documented command profile without host
  fallback.
- [x] Snapshots restore files, cwd, and approved environment state.
- [x] The minimal explicit admission seam and dependency failures produce stable domain
  errors; a composed policy engine is not required for the first usable release.
- [x] Events contain no secret or unbounded content.
- [x] The OpenAI Agents SDK sandbox client/session and custom capability pass the shared
  conformance scenario.
- [x] The stateful create, snapshot, resume, continue, fork, and cleanup scenario passes
  through the direct session and supported adapters.
- [x] A short-duration provisioning and adapter-overhead reference is published; any
  future regression gate uses a controlled runner with documented budgets.
- [x] Documented official OpenAI and Azure OpenAI samples complete stateful tasks through
  the host-bound four-tool capability while required CI remains deterministic and
  network-free.
- [x] Core has no OpenAI, PydanticAI, LangChain, MCP, or other framework dependency.
- [x] Every implemented boundary is reflected in its component design document.
