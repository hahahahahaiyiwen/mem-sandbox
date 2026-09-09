# Command Executor Design

**Status:** Command profile and issue #20 protected-overlay behavior implemented;
external-resource command direction approved in issue #49

## Purpose

The command executor runs a constrained, deterministic command language over the virtual
workspace. It is not a wrapper around the host shell.

The executor owns command parsing, dispatch, working-directory and environment behavior,
timeouts, output limits, and command result semantics.

The framework-neutral implementation is in `src/mem_sandbox/command_executor`. Its
default factory constructor injects the workspace reader and mutator ports into the
complete command profile and injects an immutable registry into the executor. The
historical `create_first_wave_commands()` factory remains limited to the original eight
Milestone 2 commands; `create_command_profile()` constructs the complete profile.

## Responsibilities

- Parse command text into a validated execution plan.
- Resolve commands from an explicit registry.
- Execute commands against narrow workspace ports.
- Maintain command-local cwd and approved environment state.
- Support deterministic sequencing and output redirection.
- Apply command, argument, timeout, and output limits.
- Consume a typed operation-local environment overlay without persisting it.
- Redact protected output and reject protected persistence before workspace mutation.
- Cooperate with cancellation.
- Return structured stdout, stderr, exit code, duration, and truncation metadata.
- Reject unsupported shell syntax explicitly.
- Match familiar POSIX/Bash behavior for every supported command unless a documented
  deterministic-sandbox constraint requires a deviation.

## Out of scope

- Arbitrary host subprocess execution.
- Importing and running arbitrary Python in process.
- Filesystem storage implementation.
- Secret policy decisions.
- Agent framework tools.
- A complete POSIX shell, process model, or utility collection.

## POSIX compatibility target

The executor is not advertised as Bash, `/bin/sh`, or a complete POSIX environment.
However, LLM agents are trained on standard terminal behavior, so every command that is
registered should match familiar GNU/POSIX/Bash syntax, operand handling, output, and
exit status as closely as the in-memory model permits.

Deviations require a concrete reason and explicit documentation. Approved reasons
include:

- no host processes or concurrent pipeline stages;
- deterministic behavior across Windows and Linux;
- finite command, intermediate-output, aggregate-output, and timeout limits;
- atomic virtual-workspace publication;
- no mutation after timeout, cancellation, or infrastructure failure;
- operation-scoped secret non-persistence and mandatory external-output redaction;
- missing virtual metadata such as owners, permissions, links, and timestamps;
- inability to provide atomic recursive directory merging.

Capability descriptions must expose the supported subset and must not call it a generic
shell. Compatibility-sensitive behavior is included in the executable multi-model
evaluation before the first framework adapter.

The cross-model evidence and roadmap implications for pipelines, scripts, and structured
file tools are recorded in
[Command Composition Usability Pilot](./PIPELINE_EVALUATION.md).
The executable Milestone 4 corpus, runner, transcripts, and measurements are in
[`evaluations/command_usability`](../../../evaluations/command_usability/README.md).

The parser dependency comparison and decision to continue with the constrained parser
are recorded in
[OSS POSIX and Bash Parser Evaluation](../../Python/POSIX_PARSER_EVALUATION.md).

## Public contract

```python
@dataclass(frozen=True)
class CommandLimits:
    max_command_bytes: int = 32 * 1024
    max_argv_entries: int = 256
    max_argument_bytes: int = 8 * 1024
    max_stdout_bytes: int = 256 * 1024
    max_stderr_bytes: int = 256 * 1024
    max_pipeline_stages: int = 8
    max_pipeline_intermediate_bytes: int = 256 * 1024
    max_pipeline_aggregate_bytes: int = 1024 * 1024
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
3. Parse the complete input into immutable plan units and stages before dispatch.
4. Start one complete-plan timeout scope.
5. Before a pipeline starts, expand and validate all stages against the current
   execution state, immutable registry, capability profile, and effective limits.
6. Invoke each eligible focused handler through command-owned workspace ports.
7. Pass complete bounded stdout between connected pipeline stages.
8. Apply permitted final stdout redirection atomically after any normal command or
   pipeline result, including a non-zero result.
9. Advance explicit cwd or environment state for successful non-pipeline commands and
   aggregate visible output.
10. Return the effective unit status and final normal-completion state.

Parsing the complete plan first ensures malformed or unsupported syntax cannot execute a
prefix of the input.

## Parser ownership

Tokenization and syntax parsing are stateless capabilities owned by
`mem_sandbox.command_executor`. The current implementation uses pure `tokenize()` and
`parse_execution_plan()` functions, so no parser instance or mutable parser state exists
inside `SandboxSession`.

An immutable parser object may be constructor-injected and shared across many executors,
but it remains a command-executor dependency. `SandboxService` may arrange that sharing
through its session factory; it must not become the owner of command syntax or parse
commands itself.

Issue #16 continues with the current constrained parser and adds no runtime parser
dependency. `tree-sitter-bash` remains the only candidate for a future measured spike if
the accepted grammar or unsupported-syntax recognition burden expands materially.

The parser recognizes syntax only. Registry resolution, capability-profile admission,
environment expansion, pipeline-safety validation, and effective limits remain
executor-owned. Future command policy consumes an immutable prepared-plan summary from
the executor boundary so the session and policy engine do not reparse command text.

## Command extension contract

Each command has one responsibility:

```python
@dataclass(frozen=True)
class CommandRequest:
    argv: tuple[str, ...]
    stdin: str
    stdin_connected: bool = False


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

