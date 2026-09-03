"""Complete-plan orchestration for the constrained virtual command language."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from mem_sandbox.command_executor.commands import create_command_profile
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
    CommandEnvironmentView,
    CommandExecutionContext,
    CommandFailureCode,
    CommandRequest,
    CommandResult,
    CommandValueProtection,
    EnvironmentChange,
    EnvironmentValue,
    ExecuteRequest,
    ExecuteResult,
    PlanUnit,
    RedirectionMode,
)
from mem_sandbox.command_executor.output import BoundedOutputCollector, bound_text
from mem_sandbox.command_executor.parser import expand_word, parse_execution_plan
from mem_sandbox.command_executor.ports import CommandWorkspaceMutator, CommandWorkspaceReader
from mem_sandbox.command_executor.registry import CommandRegistry, VirtualCommand
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


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedStage:
    argv: tuple[str, ...]
    command: VirtualCommand


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedUnit:
    stages: tuple[_PreparedStage, ...]
    destination: str | None
    failure: CommandResult | None = None
    redirection_admitted: bool = True


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
        self._validate_plan_structure(plan.units, request)
        started = time.perf_counter()
        deadline = asyncio.get_running_loop().time() + request.limits.timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                result = await self._execute_plan(request, context, plan.units, deadline)
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
        units: tuple[PlanUnit, ...],
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

        for item in units:
            if item.connector.value == "on_success" and last_exit != 0:
                continue
            self._check_execution_state(execution_context, deadline)
            environment_view = CommandEnvironmentView(environment, request.overlay)
            prepared = self._prepare_unit(
                item,
                request,
                environment_view,
                cwd,
                execution_context.protection,
            )
            if prepared.failure is not None:
                stage = prepared.failure
            else:
                stage = await self._execute_unit(
                    prepared,
                    request,
                    execution_context,
                    environment_view,
                    cwd,
                    deadline,
                )
            stage = self._sanitize_stage(
                stage,
                "command",
                execution_context,
                environment_view,
            )
            self._check_execution_state(execution_context, deadline)

            if (
                item.redirection is not None
                and prepared.redirection_admitted
                and stage.failure_code is not CommandFailureCode.PIPELINE_LIMIT_EXCEEDED
            ):
                assert prepared.destination is not None
                command_name = self._diagnostic_command_name(
                    expand_word(item.stages[-1].words[0], environment_view, cwd)
                )
                stage = await self._apply_redirection(
                    command_name,
                    prepared.destination,
                    item.redirection.mode,
                    cwd,
                    stage,
                    request,
                    execution_context,
                )
                stage = self._sanitize_stage(
                    stage,
                    command_name,
                    execution_context,
                    environment_view,
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

    def _prepare_unit(
        self,
        item: PlanUnit,
        request: ExecuteRequest,
        environment: CommandEnvironmentView,
        cwd: SandboxPath,
        protection: CommandValueProtection,
    ) -> _PreparedUnit:
        destination = (
            expand_word(item.redirection.destination, environment, cwd)
            if item.redirection is not None
            else None
        )
        prepared: list[_PreparedStage] = []
        first_failure: CommandResult | None = None
        final_command: VirtualCommand | None = None
        final_argv: tuple[str, ...] | None = None
        redirection_admitted = True
        is_pipeline = len(item.stages) > 1
        if (
            destination is not None
            and protection.contains_protected_text(destination)
        ):
            first_failure = CommandResult(
                1,
                CommandFailureCode.PROTECTED_VALUE_REJECTED,
                "",
                "command: protected command arguments are not permitted\n",
            )
            redirection_admitted = False
        for index, stage in enumerate(item.stages):
            argv = tuple(expand_word(word, environment, cwd) for word in stage.words)
            if index == len(item.stages) - 1:
                final_argv = argv
            if any(protection.contains_protected_text(argument) for argument in argv):
                if first_failure is None:
                    first_failure = CommandResult(
                        1,
                        CommandFailureCode.PROTECTED_VALUE_REJECTED,
                        "",
                        "command: protected command arguments are not permitted\n",
                    )
                redirection_admitted = False
                continue
            argument_error = self._expanded_argument_error(
                argv,
                destination if index == len(item.stages) - 1 else None,
                request,
                protection,
            )
            if argument_error is not None:
                redirection_admitted = False
                if first_failure is None:
                    first_failure = CommandResult(
                        2,
                        CommandFailureCode.INVALID_ARGUMENT,
                        "",
                        f"{self._diagnostic_command_name(argv[0])}: {argument_error}\n",
                    )
            command = self._registry.resolve(argv[0])
            if command is None:
                if first_failure is None:
                    first_failure = CommandResult(
                        127,
                        CommandFailureCode.COMMAND_NOT_FOUND,
                        "",
                        f"{argv[0]}: command not found\n",
                    )
                continue
            if index == len(item.stages) - 1:
                final_command = command
            if is_pipeline and not command.descriptor.pipeline_safe:
                if first_failure is None:
                    first_failure = CommandResult(
                        2,
                        CommandFailureCode.INVALID_ARGUMENT,
                        "",
                        f"{argv[0]}: command is not permitted in pipelines\n",
                    )
                redirection_admitted = False
            if index > 0 and not command.descriptor.accepts_stdin:
                if first_failure is None:
                    first_failure = CommandResult(
                        2,
                        CommandFailureCode.INVALID_ARGUMENT,
                        "",
                        f"{argv[0]}: command does not accept pipeline input\n",
                    )
                redirection_admitted = False
            prepared.append(_PreparedStage(argv, command))

        if (
            item.redirection is not None
            and final_command is not None
            and not final_command.descriptor.permits_stdout_redirection
        ):
            assert final_argv is not None
            return _PreparedUnit(
                tuple(prepared),
                destination,
                first_failure
                if (
                    first_failure is not None
                    and first_failure.failure_code
                    is CommandFailureCode.PROTECTED_VALUE_REJECTED
                )
                else CommandResult(
                    2,
                    CommandFailureCode.INVALID_ARGUMENT,
                    "",
                    f"{final_argv[0]}: stdout redirection is not permitted\n",
                ),
                False,
            )
        return _PreparedUnit(
            tuple(prepared),
            destination,
            first_failure,
            redirection_admitted,
        )

    async def _execute_unit(
        self,
        prepared: _PreparedUnit,
        request: ExecuteRequest,
        execution_context: CommandExecutionContext,
        environment: CommandEnvironmentView,
        cwd: SandboxPath,
        deadline: float,
    ) -> CommandResult:
        if len(prepared.stages) == 1:
            stage = prepared.stages[0]
            return await self._dispatch(
                stage,
                "",
                False,
                environment,
                cwd,
                execution_context,
            )

        intermediate_bytes = 0
        previous_stdout = ""
        combined_stderr: list[str] = []
        effective_exit = 0
        effective_failure: CommandFailureCode | None = None
        final_stdout = ""
        for index, prepared_stage in enumerate(prepared.stages):
            self._check_execution_state(execution_context, deadline)
            result = await self._dispatch(
                prepared_stage,
                previous_stdout if index > 0 else "",
                index > 0,
                environment,
                cwd,
                execution_context,
            )
            self._check_execution_state(execution_context, deadline)
            if result.resulting_cwd is not None or result.environment_changes:
                raise CommandInternalFailure(
                    f"pipeline-safe command {prepared_stage.command.descriptor.name} "
                    "attempted to change execution state"
                )
            combined_stderr.append(result.stderr)
            if result.exit_code != 0:
                effective_exit = result.exit_code
                effective_failure = result.failure_code
            if index == len(prepared.stages) - 1:
                final_stdout = result.stdout
                continue
            stage_bytes = len(result.stdout.encode("utf-8"))
            intermediate_bytes += stage_bytes
            if (
                stage_bytes > request.limits.max_pipeline_intermediate_bytes
                or intermediate_bytes > request.limits.max_pipeline_aggregate_bytes
            ):
                combined_stderr.append(
                    f"{prepared_stage.argv[0]}: pipeline intermediate output exceeds "
                    "the configured limit\n"
                )
                return CommandResult(
                    1,
                    CommandFailureCode.PIPELINE_LIMIT_EXCEEDED,
                    "",
                    "".join(combined_stderr),
                )
            previous_stdout = result.stdout
        return CommandResult(
            effective_exit,
            effective_failure,
            final_stdout,
            "".join(combined_stderr),
        )

    async def _dispatch(
        self,
        prepared: _PreparedStage,
        stdin: str,
        stdin_connected: bool,
        environment: CommandEnvironmentView,
        cwd: SandboxPath,
        execution_context: CommandExecutionContext,
    ) -> CommandResult:
        command_context = CommandContext(
            cwd,
            environment,
            execution_context.cancellation,
            execution_context.session_id,
            execution_context.operation_id,
            execution_context.protection,
        )
        try:
            result = await prepared.command.execute(
                CommandRequest(prepared.argv, stdin, stdin_connected),
                command_context,
            )
        except SandboxError as error:
            if execution_context.protection.contains_protected_text(str(error)):
                raise CommandInternalFailure(
                    f"command handler {prepared.command.descriptor.name} "
                    "failed with protected error data"
                ) from None
            raise
        except Exception as error:
            if execution_context.protection.contains_protected_text(str(error)):
                raise CommandInternalFailure(
                    f"command handler {prepared.command.descriptor.name} "
                    "failed with protected error data"
                ) from None
            raise CommandInternalFailure(
                f"command handler {prepared.command.descriptor.name} failed unexpectedly"
            ) from error
        return self._sanitize_stage(
            result,
            prepared.command.descriptor.name,
            execution_context,
            environment,
        )

    async def _apply_redirection(
        self,
        command_name: str,
        destination: str,
        mode: RedirectionMode,
        cwd: SandboxPath,
        stage: CommandResult,
        request: ExecuteRequest,
        execution_context: CommandExecutionContext,
    ) -> CommandResult:
        if execution_context.protection.contains_protected_text(destination):
            return self._protected_failure(command_name, execution_context)
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
            stage.exit_code,
            stage.failure_code,
            "",
            stage.stderr,
            stage.resulting_cwd,
            stage.environment_changes,
        )

    @staticmethod
    def _sanitize_stage(
        stage: CommandResult,
        command_name: str,
        execution_context: CommandExecutionContext,
        environment: CommandEnvironmentView,
    ) -> CommandResult:
        protection = execution_context.protection
        if (
            stage.resulting_cwd is not None
            and protection.contains_protected_text(stage.resulting_cwd.value)
        ):
            return VirtualCommandExecutor._protected_failure(command_name, execution_context)
        for change in stage.environment_changes:
            if (
                protection.contains_protected_text(change.name)
                or environment.is_overlay_name(change.name)
                or (
                change.value is not None
                and protection.contains_protected_text(change.value)
                )
            ):
                return VirtualCommandExecutor._protected_failure(
                    command_name,
                    execution_context,
                )
        return CommandResult(
            stage.exit_code,
            stage.failure_code,
            protection.redact_text(stage.stdout),
            protection.redact_text(stage.stderr),
            stage.resulting_cwd,
            stage.environment_changes,
        )

    @staticmethod
    def _protected_failure(
        command_name: str,
        execution_context: CommandExecutionContext,
    ) -> CommandResult:
        safe_name = execution_context.protection.redact_text(command_name)
        return CommandResult(
            1,
            CommandFailureCode.PROTECTED_VALUE_REJECTED,
            "",
            f"{safe_name}: protected values cannot be persisted\n",
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
    def _validate_plan_structure(
        units: tuple[PlanUnit, ...],
        request: ExecuteRequest,
    ) -> None:
        for unit in units:
            if len(unit.stages) > request.limits.max_pipeline_stages:
                raise CommandSyntaxInvalid(
                    f"pipeline has {len(unit.stages)} stages; limit is "
                    f"{request.limits.max_pipeline_stages}"
                )
            for stage in unit.stages:
                if len(stage.words) > request.limits.max_argv_entries:
                    raise CommandSyntaxInvalid(
                        f"command has {len(stage.words)} argv entries; limit is "
                        f"{request.limits.max_argv_entries}"
                    )

    @staticmethod
    def _expanded_argument_error(
        argv: tuple[str, ...],
        destination: str | None,
        request: ExecuteRequest,
        protection: CommandValueProtection,
    ) -> str | None:
        for argument in (*argv, *((destination,) if destination is not None else ())):
            try:
                size = len(argument.encode("utf-8"))
            except UnicodeEncodeError:
                return "expanded argument must be valid UTF-8"
            if size > request.limits.max_argument_bytes:
                if protection.contains_protected_text(argument):
                    return "expanded protected argument exceeds the configured limit"
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
    """Construct the complete approved command profile."""
    return VirtualCommandExecutor(
        CommandRegistry(create_command_profile(reader, mutator)),
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
