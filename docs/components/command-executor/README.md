# Command Executor Design

**Status:** Proposed detailed design under the approved high-level architecture

## Purpose

The command executor runs a constrained, deterministic command language over the virtual
workspace. It is not a wrapper around the host shell.

The executor owns command parsing, dispatch, working-directory and environment behavior,
timeouts, output limits, and command result semantics.

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

## Public contract

```python
@dataclass(frozen=True)
class ExecuteRequest:
    command: str
    cwd: SandboxPath
    environment: Mapping[str, str]
    timeout_seconds: float
    max_output_bytes: int


@dataclass(frozen=True)
class ExecuteResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    output_truncated: bool
    resulting_cwd: SandboxPath
    environment_changes: Mapping[str, str | None]


class CommandExecutor(Protocol):
    async def execute(
        self,
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult: ...
```

The session commits `resulting_cwd` and environment changes only when execution reaches a
defined completion state.

## Command extension contract

Each command has one responsibility:

```python
class VirtualCommand(Protocol):
    @property
    def name(self) -> str: ...

    async def execute(
        self,
        request: CommandRequest,
        context: CommandContext,
    ) -> CommandResult: ...
```

Commands receive only required capabilities, such as `CommandWorkspaceReader` or
`CommandWorkspaceMutator`. They do not receive the concrete session or service.

Duplicate command names fail executor construction.

## Grammar

Version 1 may support:

- shell-style whitespace and quoting
- environment expansion for approved variables
- command sequencing with `;`
- success sequencing with `&&`
- output replacement with `>`
- output append with `>>`
- script execution through the same parser and registry

Version 1 rejects:

- pipelines
- `||`
- input redirection and heredocs
- background jobs
- command substitution
- process substitution
- host executable lookup
- implicit fallback to `cmd.exe`, PowerShell, Bash, or `/bin/sh`

Unsupported syntax returns `CommandSyntaxUnsupported`, not an approximation.

## Initial command profile

The first useful profile should cover workspace manipulation and inspection:

```text
pwd, cd, ls
cat, head, tail
mkdir, touch, rm, cp, mv
echo, grep, find, wc
env, export
sh, help
```

Commands are enabled through a capability profile. A framework adapter must not claim
support for commands that are not registered.

OpenAI's default `SandboxAgent` shell sends `sh -lc` and expects additional Unix
utilities. The first in-memory adapter should use a custom capability rather than adding
fake POSIX compatibility to this component.

## Execution context

The context contains:

- focused workspace reader and mutator ports
- normalized starting cwd
- approved environment values
- cancellation signal
- output collector with hard limits
- operation and session identity
- approved secret leases, if required

Secret values are resolved before command dispatch by the session and are never added to
history or result metadata.

## Timeout and cancellation

- Timeout is measured over the complete execution plan.
- Commands receive cooperative cancellation.
- A timeout returns or raises one deterministic timeout outcome.
- No command may continue mutating workspace state after timeout is reported.
- If a command implementation cannot stop safely, it is not accepted into the in-memory
  executor.

## State mutation

Command sequencing is one session operation. Workspace mutations made by earlier commands
remain when a later command returns a normal non-zero exit code, matching documented
shell-style behavior. Parser, policy, timeout, or internal failures before dispatch change
nothing.

The result clearly distinguishes command non-zero status from executor infrastructure
failure.

## Output behavior

- Commands write bytes or text through a bounded collector.
- Truncation is deterministic and recorded in the result.
- Redaction occurs before model-visible output and events.
- stdout and stderr remain separate domain fields.
- Adapters decide how to format them for a framework.

## Failure semantics

Stable errors include:

- `CommandEmpty`
- `CommandTooLong`
- `CommandSyntaxInvalid`
- `CommandSyntaxUnsupported`
- `CommandNotFound`
- `CommandArgumentInvalid`
- `CommandTimeout`
- `CommandCancelled`
- `CommandOutputLimitExceeded`
- `CommandPolicyDenied`
- `CommandInternalFailure`

A normal command failure uses a non-zero exit code. Infrastructure and policy failures
use domain errors.

## Test expectations

- Parse quoting, expansion, sequencing, and redirection boundaries.
- Reject every unsupported syntax family.
- Verify each command through fake workspace ports.
- Cover happy, non-zero, policy-blocked, timeout, and dependency-failure paths.
- Confirm no post-timeout mutations.
- Confirm deterministic output truncation and redaction.
- Verify cwd and environment commit rules.
- Run the same script before and after snapshot restore and compare results.
- Verify duplicate and invalid command registrations fail fast.

## Maintenance rule

Changes to grammar, command registration, execution state, timeout behavior, or output
semantics require an update to this document.
