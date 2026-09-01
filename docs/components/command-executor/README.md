# Command Executor Design

**Status:** Implemented in Milestone 2

## Purpose

The command executor runs a constrained, deterministic command language over the virtual
workspace. It is not a wrapper around the host shell.

The executor owns command parsing, dispatch, working-directory and environment behavior,
timeouts, output limits, and command result semantics.

The framework-neutral implementation is in `src/mem_sandbox/command_executor`. Its
default factory constructor injects the workspace reader and mutator ports into the
first-wave handlers and injects an immutable registry into the executor.

## Responsibilities

- Parse command text into a validated execution plan.
- Resolve commands from an explicit registry.
- Execute commands against narrow workspace ports.
- Maintain command-local cwd and approved environment state.
- Support deterministic sequencing and output redirection.
- Apply command, argument, timeout, and output limits.
- Cooperate with cancellation.
- Return structured stdout, stderr, exit code, duration, and truncation metadata.
- Reject unsupported shell syntax explicitly.

## Out of scope

- Arbitrary host subprocess execution.
- Importing and running arbitrary Python in process.
- Filesystem storage implementation.
- Secret policy decisions.
- Agent framework tools.
- Full POSIX compatibility.

The cross-model evidence and roadmap implications for pipelines, scripts, and structured
file tools are recorded in
[Command Composition Usability Pilot](./PIPELINE_EVALUATION.md).

## Public contract

```python
@dataclass(frozen=True)
class CommandLimits:
    max_command_bytes: int = 32 * 1024
    max_argv_entries: int = 256
    max_argument_bytes: int = 8 * 1024
    max_stdout_bytes: int = 256 * 1024
    max_stderr_bytes: int = 256 * 1024
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ExecuteRequest:
    command: str
    cwd: SandboxPath
    environment: CommandEnvironment
    limits: CommandLimits


@dataclass(frozen=True)
class ExecuteResult:
    exit_code: int
    failure_code: CommandFailureCode | None
    stdout: str
    stderr: str
    stdout_original_bytes: int
    stderr_original_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: float
    resulting_cwd: SandboxPath
    environment_changes: tuple[EnvironmentChange, ...]


class CommandExecutor(Protocol):
    async def execute(
        self,
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult: ...
```

`CommandEnvironment`, limits, requests, results, and environment changes are immutable
domain values. Environment values, environment changes, stdout, and stderr must be valid
UTF-8 text. Concrete dictionaries are not exposed across the executor boundary.

The default complete-plan timeout is 30 seconds. All limits are configurable at executor
construction or through a validated effective profile; workspace quotas remain
independently authoritative.

The session commits `resulting_cwd` and environment changes only when execution reaches a
defined completion state.

## Execution architecture

The executor separates syntax, orchestration, and command behavior:

1. Validate request text, starting state, and limits.
2. Tokenize into quote-aware word fragments and operators.
3. Parse the complete input into an immutable execution plan before dispatch.
4. Start one complete-plan timeout scope.
5. For each eligible simple command, expand approved variables against the current
   execution state.
6. Resolve the command from the immutable registry.
7. Invoke the focused handler through command-owned workspace ports.
8. Apply a permitted stdout redirection only after successful command completion.
9. Advance explicit cwd or environment state and aggregate output.
10. Return the last executed command status and final normal-completion state.

Parsing the complete plan first ensures malformed or unsupported syntax cannot execute a
prefix of the input.

## Command extension contract

Each command has one responsibility:

```python
@dataclass(frozen=True)
class CommandRequest:
    argv: tuple[str, ...]
    stdin: str


class VirtualCommand(Protocol):
    @property
    def descriptor(self) -> CommandDescriptor: ...

    async def execute(
        self,
        request: CommandRequest,
        context: CommandContext,
    ) -> CommandResult: ...
```

Commands receive only required capabilities, such as `CommandWorkspaceReader` or
`CommandWorkspaceMutator`. They do not receive the concrete session or service.

`CommandDescriptor` owns the case-sensitive name, explicit aliases, summary, usage, and
whether the command permits stdout redirection or accepts stdin. Duplicate names or
aliases fail executor construction. The registry is immutable after construction and is
the only source for agent-facing command descriptions. The default profile defines no
aliases.

`stdin` is explicit but empty for every Milestone 2 dispatch. Every first-wave descriptor
declares that it does not accept stdin, and handlers reject a non-empty value. This keeps
the boundary ready for later bounded sequential pipelines without claiming pipeline
support in the MVP. Stage stdout remains distinct from aggregate executor output for the
same reason.

## Grammar

Version 1 supports:

- shell-style whitespace
- literal single-quoted fragments
- unquoted backslash escapes for the following character
- double-quoted backslash escapes only for `$`, backtick, `"`, and `\`; before any other
  character the backslash remains literal
- `$NAME` and `${NAME}` expansion in unquoted and double-quoted fragments
- command sequencing with `;`
- success sequencing with `&&`
- one trailing stdout replacement with `>`
- one trailing stdout append with `>>`

Operators are recognized only outside quotes. Empty quoted arguments are preserved.
Expansion occurs immediately before each command is dispatched, so it observes the
current explicit execution state. Undefined approved variables expand to an empty string.
Expansion never causes word splitting, command lookup, or glob expansion.

`PWD` is derived from the current execution cwd. Version 1 has no environment-mutating
command, so returned environment changes are empty.

Version 1 rejects or does not interpret:

- pipelines
- `||`
- input redirection and heredocs
- background jobs
- command substitution
- process substitution
- multiline input
- comments
- globbing
- tilde expansion
- multiple or non-trailing redirects
- host executable lookup
- implicit fallback to `cmd.exe`, PowerShell, Bash, or `/bin/sh`

Unsupported syntax returns `CommandSyntaxUnsupported`, not an approximation.
The tokenizer recognizes `|` as an unsupported operator rather than treating it as an
argument. Later pipeline support will compose immutable command stages through explicit
stdin and stdout; it will not invoke host processes or run stages concurrently.

## Approved Milestone 4 pipeline semantics

**Status:** Approved design for Milestone 4; not implemented in version 1.

> Pipelines are bounded text transformations between registered virtual commands, not
> shell emulation.

Pipelines extend the constrained execution-plan grammar without introducing a host shell,
host processes, or POSIX process semantics.

### Grammar and plan structure

- `|` binds more tightly than `&&` and `;`. Connectors evaluate the effective result of
  the complete pipeline.
- The parser validates the complete input before dispatch and represents a pipeline as
  one immutable plan node containing immutable command stages.
- A configurable maximum stage count is validated before execution.
- One `>` or `>>` redirection may appear only after the final stage and applies to the
  complete pipeline result. The final command descriptor must permit stdout redirection.
  Redirection on an intermediate stage is rejected before dispatch.
- Input redirection, heredocs, background jobs, command substitution, process
  substitution, and implicit shell behavior remain unsupported.

### Eligible commands and state

The immutable command descriptor gains an explicit pipeline-safety declaration in
addition to stdin acceptance. Before the first stage runs, the executor expands and
resolves every stage against the current execution state and verifies that:

- every command is registered and marked pipeline-safe;
- every stage after the first accepts stdin;
- expanded arguments satisfy their limits;
- no stage changes cwd, environment, or workspace state;
- a redirected final stage permits stdout redirection.

Commands such as `cd`, `export`, `mkdir`, `touch`, `rm`, `cp`, and `mv` are not
pipeline-safe. Stateful work uses `;` or `&&`, where ordered mutation is explicit.
The initial pipeline roles are:

| Role | Commands | Stdin behavior |
|---|---|---|
| Producer only | `pwd`, `ls`, `cat FILE...`, `echo`, `find` | May be the first stage; does not accept stdin |
| Transformer or consumer | `grep`, `head`, `tail`, `sort`, `uniq`, `wc` | May accept stdin after the first stage |

`cat` retains its version 1 requirement for one or more file arguments; pipeline support
does not implicitly add a no-argument stdin-copy mode. A later command-specific decision
may add that behavior if usability evidence justifies it.

The executor-owned final stdout redirection is the only permitted workspace mutation in
a pipeline. It remains one atomic workspace operation after the effective pipeline result
is successful.

This restriction avoids pretending that sequential stages have shell subprocess
isolation. For example, a naive sequential `cd /workspace/project | pwd` would otherwise
let `pwd` observe and potentially commit the earlier `cd`, while normal shell pipeline
stages generally do not share cwd or environment changes.

### Execution and output flow

Stages run one at a time in memory:

```text
stage 1 stdout -> stage 2 stdin -> ... -> final stage stdout
```

- Pipeline stdin and stdout are UTF-8 text.
- A stage runs to completion before the next stage begins.
- The first stage receives explicit empty stdin.
- Complete stage stdout becomes the next stage's explicit `CommandRequest.stdin`.
- Intermediate stdout is consumed by the next stage and is not added to model-visible
  aggregate stdout.
- Final-stage stdout becomes the pipeline stdout.
- Stderr from every executed stage remains separate from the pipe and is concatenated in
  stage order.
- A normal non-zero stage does not prevent later stages from consuming its stdout.
- The effective pipeline status uses fixed pipefail behavior: the rightmost non-zero
  stage status and failure code, or success when every stage succeeds.
- Executor, timeout, cancellation, and dependency failures abort the pipeline and the
  remaining execution plan immediately.

### Bounds and failure behavior

Pipelines add explicit limits for:

- stages per pipeline;
- complete bytes passed between any two stages;
- aggregate intermediate bytes materialized across the pipeline.

Intermediate output must be complete to preserve transformation correctness. If a stage
exceeds an intermediate or aggregate pipeline limit, the pipeline fails before the next
stage starts; truncated data is never supplied as stdin. This is a structured
`PIPELINE_LIMIT_EXCEEDED` non-zero pipeline result, not an executor exception. A following
`;` plan unit remains eligible, while a following `&&` plan unit is skipped.

Final model-visible stdout and stderr retain the normal complete-plan output limits and
observable truncation behavior. Final redirection still requires complete stdout and
leaves the target unchanged when output is unavailable in full.

The existing complete-plan timeout and cancellation scope covers every pipeline stage
and redirection boundary. Sequential buffering deliberately avoids concurrent-stage
backpressure, process cleanup, scheduling races, and unbounded queues.

## Initial command profile

Milestone 2 implements exactly these eight commands:

| Command | Version 1 behavior |
|---|---|
| `pwd` | Accept no arguments and emit the absolute cwd followed by LF |
| `cd PATH` | Require exactly one existing directory and return the new cwd |
| `ls [PATH]` | Accept zero or one path and emit stable lexical names, one per line |
| `cat FILE...` | Concatenate one or more UTF-8 files exactly, without inserted separators |
| `echo ARG...` | Join arguments with one space and append LF |
| `mkdir [-p] PATH...` | Create one or more directories, optionally including parents |
| `touch FILE...` | Create missing empty files; an existing file is a successful no-op |
| `rm [-rR] [-f] PATH...` | Remove files or directories with explicit recursive/force behavior |

`ls` does not hide dot-prefixed names or synthesize host metadata. `cat` fails normally
on invalid UTF-8. `touch` rejects directories because version 1 has no timestamp mutation.
Unknown options are argument failures. Multi-target commands fail fast; earlier successful
workspace mutations remain committed.

Commands are enabled through a capability profile. A framework adapter must not claim
support for commands that are not registered.

Additional utilities such as `head`, `tail`, `cp`, `mv`, `grep`, `find`, `wc`, `sort`,
`uniq`, `env`, `export`, `sh`, and `help` are deferred. The complete-core milestone adds
the useful search and aggregation command wave together with bounded sequential
in-memory pipelines under the approved semantics above, before the first framework
adapter.

OpenAI's default `SandboxAgent` shell sends `sh -lc` and expects additional Unix
utilities. The first in-memory adapter should use a custom capability rather than adding
fake POSIX compatibility to this component.

## Workspace ports

The command-executor module owns focused reader and mutator protocols for only the
operations used by registered commands. Handlers do not import or type against
`MemoryWorkspace`.

Append redirection requires one workspace-owned atomic append mutation. It must not be
implemented as an unguarded read followed by write. The Workspace module will add the
append request, quota, hash, revision, atomicity, and stale-state tests as part of issue
#4, with corresponding Workspace README updates.

## Execution context

The context contains:

- focused workspace reader and mutator ports
- immutable current cwd and approved environment state
- cancellation signal
- separate bounded stdout and stderr collectors
- operation and session identity
- approved secret leases, if required

Handlers return state transitions; they do not mutate a shared cwd or environment
dictionary. The executor applies successful transitions before evaluating the next
command.

Secret values are resolved before command dispatch by the session and are never added to
history or result metadata.

## Timeout and cancellation

- Timeout is measured over the complete execution plan.
- The default timeout is 30 seconds.
- Commands receive cooperative cancellation and may not create detached work.
- Cancellation is checked before and after each handler boundary.
- The executor checks its monotonic deadline before and after each awaited command or
  redirection boundary, so a handler that suppresses cancellation cannot return success
  or allow a later command to start after the deadline.
- Timeout raises `CommandTimeout`; cooperative request cancellation through
  `CommandExecutionContext` raises `CommandCancelled`.
- Native `asyncio` task cancellation propagates `asyncio.CancelledError` unchanged so
  `TaskGroup`, `wait_for`, and outer timeout scopes retain standard Python semantics.
- No command may continue mutating workspace state after timeout is reported.
- If a command implementation cannot stop safely, it is not accepted into the in-memory
  executor.
- The executor waits for cancellation cleanup before surfacing the terminal error.

## State mutation

Command sequencing is one session operation. Workspace mutations made by earlier commands
remain when a later command returns a normal non-zero exit code, matching documented
shell-style behavior. `;` always evaluates the next command, while `&&` evaluates it only
after exit code 0. The final result uses the status of the last command actually executed.

Outputs are concatenated exactly in execution order without inserting separators.
Commands emit their own conventional newlines.

On normal completion, including a non-zero final status, the result returns the current
cwd and environment changes for the session to commit. On timeout or cancellation, those
transient state changes are discarded because no result is returned. Already committed
workspace mutations remain, but no later command starts.

Parser failures before dispatch change nothing. Unexpected executor or collaborator
failures are surfaced explicitly and are never converted to successful-looking command
results.

The result clearly distinguishes command non-zero status from executor infrastructure
failure.

## Redirection

Version 1 allows one trailing stdout redirection only for `pwd`, `ls`, `cat`, and `echo`.
Restricting redirection to output-only commands prevents a redirect failure from being
coupled to an earlier mutation by the same simple command.

- `>` atomically creates or replaces the destination.
- `>>` atomically appends and creates the destination when absent.
- The destination resolves against the cwd active for that command.
- A non-zero command result leaves the destination unchanged.
- Successful redirection suppresses that command's stdout from the aggregate result.
- stderr is never redirected.
- If complete stdout was not retained because of the output limit, redirection fails
  normally and leaves the destination unchanged.
- Workspace path, target-type, quota, and append failures use exit code 1 with a
  structured redirection failure code.

## Output behavior

- stdout and stderr each have an independent 256 KiB default UTF-8 byte limit over the
  complete execution plan.
- Collectors preserve complete UTF-8 code points, retain the bounded prefix, count the
  original emitted bytes, and record separate truncation flags.
- Normal unredirected overflow does not change the command exit status.
- Redirected stdout overflow fails instead of writing partial output.
- Redaction occurs before model-visible output and events.
- stdout and stderr remain separate domain fields.
- Adapters decide how to format them for a framework.

## Failure semantics

Typed executor errors include:

- `CommandEmpty`
- `CommandTooLong`
- `CommandSyntaxInvalid`
- `CommandSyntaxUnsupported`
- `CommandTimeout`
- `CommandCancelled`
- `CommandInternalFailure`

Normal command failures remain structured results:

| Failure | Exit code |
|---|---:|
| Command not registered | 127 |
| Invalid option or argument | 2 |
| Workspace operation failure | 1 |
| Redirection or redirected-output-limit failure | 1 |
| Pipeline intermediate or aggregate limit failure | 1 |

The result includes a stable failure code in addition to deterministic stderr. This lets
`;` and `&&` evaluate failures without exception-driven control flow. Policy denial is a
session concern and occurs before invoking the executor. Milestone 4 adds the stable
`PIPELINE_LIMIT_EXCEEDED` failure code for a bounded pipeline that cannot retain complete
intermediate data.

## Resource limits

The configurable default profile is:

| Limit | Default |
|---|---:|
| Command text | 32 KiB UTF-8 |
| argv entries per simple command | 256 |
| Expanded argument | 8 KiB UTF-8 |
| stdout | 256 KiB UTF-8 |
| stderr | 256 KiB UTF-8 |
| Complete execution plan | 30 seconds |

The static argv-entry count is checked for the complete plan before dispatch because
expansion never performs word splitting. Expanded UTF-8 argument and redirection-target
sizes are checked immediately before each command and produce a structured invalid-
argument result, allowing `;` and `&&` to retain normal sequencing semantics. Empty,
non-finite, zero, or negative timeout and limit values are invalid requests.

## Test expectations

- Parse quoting, expansion, sequencing, and redirection boundaries.
- Reject every unsupported syntax family.
- Verify each command through fake workspace ports.
- Cover happy, non-zero, timeout, cancellation, and dependency-failure paths.
- Confirm no post-timeout mutations.
- Confirm deterministic output truncation and redaction.
- Verify cwd and environment commit rules.
- Verify append is one atomic workspace mutation rather than read-then-write.
- Verify redirected output overflow and command failure leave the target unchanged.
- For Milestone 4 pipelines, verify precedence, pipeline-safe command admission, fixed
  pipefail status, producer and stdin-consumer roles, ordered stderr, final-only stdout,
  descriptor-gated final-stage-only redirection, and exact intermediate and aggregate
  byte boundaries.
- Confirm an intermediate overflow never supplies truncated stdin or starts the next
  stage, remains a structured non-zero result, permits following `;` execution, and
  prevents following `&&` execution.
- Run the same script before and after snapshot restore and compare results.
- Verify duplicate and invalid command registrations fail fast.

## Maintenance rule

Changes to grammar, command registration, execution state, timeout behavior, or output
semantics require an update to this document.
