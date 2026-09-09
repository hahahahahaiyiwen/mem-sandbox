# MemSandbox Implementation Plan

**Status:** Active implementation roadmap
**Current focus:** Milestone 5. The OpenAI integration and product evidence landed
through issue #36; issue #37 records the final trust-boundary and deferred-integration
review, and issue #45 is the remaining Azure OpenAI sample gate.

**Approved design inputs:** [High-Level Design](./HIGH_LEVEL_DESIGN.md),
[Workspace Design](./components/workspace/README.md), and
[Command Executor Design](./components/command-executor/README.md)

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
  -> policy and secret re-evaluation when a concrete trust boundary exists
```

The workspace is the state model and consistency boundary. The command executor consumes
workspace interfaces, so implementing both independently or in parallel would force the
executor to guess path, mutation, quota, and error semantics.

Do not wait until every filesystem feature and shell command is complete before composing
a session. After the workspace and a small command set work, build one vertical slice
through the four agent-facing operations. That validates the architecture before the
implementation becomes large.

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
- Keep MCP, remote transports, arbitrary Python execution, and full POSIX compatibility
  outside this plan.

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
`dedb04ee5b528c0cb3a605e086dc792c26485106`. The child-issue reconciliation,
cross-platform CI matrix, benchmark reference, trust-boundary outcome, and final scope
are recorded on tracking issue
[#7](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/7).

## 10. Milestone 6: workspace scalability decision

This milestone begins only after every Milestone 5 exit criterion is complete. It is a
post-release decision checkpoint, not a commitment to implement content offload.

### Goal

Revisit [workspace content offload](./components/workspace/content-offload/README.md)
using representative workload data and measured performance while preserving the product
priority of fastest provisioning.

### Work

#### 6.1 Evidence and baseline

- [ ] **6.1.1** Extend the Milestone 5 product-validation baseline only where additional
  workspace-size profiles or mutation measurements are needed for the scalability
  decision.
- [ ] **6.1.2** Record representative workload distributions for logical workspace
  bytes, individual file bytes, node count, session lifetime, snapshot frequency, and
  file access patterns.
- [ ] **6.1.3** Determine whether the primary scaling constraint is resident file bytes,
  tree copying, hash recomputation, metadata volume, snapshot encoding, or another
  measured cause.

#### 6.2 Design review

- [ ] **6.2.1** Review the deferred content-offload design against the implemented
  workspace, session, command, event, snapshot-store, and deferred-policy boundaries.
- [ ] **6.2.2** Compare content offload with simpler alternatives such as adjusted
  quotas, lower process density, structural sharing, cached subtree hashes, and
  incremental accounting.
- [ ] **6.2.3** Decide to defer, prototype, or plan implementation and record the
  evidence, product impact, compatibility constraints, and rationale in the workspace
  design.

#### 6.3 Follow-up planning

- [ ] **6.3.1** If approved, create a separate implementation milestone and issue set
  with explicit provisioning-latency, memory, correctness, and provider conformance
  acceptance criteria.
- [ ] **6.3.2** If deferred, record the measurable trigger and next review point rather
  than leaving the decision open-ended.

### Exit criteria

- [ ] **6.9.1** Baseline measurements and representative workload assumptions are
  documented.
- [ ] **6.9.2** The content-offload decision and rationale are recorded in the workspace
  documentation.
- [ ] **6.9.3** No content-offload implementation starts without a separately approved
  milestone based on the review evidence.

## 11. Checklist execution rules

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

## 12. Test strategy

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

### Conformance tests

One behavior suite runs against:

- direct `SandboxSession`
- OpenAI Agents SDK sandbox client/session
- OpenAI Agents SDK custom capability

PydanticAI capabilities and LangChain Deep Agents backends remain future candidates and
are added to the shared suite only after separate adapter decisions and implementations.

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

## 13. Decision gates

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
| First live agent sample | Azure OpenAI model through the OpenAI Agents SDK and host-bound MemSandbox capability; live calls remain opt-in | `5.8` | Planned in #45 |
| Workspace content offload | Revisit after Milestone 5 using measured workload and provisioning data | `6.2.3` | Deferred |

## 14. Definition of first usable release

The first usable release is complete when:

- [ ] A host can create, use, and close an in-memory session.
- [ ] The four default agent operations work through `SandboxSession`.
- [ ] Paths and mutations are deterministic, atomic, and quota-safe.
- [ ] The virtual command executor supports the documented command profile without host
  fallback.
- [ ] Snapshots restore files, cwd, and approved environment state.
- [ ] The minimal explicit admission seam and dependency failures produce stable domain
  errors; a composed policy engine is not required for the first usable release.
- [ ] Events contain no secret or unbounded content.
- [ ] The OpenAI Agents SDK sandbox client/session and custom capability pass the shared
  conformance scenario.
- [ ] The stateful create, snapshot, resume, continue, fork, and cleanup scenario passes
  through the direct session and supported adapters.
- [ ] A short-duration provisioning and adapter-overhead reference is published; any
  future regression gate uses a controlled runner with documented budgets.
- [ ] A documented Azure OpenAI sample completes a stateful task through the host-bound
  four-tool capability while required CI remains deterministic and network-free.
- [ ] Core has no OpenAI, PydanticAI, LangChain, MCP, or other framework dependency.
- [ ] Every implemented boundary is reflected in its component design document.