`CommandDescriptor` owns the case-sensitive name, explicit aliases, summary, usage,
whether the command permits stdout redirection or accepts stdin, and an additive
`pipeline_safe` flag that defaults to `False`. Duplicate names or aliases fail executor
construction. The registry is immutable after construction and is the only source for
agent-facing command descriptions. The default profile defines no aliases.

`stdin` is always explicit. `stdin_connected` distinguishes an unconnected command from a
pipeline stage whose upstream command produced an empty string. Ordinary commands and
first pipeline stages receive `stdin=""` and `stdin_connected=False`; later stages
receive complete upstream stdout and `stdin_connected=True`.

Commands use familiar operand behavior: no file operand consumes stdin, and `-`
explicitly selects stdin among file operands. A pipeline-safe command may therefore
consume connected stdin, named files, or an explicit `-` according to its documented
POSIX-shaped contract.

### Future external-resource commands

The immutable registry is the extension seam for future network and Python commands,
but it is not a plugin security boundary. Extension implementations loaded into the
MemSandbox process are trusted application code and are selected by the host.

A future external-resource command:

- is registered only when the immutable sandbox profile grants its required resource;
- receives a narrow resource port through constructor injection and operation context;
- never creates a private HTTP client, resolver, host subprocess, or host path;
- never calls a public `SandboxSession` method from inside the current `execute`
  operation;
- shares normalized behavior with any typed session/tool adapter over the same resource
  service;
- declares exact supported syntax and does not imply a broader host command.

The planned HTTP command delegates to the
[controlled network gateway](../network-egress/README.md), never a host `curl`
executable. The planned `python` command delegates to the
[external execution coordinator](../external-execution/README.md), never an in-process
interpreter or directly managed host process.

Future descriptor metadata may declare required resource kinds and profile compatibility.
Registry construction rejects a command when its grant or collaborator is absent. The
current default registry and model-facing descriptions remain unchanged.

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
- higher-precedence bounded pipelines with `|`
- one trailing stdout replacement with `>`
- one trailing stdout append with `>>`

Operators are recognized only outside quotes. Empty quoted arguments are preserved.
Expansion occurs immediately before each command is dispatched, so it observes the
current explicit execution state. Undefined approved variables expand to an empty string.
Expansion never causes word splitting, command lookup, or glob expansion.

`PWD` is derived from the current execution cwd. `export` and `unset` return explicit
environment changes that the executor applies between eligible plan units.

Version 1 rejects or does not interpret:

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

Unsupported syntax returns `CommandSyntaxUnsupported`, not an approximation. Pipeline
syntax composes immutable command stages through explicit stdin and stdout; it does not
invoke host processes or run stages concurrently.

## Approved Milestone 4 pipeline semantics

**Status:** Implemented by issue #16.

> Pipelines are bounded text transformations between registered virtual commands, not
> host-shell execution.

Pipelines extend the constrained execution-plan grammar without introducing a host shell,
host processes, or POSIX process scheduling. Their user-visible command behavior remains
POSIX-shaped where the sequential in-memory model can reproduce it.

### Grammar and plan structure

- `|` binds more tightly than `&&` and `;`. Connectors evaluate the effective result of
  the complete pipeline.
