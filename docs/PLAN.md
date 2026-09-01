# MemSandbox Implementation Plan

**Status:** Proposed implementation sequence
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
  -> policy, snapshots, secrets, and events
  -> framework adapters
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

- [ ] **3.1.1** Write tests for created, running, closing, closed, and failed lifecycle
  transitions.
- [ ] **3.1.2** Implement session identity and explicit lifecycle-state enforcement.
- [ ] **3.1.3** Write concurrency tests for queued operations, waiter cancellation, and
  exclusive restore.
- [ ] **3.1.4** Serialize operations behind one cancellation-safe async coordination
  gate.

#### 3.2 Agent-facing operations

- [ ] **3.2.1** Define stable domain requests and results for `execute`, `read_file`,
  `write_file`, and `apply_patch`.
- [ ] **3.2.2** Write behavior tests for each operation through `SandboxSession` using
  fake collaborators.
- [ ] **3.2.3** Implement the four operations by orchestrating constructor-injected
  workspace and command-executor interfaces.
- [ ] **3.2.4** Add host-only binary read, binary write, stat, and list operations without
  exposing them in the default agent tool profile.

#### 3.3 Explicit minimal collaborators

- [ ] **3.3.1** Implement an allow-all policy engine whose decision is explicit and
  observable.
- [ ] **3.3.2** Implement a no-secret broker that rejects all secret resolution
  explicitly.
- [ ] **3.3.3** Implement a no-op event sink with a documented delivery contract.
- [ ] **3.3.4** Inject all collaborators through constructor interfaces; do not use
  `None` checks or catch-and-ignore behavior.

#### 3.4 Session snapshots

- [ ] **3.4.1** Define session snapshot state for workspace, cwd, approved environment,
  and compatibility metadata.
- [ ] **3.4.2** Implement an in-memory snapshot store and session snapshot creation.
- [ ] **3.4.3** Implement exclusive session restore using the workspace codec.
- [ ] **3.4.4** Write tests for ownership, missing snapshots, incompatible snapshots,
  atomic restore failure, and successful round-trip.

#### 3.5 Vertical-slice conformance

- [ ] **3.5.1** Build one framework-free scenario that uses only the public
  `SandboxSession` interface.
- [ ] **3.5.2** Assert files, cwd, approved environment, hashes, revisions, operation
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

- [ ] **3.9.1** `python -m pytest tests/unit/session -q` passes.
- [ ] **3.9.2**
  `python -m pytest tests/integration/session/test_minimal_vertical_slice.py -q` passes.
- [ ] **3.9.3** Public-session tests use no concrete workspace or executor types.
- [ ] **3.9.4** Restore-concurrency tests prove that snapshot restore is exclusive and
  failed restore leaves all session state unchanged.
- [ ] **3.9.5** Timeout and cancellation tests prove no queued or later mutation leaks
  past the terminal result.
- [ ] **3.9.6** The vertical slice imports and runs without an LLM or agent-framework
  dependency.
- [ ] **3.9.7** `docs/components/sandbox-session/README.md` and
  `docs/components/snapshot-store/README.md` match the implemented behavior.

## 8. Milestone 4: complete core behavior

### Goal

Complete the framework-neutral core after the vertical slice has proved its boundaries,
including the remaining command profile, policy, events, snapshots, and secrets.

### Work

#### 4.1 Workspace and command completion

- [ ] **4.1.1** Write tests and implement `head`, `tail`, `grep`, `find`, `wc`, `sort`,
  and `uniq`.
- [ ] **4.1.2** Write tests and implement `cp` and `mv` through existing workspace ports.
- [ ] **4.1.3** Write tests and implement `env`, `export`, and scripts through the same
  parser and command registry.
- [ ] **4.1.4** Add native file-info or search APIs only when an approved adapter
  requirement cannot use the existing operations efficiently.
- [ ] **4.1.5** Reject unimplemented POSIX behavior explicitly rather than adding host
  fallbacks or partial emulation.
