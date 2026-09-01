"""Complete-plan orchestration for the constrained virtual command language."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from mem_sandbox.command_executor.commands import create_first_wave_commands
from mem_sandbox.command_executor.errors import (
    CommandCancelled,
    CommandInternalFailure,
    CommandSyntaxInvalid,
    CommandTimeout,
    CommandTooLong,
)
from mem_sandbox.command_executor.models import (
    CommandContext,
    CommandEnvironment,
    CommandExecutionContext,
    CommandFailureCode,
    CommandRequest,
    CommandResult,
    EnvironmentChange,
    EnvironmentValue,
    ExecuteRequest,
    ExecuteResult,
    PlanCommand,
    RedirectionMode,
)
from mem_sandbox.command_executor.output import BoundedOutputCollector, bound_text
from mem_sandbox.command_executor.parser import expand_word, parse_execution_plan
from mem_sandbox.command_executor.ports import CommandWorkspaceMutator, CommandWorkspaceReader
from mem_sandbox.command_executor.registry import CommandRegistry
from mem_sandbox.core.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    QuotaExceededError,
    SandboxError,
    UnsupportedOperationError,
)
from mem_sandbox.workspace import (
    AnyCurrentState,
    SandboxPath,
    WorkspaceAppendRequest,
    WorkspaceWriteRequest,
)

_EXPECTED_WORKSPACE_FAILURES = (
    InvalidRequestError,
    NotFoundError,
    ConflictError,
    QuotaExceededError,
    UnsupportedOperationError,
)


class CommandExecutor(Protocol):
    """Framework-neutral async command execution boundary."""

    async def execute(
        self,
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult: ...


class VirtualCommandExecutor:
    """Execute immutable plans through an injected command registry and workspace port."""

    def __init__(
        self,
        registry: CommandRegistry,
        redirection_mutator: CommandWorkspaceMutator,
    ) -> None:
        self._registry = registry
        self._redirection_mutator = redirection_mutator

    @property
    def registry(self) -> CommandRegistry:
        """Expose immutable registry metadata for adapter descriptions."""
        return self._registry

    async def execute(
        self,
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult:
        """Parse the complete plan, then execute it under one timeout scope."""
        try:
            command_bytes = len(request.command.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise CommandSyntaxInvalid("command text must be valid UTF-8") from error
        if command_bytes > request.limits.max_command_bytes:
            raise CommandTooLong(
                f"command contains {command_bytes} bytes; limit is "
                f"{request.limits.max_command_bytes} bytes"
            )
        plan = parse_execution_plan(request.command)
        self._validate_plan_argument_counts(plan.commands, request)
        started = time.perf_counter()
        deadline = asyncio.get_running_loop().time() + request.limits.timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                result = await self._execute_plan(request, context, plan.commands, deadline)
        except TimeoutError as error:
            raise CommandTimeout("command execution exceeded the complete-plan timeout") from error
        duration_ms = (time.perf_counter() - started) * 1000
        return ExecuteResult(
            exit_code=result.exit_code,
            failure_code=result.failure_code,
            stdout=result.stdout,
            stderr=result.stderr,
            stdout_original_bytes=result.stdout_original_bytes,
            stderr_original_bytes=result.stderr_original_bytes,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            duration_ms=duration_ms,
            resulting_cwd=result.resulting_cwd,
            environment_changes=result.environment_changes,
        )

    async def _execute_plan(
        self,
        request: ExecuteRequest,
        execution_context: CommandExecutionContext,
        commands: tuple[PlanCommand, ...],
        deadline: float,
    ) -> ExecuteResult:
        stdout = BoundedOutputCollector(request.limits.max_stdout_bytes)
        stderr = BoundedOutputCollector(request.limits.max_stderr_bytes)
        cwd: SandboxPath = request.cwd
        environment = request.environment
        all_changes: list[EnvironmentChange] = []
        last_result = CommandResult.success()
        last_exit = 0
        executed = False

        for item in commands:
            if item.connector.value == "on_success" and last_exit != 0:
                continue
            self._check_execution_state(execution_context, deadline)
            argv = tuple(expand_word(word, environment, cwd) for word in item.words)
            destination = (
                expand_word(item.redirection.destination, environment, cwd)
                if item.redirection is not None
                else None
            )
            argument_error = self._expanded_argument_error(argv, destination, request)
            if argument_error is not None:
                stage = CommandResult(
                    2,
                    CommandFailureCode.INVALID_ARGUMENT,
                    "",
                    f"{self._diagnostic_command_name(argv[0])}: {argument_error}\n",
                )
            else:
                command = self._registry.resolve(argv[0])
                if command is None:
                    stage = CommandResult(
                        127,
                        CommandFailureCode.COMMAND_NOT_FOUND,
                        "",
                        f"{argv[0]}: command not found\n",
                    )
                elif (
                    item.redirection is not None
                    and not command.descriptor.permits_stdout_redirection
                ):
                    stage = CommandResult(
                        2,
                        CommandFailureCode.INVALID_ARGUMENT,
                        "",
                        f"{argv[0]}: stdout redirection is not permitted\n",
                    )
                else:
                    command_context = CommandContext(
                        cwd,
                        environment,
                        execution_context.cancellation,
                        execution_context.session_id,
                        execution_context.operation_id,
                    )
                    try:
                        stage = await command.execute(CommandRequest(argv, ""), command_context)
                    except SandboxError:
                        raise
                    except Exception as error:
                        raise CommandInternalFailure(
                            f"command handler {command.descriptor.name} failed unexpectedly"
                        ) from error
            self._check_execution_state(execution_context, deadline)

            if item.redirection is not None:
                assert destination is not None
                stage = await self._apply_redirection(
                    argv[0],
                    destination,
                    item.redirection.mode,
                    cwd,
                    stage,
                    request,
                )
                self._check_execution_state(execution_context, deadline)
            else:
                stdout.append(stage.stdout)
            stderr.append(stage.stderr)

            executed = True
            last_result = stage
            last_exit = stage.exit_code
            if stage.exit_code == 0:
                if stage.resulting_cwd is not None:
                    cwd = stage.resulting_cwd
                if stage.environment_changes:
                    environment = _apply_environment_changes(environment, stage.environment_changes)
                    all_changes.extend(stage.environment_changes)

        assert executed
        stdout_result = stdout.result()
        stderr_result = stderr.result()
        return ExecuteResult(
            last_result.exit_code,
            last_result.failure_code,
            stdout_result.text,
            stderr_result.text,
            stdout_result.original_bytes,
            stderr_result.original_bytes,
            stdout_result.truncated,
            stderr_result.truncated,
            0.0,
            cwd,
            tuple(all_changes),
        )

    async def _apply_redirection(
        self,
        command_name: str,
        destination: str,
        mode: RedirectionMode,
        cwd: SandboxPath,
        stage: CommandResult,
        request: ExecuteRequest,
    ) -> CommandResult:
        if stage.exit_code != 0:
            return CommandResult(
                stage.exit_code,
                stage.failure_code,
                "",
                stage.stderr,
                stage.resulting_cwd,
                stage.environment_changes,
            )
        bounded = bound_text(stage.stdout, request.limits.max_stdout_bytes)
        if bounded.truncated:
            return CommandResult(
                1,
                CommandFailureCode.REDIRECTION_FAILURE,
                "",
                stage.stderr + f"{command_name}: redirected stdout exceeds the output limit\n",
            )
        try:
            path = self._redirection_mutator.resolve_path(destination, cwd=cwd)
            content = stage.stdout.encode("utf-8")
            if mode is RedirectionMode.REPLACE:
                await self._redirection_mutator.write(
                    WorkspaceWriteRequest(path, content, AnyCurrentState())
                )
            else:
                await self._redirection_mutator.append(
                    WorkspaceAppendRequest(path, content, AnyCurrentState())
                )
        except _EXPECTED_WORKSPACE_FAILURES as error:
            return CommandResult(
                1,
                CommandFailureCode.REDIRECTION_FAILURE,
                "",
                stage.stderr + f"{command_name}: redirection failed: {error}\n",
            )
        except SandboxError:
            raise
        except Exception as error:
            raise CommandInternalFailure("stdout redirection failed unexpectedly") from error
        return CommandResult(
            0,
            None,
            "",
            stage.stderr,
            stage.resulting_cwd,
            stage.environment_changes,
        )

    @staticmethod
    def _check_cancelled(context: CommandExecutionContext) -> None:
        if context.cancellation is not None and context.cancellation.is_set():
            raise CommandCancelled("command execution was cancelled")

    @staticmethod
    def _check_execution_state(context: CommandExecutionContext, deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise CommandTimeout("command execution exceeded the complete-plan timeout")
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        VirtualCommandExecutor._check_cancelled(context)

    @staticmethod
    def _validate_plan_argument_counts(
        commands: tuple[PlanCommand, ...],
        request: ExecuteRequest,
    ) -> None:
        for command in commands:
            if len(command.words) > request.limits.max_argv_entries:
                raise CommandSyntaxInvalid(
                    f"command has {len(command.words)} argv entries; limit is "
                    f"{request.limits.max_argv_entries}"
                )

    @staticmethod
    def _expanded_argument_error(
        argv: tuple[str, ...],
        destination: str | None,
        request: ExecuteRequest,
    ) -> str | None:
        for argument in (*argv, *((destination,) if destination is not None else ())):
            try:
                size = len(argument.encode("utf-8"))
            except UnicodeEncodeError:
                return "expanded argument must be valid UTF-8"
            if size > request.limits.max_argument_bytes:
                return (
                    f"expanded argument contains {size} bytes; limit is "
                    f"{request.limits.max_argument_bytes}"
                )
        return None

    @staticmethod
    def _diagnostic_command_name(value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return "command"
        return value


def create_default_executor(
    reader: CommandWorkspaceReader,
    mutator: CommandWorkspaceMutator,
) -> VirtualCommandExecutor:
    """Construct the exact approved first-wave profile."""
    return VirtualCommandExecutor(
        CommandRegistry(create_first_wave_commands(reader, mutator)),
        mutator,
    )


def _apply_environment_changes(
    environment: CommandEnvironment,
    changes: tuple[EnvironmentChange, ...],
) -> CommandEnvironment:
    current = {item.name: item.value for item in environment.values}
    for change in changes:
        if change.value is None:
            current.pop(change.name, None)
        else:
            current[change.name] = change.value
    return CommandEnvironment(
        tuple(EnvironmentValue(name, value) for name, value in sorted(current.items()))
    )