- Syntax parsing produces immutable `CommandStage`, `PlanUnit`, and `ExecutionPlan`
  values. A simple command is a one-stage plan unit.
- The executor validates the parsed plan against the registry, capability profile, and
  effective limits before dispatch. A configurable maximum stage count is validated
  before any stage runs.
- One `>` or `>>` redirection may appear only after the final stage and applies to the
  complete pipeline result. A resolved final command descriptor must permit stdout
  redirection; normal command-not-found status is the documented descriptor-free
  exception. Redirection on an intermediate stage is rejected before dispatch.
- Input redirection, heredocs, background jobs, command substitution, process
  substitution, and implicit shell behavior remain unsupported.

### Eligible commands and state

The immutable command descriptor gains an explicit `pipeline_safe` declaration, default
`False`, in addition to stdin acceptance. Before the first stage runs, the executor
expands and resolves every stage against the current execution state and verifies that:

- every command is registered and marked pipeline-safe;
- every stage after the first accepts stdin;
- expanded arguments satisfy their limits;
- a resolved redirected final stage permits stdout redirection.

Commands such as `cd`, `export`, `mkdir`, `touch`, `rm`, `cp`, and `mv` are not
pipeline-safe. Stateful work uses `;` or `&&`, where ordered mutation is explicit.
The initial pipeline roles are:

| Role | Commands | Stdin behavior |
|---|---|---|
| Producer only | `pwd`, `ls`, `echo`, `find` | May be the first stage; does not accept connected stdin |
| File/stdin producer or transformer | `cat`, `grep`, `head`, `tail`, `sort`, `uniq`, `wc` | Uses named operands, explicit `-`, or connected stdin according to its command contract |
| Environment producer | `env` | May be the first stage and does not accept connected stdin |

`cat` with no file operands consumes stdin. An explicit `-` selects stdin among file
operands for commands that support file input. `stdin_connected` remains true even when
an upstream stage emitted no bytes, avoiding ambiguity between no pipe and an empty pipe.

The executor-owned final stdout redirection is the only permitted workspace mutation in
a pipeline. It remains one atomic workspace operation after the pipeline reaches any
normal result, including a non-zero result. The effective pipeline exit status is
preserved after successful redirection. Timeout, cancellation, infrastructure failure,
incomplete output, invalid destinations, and resource-limit failures leave the target
unchanged.

This restriction avoids pretending that sequential stages have shell subprocess
isolation. For example, a naive sequential `cd /workspace/project | pwd` would otherwise
let `pwd` observe and potentially commit the earlier `cd`, while normal shell pipeline
stages generally do not share cwd or environment changes.

`pipeline_safe` is a trusted extension assertion. Default safe commands receive only
read-only or no workspace ports. The executor treats an unexpected cwd or environment
change from a safe handler as `CommandInternalFailure`. A custom handler that falsely
declares itself safe and mutates through an undeclared external capability violates the
extension contract; the executor cannot roll back behavior outside its ports.

### Execution and output flow

Stages run one at a time in memory:

```text
stage 1 stdout -> stage 2 stdin -> ... -> final stage stdout
```

- Pipeline stdin and stdout are UTF-8 text.
- A stage runs to completion before the next stage begins.
- The first stage receives `stdin=""` with `stdin_connected=False`.
- Complete stage stdout becomes the next stage's explicit `CommandRequest.stdin`.
- Every later stage receives `stdin_connected=True`, including when upstream stdout is
  empty.
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
- Final redirection uses the final stage stdout for every normal pipeline status. A
  redirection failure becomes the effective unit failure.

### Bounds and failure behavior

Pipelines add explicit limits for:

- stages per pipeline, default 8;
- complete bytes passed between any two stages, default 256 KiB;
- aggregate intermediate bytes materialized across the pipeline, default 1 MiB.

Intermediate output must be complete to preserve transformation correctness. If a stage
exceeds an intermediate or aggregate pipeline limit, the pipeline fails before the next
stage starts; truncated data is never supplied as stdin. This is a structured
`PIPELINE_LIMIT_EXCEEDED` non-zero pipeline result, not an executor exception. A following
`;` plan unit remains eligible, while a following `&&` plan unit is skipped.

Aggregate intermediate bytes are the sum of every complete inter-stage stdout payload;
final stdout and stderr are excluded. Final model-visible stdout and stderr retain the
normal complete-plan output limits and observable truncation behavior. Final redirection
still requires complete stdout and leaves the target unchanged when output is unavailable
in full.

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