- [ ] **4.1.6** Write tests and implement the
  [approved pipeline semantics](./components/command-executor/README.md#approved-milestone-4-pipeline-semantics):
  parse higher-precedence `|` into immutable pipeline stages; admit only registered
  pipeline-safe non-mutating commands; execute stages sequentially through bounded UTF-8
  stdin and stdout; use the rightmost non-zero status; preserve stderr in stage order;
  permit descriptor-approved redirection only on the final stage; and return a structured
  non-zero pipeline-limit result rather than pass truncated intermediate data. Preserve
  `;` and `&&` behavior around that result. Add explicit stage-count and
  aggregate-materialization limits. Do not use host processes or concurrent stage
  execution.
- [ ] **4.1.7** Run an executable multi-model command-usability evaluation before the
  first framework adapter, including acceptance, repair turns, tool calls, tokens,
  latency, output size, and truncation.

#### 4.2 Policy engine

- [ ] **4.2.1** Define typed policy requests, decisions, denial reasons, effective
  limits, and obligations.
- [ ] **4.2.2** Write happy, denied, failure, and boundary tests for operation admission
  and path read/write rules.
- [ ] **4.2.3** Implement operation, path, command, and argument policy evaluation.
- [ ] **4.2.4** Implement effective timeout, output, and quota limit composition.
- [ ] **4.2.5** Implement secret-reference and destination rules before enabling secret
  resolution.

#### 4.3 Event sink

- [ ] **4.3.1** Define structured event envelopes, event categories, per-session
  sequence numbers, and sensitivity classifications.
- [ ] **4.3.2** Write ordering and redaction tests for lifecycle, operation, policy,
  snapshot, timeout, cancellation, and failure events.
- [ ] **4.3.3** Implement the process-local event sink and emit events from the session
  orchestration boundary.
- [ ] **4.3.4** Bound event payloads and reject secret or unbounded content.

#### 4.4 Snapshot store completion

- [ ] **4.4.1** Write tests for snapshot ownership, count and byte limits, expiration,
  compatibility, and integrity.
- [ ] **4.4.2** Implement process-local snapshot limits and expiration.
- [ ] **4.4.3** Implement explicit compatibility and integrity checks before restore
  mutation begins.

#### 4.5 Secret broker

- [ ] **4.5.1** Define secret references and operation-scoped lease types without
  exposing raw values in domain results.
- [ ] **4.5.2** Write tests for authorization, expiry, cleanup, redaction, and dependency
  failures.
- [ ] **4.5.3** Implement secret resolution only after policy approval and event
  redaction are active.
- [ ] **4.5.4** Prove secret values never enter workspace files, snapshots, errors,
  results, or events.

#### 4.6 Core conformance

- [ ] **4.6.1** Run one shared behavior suite against direct `SandboxSession`.
- [ ] **4.6.2** Add generated operation-sequence tests for paths, quota accounting,
  mutation invariants, snapshots, parser tokenization, and deterministic execution.
- [ ] **4.6.3** Update every affected component `README.md` in the same change as its
  behavior.

### Exit criteria

- [ ] **4.9.1** `python -m pytest tests/unit tests/integration -q` passes.
- [ ] **4.9.2** `python -m pytest tests/conformance/test_direct_session.py -q` passes.
- [ ] **4.9.3** `python -m ruff check src tests` and
  `python -m pyright src tests` pass.
- [ ] **4.9.4** Every policy-aware behavior has happy, denied, dependency-failure, and
  exact-boundary coverage.
- [ ] **4.9.5** Secret-canary tests find no secret value in files, snapshots, errors,
  results, or events.
- [ ] **4.9.6** Unsupported behavior tests prove failure occurs before mutation and
  without host fallback.
- [ ] **4.9.7** The dependency-boundary test still proves no agent-framework dependency
  is present in core.
- [ ] **4.9.8** Pipeline tests prove precedence, pipeline-safe producer/consumer
  admission, fixed pipefail status, final-only stdout, ordered stderr,
  descriptor-gated final-stage-only redirection, structured limit-failure sequencing,
  and exact intermediate, aggregate, timeout, and cancellation boundaries.

## 9. Milestone 5: native integrations

### Goal

Integrate the proven core with Python agent frameworks while preserving core ownership of
filesystem, command, policy, lifecycle, and snapshot behavior.

### Work

#### 5.1 Shared tool/capability adapter

- [ ] **5.1.1** Define framework-neutral tool schemas for `execute`, `read_file`,
  `write_file`, and `apply_patch`.
- [ ] **5.1.2** Implement shared request, result, correctable-error, terminal-error, and
  cancellation translation.
- [ ] **5.1.3** Write adapter conformance tests proving no policy, filesystem, command,
  or snapshot behavior is reimplemented in the adapter.

#### 5.2 PydanticAI capability

- [ ] **5.2.1** Pin and document the supported PydanticAI version range.
- [ ] **5.2.2** Implement a constructor-injected PydanticAI capability with exactly the
  four approved tools.
- [ ] **5.2.3** Run the shared conformance scenario through the capability and assert the
  same domain outcomes and workspace hashes as direct `SandboxSession`.

#### 5.3 Deep Agents workspace/backend adapter

- [ ] **5.3.1** Pin and document the supported Deep Agents version and backend support
  matrix.
- [ ] **5.3.2** Map backend lifecycle and filesystem operations to one owned core
  session.
- [ ] **5.3.3** Write tests for binary round-trip, lifecycle cleanup, snapshots,
  unsupported features, and absence of host fallback.
- [ ] **5.3.4** Run the shared conformance scenario through the Deep Agents backend.

#### 5.4 OpenAI Agents SDK sandbox adapter

- [ ] **5.4.1** Pin the OpenAI Agents SDK version and verify the documented abstract
  client, session, state, options, and snapshot contracts against that version.
- [ ] **5.4.2** Implement the sandbox client and session adapter over one owned core
  session.
- [ ] **5.4.3** Override inherited POSIX-assuming behavior required for the supported
  capability profile.
- [ ] **5.4.4** Reject unsupported manifests, users, groups, mounts, ports, PTY, and Git
  entries before mutation.
- [ ] **5.4.5** Run lifecycle, binary stream, snapshot, unsupported-feature, and shared
  conformance tests.

#### 5.5 Deferred integrations

- [ ] **5.5.1** Keep MCP, HTTP/OpenAPI, A2A, local subprocesses, Docker, and hosted
  providers outside this implementation phase.
- [ ] **5.5.2** Require a new approved design decision and support matrix before adding
  any deferred integration.

### Exit criteria

- [ ] **5.9.1** `python -m pytest tests/conformance -q` passes for direct session and
  every implemented adapter.
- [ ] **5.9.2** PydanticAI produces the same domain outcomes and workspace hashes as the
  direct-session reference scenario.
- [ ] **5.9.3** Each implemented workspace/backend adapter passes lifecycle, binary
  stream, snapshot round-trip, unsupported-feature, and no-host-fallback tests.
- [ ] **5.9.4** Adapter dependency tests prove framework packages are isolated from core.
- [ ] **5.9.5** Each integration `README.md` records its pinned version, support matrix,
  ownership model, unsupported behavior, and conformance results.

## 10. Milestone 6: workspace scalability decision

This milestone begins only after every Milestone 5 exit criterion is complete. It is a
post-release decision checkpoint, not a commitment to implement content offload.

### Goal

Revisit [workspace content offload](./components/workspace/content-offload/README.md)
using representative workload data and measured performance while preserving the product
priority of fastest provisioning.

### Work

#### 6.1 Evidence and baseline

- [ ] **6.1.1** Benchmark empty workspace creation, session creation, snapshot export and
  restore, resident content memory, and mutation latency at representative workspace
  sizes.
- [ ] **6.1.2** Record representative workload distributions for logical workspace
  bytes, individual file bytes, node count, session lifetime, snapshot frequency, and
  file access patterns.
- [ ] **6.1.3** Determine whether the primary scaling constraint is resident file bytes,
  tree copying, hash recomputation, metadata volume, snapshot encoding, or another
  measured cause.

#### 6.2 Design review

- [ ] **6.2.1** Review the deferred content-offload design against the implemented
  workspace, session, command, policy, event, and snapshot-store boundaries.
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
- PydanticAI capability
- Deep Agents backend
- OpenAI sandbox adapter

The expected workspace hashes and domain outcomes remain the same.

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
| First framework adapter | PydanticAI capability | `5.2.1` | Open |
| Workspace content offload | Revisit after Milestone 5 using measured workload and provisioning data | `6.2.3` | Deferred |

## 14. Definition of first usable release

The first usable release is complete when:

- [ ] A host can create, use, and close an in-memory session.
- [ ] The four default agent operations work through `SandboxSession`.
- [ ] Paths and mutations are deterministic, atomic, and quota-safe.
- [ ] The virtual command executor supports the documented command profile without host
  fallback.
- [ ] Snapshots restore files, cwd, and approved environment state.
- [ ] Policy denials and dependency failures produce stable domain errors.
- [ ] Events contain no secret or unbounded content.
- [ ] The PydanticAI capability passes the shared conformance scenario.
- [ ] Core has no OpenAI, PydanticAI, LangChain, MCP, or other framework dependency.
- [ ] Every implemented boundary is reflected in its component design document.