## Milestone 4 command profile

Supported commands follow their familiar POSIX-shaped operand behavior within this
explicit subset:

| Command | Approved behavior |
|---|---|
| `cat [FILE...]` | Concatenate UTF-8 files; no operands consume stdin; `-` selects stdin |
| `echo [-n] [ARG...]` | Join arguments with spaces and optionally omit trailing LF |
| `ls [-1a] [PATH...]` | List one or more paths; `-1` is the stable one-entry-per-line form and `-a` is accepted because dot names are never hidden |
| `head [-n COUNT] [FILE...]` | Emit leading LF-delimited records; no files consume stdin and `-` selects stdin |
| `tail [-n COUNT] [FILE...]` | Emit trailing LF-delimited records; no files consume stdin and `-` selects stdin |
| `grep [-FEivnlrR] PATTERN [FILE...]` | Search Python regular expressions or fixed text; recursive mode defaults to cwd; no files consume stdin; no match is exit 1 with `NO_MATCH` |
| `find [PATH] [-type f\|d] [-name GLOB] [-maxdepth N]` | Stable lexical depth-first traversal; `fnmatch.fnmatchcase` basename matching; output follows the supplied relative or absolute path style |
| `wc [-clw] [FILE...]` | Count exact UTF-8 bytes, LF characters, and words; no files consume stdin and `-` selects stdin |
| `sort [-nru] [FILE...]` | Stable locale-independent line sorting with numeric, reverse, and unique modes |
| `uniq [-c] [FILE]` | Filter adjacent equal lines and optionally prefix counts |
| `cp [-fRr] SOURCE... DEST` | Copy one or more sources; directory sources require recursion; existing directory destinations append basenames; compatible files overwrite atomically; `-f` is accepted because compatible overwrite is already the default |
| `mv [-f] SOURCE... DEST` | Move one or more sources; existing directory destinations append basenames; compatible files overwrite atomically; `-f` is accepted because compatible overwrite is already the default |
| `env` | Emit the sorted approved environment plus derived `PWD` |
| `export NAME=VALUE...` | Return persistent environment assignments for the current execution plan |
| `unset NAME...` | Return persistent environment removals; derived `PWD` cannot be unset |

Commands with options accept `--` where needed to terminate option parsing. `head` and
`tail` default to ten records. LF is the record delimiter; CR bytes and missing final
newlines are preserved where the corresponding utility normally preserves input.
`wc -l` counts LF characters and `wc -c` counts exact UTF-8 bytes.

Multiple source or file operands follow familiar order and fail-fast behavior. Earlier
successful workspace mutations remain committed. `cp` and `mv` do not create missing
parents. Recursive copy into an already-existing destination subtree is rejected because
the workspace does not currently expose an atomic POSIX directory-merge operation.

`ls -l` is explicitly unsupported because the virtual workspace has no meaningful owner,
group, permission, link-count, or timestamp fields. `env NAME=VALUE COMMAND`, full shell
assignment prefixes, POSIX BRE, locale-dependent sorting, shell glob expansion, and
script-file execution remain outside this milestone. `env` command invocation may be
revisited with the operation-local environment-overlay boundary used by secret leasing.

OpenAI's default `SandboxAgent` shell sends `sh -lc` and expects additional Unix
utilities. The first in-memory adapter should use a custom capability rather than adding
host-shell behavior to this component.

## Workspace ports

The command-executor module owns focused reader and mutator protocols for only the
operations used by registered commands. Handlers do not import or type against
`MemoryWorkspace`.

Append redirection uses the workspace-owned atomic append mutation and must never become
an unguarded read followed by write. The mutator port includes the workspace-owned atomic
copy and move operations; command handlers retain ownership only of POSIX-shaped operand
and destination interpretation.

## Execution context

The context contains:

- focused workspace reader and mutator ports
- immutable current cwd and approved environment state
- cancellation signal
- separate bounded stdout and stderr collectors
- operation and session identity
- one operation-local protected-value guard, when required

Handlers return state transitions; they do not mutate a shared cwd or environment
dictionary. The executor applies successful transitions before evaluating the next
command.

Secret values are resolved before command dispatch by the session. The executor receives
a separate `CommandEnvironmentOverlay`; it never receives a merged persistent
`CommandEnvironment`.

## Protected environment overlay

The command-executor module owns the overlay and protection contracts:

```python
class CommandOverlayValue(Protocol):
    def reveal_text(self) -> str: ...


@dataclass(frozen=True, slots=True, repr=False)
class CommandEnvironmentOverlayEntry:
    name: str
    value: CommandOverlayValue


@dataclass(frozen=True, slots=True, repr=False)
class CommandEnvironmentOverlay:
    entries: tuple[CommandEnvironmentOverlayEntry, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class CommandEnvironmentViewEntry:
    name: str
    value: str


@dataclass(frozen=True, slots=True, repr=False)
class CommandEnvironmentView:
    base: CommandEnvironment
    overlay: CommandEnvironmentOverlay

    def get(self, name: str, default: str = "") -> str: ...
    @property
    def values(self) -> tuple[CommandEnvironmentViewEntry, ...]: ...


class CommandValueProtection(Protocol):
    def redact_text(self, value: str) -> str: ...
    def redact_bytes(self, value: bytes) -> bytes: ...
    def contains_protected_text(self, value: str) -> bool: ...
```

`ExecuteRequest` keeps its approved base environment and gains the separate overlay.
`CommandExecutionContext` gains the operation-local protection port. Empty overlay and
protection implementations preserve current direct-executor behavior.

The executor constructs `CommandEnvironmentView` and supplies it to both parser
expansion and `CommandContext`. `env` therefore sees overlay names internally, but the
stage output is redacted before it reaches a pipeline, redirection, collector, or caller.
View entries use non-revealing representations even after overlay values are
materialized.

Generic command dispatch rejects any expanded command name, argument, or redirection
destination containing a protected value before command resolution or handler
invocation. Exact output redaction alone cannot prevent a command from exposing derived
facts through match results, option parsing, exit status, or `&&` control flow. A future
secret-consuming command must therefore be an explicitly registered trusted handler
that reads a typed value from `CommandContext.environment`; the built-in command profile
provides no such destination-specific capability.

Overlay rules:

- names use the existing command environment grammar;
- `PWD` is always derived and cannot be overlaid;
- duplicate names are invalid;
- overlay lookup shadows the base environment and command-local `export`/`unset`
  transitions for the entire execution plan;
- each lookup reaches the overlay value provider so lease close/expiry remains
  enforceable;
- overlay entries are never included in `ExecuteResult.environment_changes`.
- `export` and `unset` targeting an overlaid name are rejected so a shadowed persistent
  base value cannot change invisibly.

`CommandContext`, `CommandRequest`, internal `CommandResult`, `EnvironmentChange`,
expanded stages, prepared units, overlays, and value providers that may contain resolved
material use non-revealing representations. The externally returned `ExecuteResult`
contains only already-sanitized values. The executor retains no command history or
prepared plan after completion.

## Protected output and persistence

Every command stage is sanitized immediately after handler return and before its data can
cross another boundary:

```text
handler result
  -> redact stdout/stderr
  -> reject protected persistent environment changes
  -> forward redacted pipeline data or apply guarded redirection
  -> append redacted bounded output
```

This ordering ensures:

- later pipeline stages never receive raw protected output;
- redirected content is already redacted;
- output collectors and results never retain raw protected text;
- output byte/truncation metadata describes only the redacted collector input;
- generated command diagnostics are redacted before result or exception publication.

The executor rejects protected values in:

- expanded command names and arguments before dispatch;
- persistent `EnvironmentChange` values;
- stdout-redirection destinations;
- workspace path operands identified by each command handler;
- cwd transitions.

Handlers own operand meaning and must run the command-owned protection guard before
resolving a path or invoking a workspace port. A rejected persistence attempt returns
`CommandFailureCode.PROTECTED_VALUE_REJECTED` and performs no corresponding workspace or
session-state mutation. The executor does not silently discard changes or report success
after substituting a redaction marker.

Exact redaction does not infer encoded or derived forms. Pre-dispatch rejection prevents
the built-in profile from using a protected argument as a semantic oracle, while
redaction before pipeline forwarding prevents a trusted handler's output from reaching a
later stage in raw form.

For secret-bearing operations, output byte counts and truncation flags describe the
redacted stream accepted by the collectors, not the raw secret-bearing stream. This
avoids exposing secret length through result metadata.

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

Milestone 2 allowed one trailing stdout redirection only for `pwd`, `ls`, `cat`, and
`echo`. The current profile also permits standalone and final-pipeline-stage redirection
for the non-mutating output commands `head`, `tail`, `grep`, `find`, `wc`, `sort`,
`uniq`, and `env`. Mutating commands remain ineligible so a redirect failure cannot be
coupled to an earlier mutation by the same simple command.

- `>` atomically creates or replaces the destination.
- `>>` atomically appends and creates the destination when absent.
- The destination resolves against the cwd active for that command.
- Every normal command or pipeline result, including exit codes 1, 2, and 127, redirects
  its captured stdout while retaining the original status when the write succeeds. This
  intentionally supersedes the Milestone 2 exit-code-zero-only rule.
- Command lookup failure is a normal exit-127 result. When the final command has no
  descriptor because it is unknown, that result remains redirection-eligible as the
  POSIX-shaped exception to descriptor gating; the atomic redirect publishes empty
  stdout after pipeline preflight returns without dispatching any stage.
- Completed redirection suppresses that command or pipeline stdout from the aggregate
  result.
- stderr is never redirected.
- If complete stdout was not retained because of the output limit, redirection fails
  normally and leaves the destination unchanged.
- Workspace path, target-type, quota, and append failures use exit code 1 with a
  structured redirection failure code.
- Syntax errors, timeout, cancellation, and infrastructure failures occur outside normal
  command completion and leave the destination unchanged. This is an intentional atomic
  safety deviation from a host shell opening the target before process execution.

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
| `grep` found no matching input | 1 |
| Workspace operation failure | 1 |
| Redirection or redirected-output-limit failure | 1 |
| Pipeline intermediate or aggregate limit failure | 1 |
| Protected value would enter persistent environment, cwd, path, or file state | 1 |

The result includes a stable failure code in addition to deterministic stderr. This lets
`;` and `&&` evaluate failures without exception-driven control flow. Policy denial is a
session concern and occurs before invoking the executor. Milestone 4 adds the stable
`NO_MATCH` code for normal grep no-match results and `PIPELINE_LIMIT_EXCEEDED` for a
bounded pipeline that cannot retain complete intermediate data.
Issue #20 adds `PROTECTED_VALUE_REJECTED` for a normal fail-before-persistence result.

## Resource limits

The configurable default profile is:

| Limit | Default |
|---|---:|
| Command text | 32 KiB UTF-8 |
| argv entries per simple command | 256 |
| Expanded argument | 8 KiB UTF-8 |
| stdout | 256 KiB UTF-8 |
| stderr | 256 KiB UTF-8 |
| Pipeline stages | 8 |
| Intermediate pipeline payload | 256 KiB UTF-8 |
| Aggregate intermediate materialization | 1 MiB UTF-8 |
| Secret environment bindings | 16 |
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
- Confirm secret overlay values are visible to expansion and explicitly trusted handlers
  but absent from every result, diagnostic, and useful object representation.
- Confirm every protected expanded command name, argument, and redirection destination
  fails before dispatch, independent of its content or potential command semantics.
- Confirm stage output is redacted before pipeline forwarding and redirection.
- Confirm protected `export`, cwd, path operands, and redirection destinations fail
  before persistence with `PROTECTED_VALUE_REJECTED`.
- Confirm an empty overlay preserves all existing command behavior and representations.
- Verify cwd and environment commit rules.
- Verify append is one atomic workspace mutation rather than read-then-write.
- Verify normal non-zero command and pipeline results redirect captured stdout while
  preserving their non-zero status.
- Verify timeout, cancellation, infrastructure failure, invalid destination, quota
  failure, and redirected-output overflow leave the target unchanged.
- For Milestone 4 pipelines, verify precedence, pipeline-safe command admission, fixed
  pipefail status, producer and stdin-consumer roles, ordered stderr, final-only stdout,
  descriptor-gated final-stage-only redirection, and exact intermediate and aggregate
  byte boundaries.
- Confirm an intermediate overflow never supplies truncated stdin or starts the next
  stage, remains a structured non-zero result, permits following `;` execution, and
  prevents following `&&` execution.
- Run the same script before and after snapshot restore and compare results.
- Run the bounded generated grammar, pipeline, limit, redirection, and twin-execution
  models defined in
  [Property and Stateful Test Design](../../../tests/property/README.md).
- Verify duplicate and invalid command registrations fail fast.
- Compare supported command behavior with POSIX-shaped golden cases and document every
  intentional compatibility deviation.

## Maintenance rule

Changes to grammar, command registration, execution state, timeout behavior, or output
semantics require an update to this document.
