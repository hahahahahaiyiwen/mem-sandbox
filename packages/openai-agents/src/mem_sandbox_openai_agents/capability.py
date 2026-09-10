"""Model-facing OpenAI Agents SDK tools backed by a MemSandbox session."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Literal

from agents.sandbox import Capability
from agents.sandbox.session import BaseSandboxSession
from agents.sandbox.workspace_paths import SandboxWorkspaceScope
from agents.tool import FunctionTool, Tool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from mem_sandbox.command_executor import CommandLimits
from mem_sandbox.core import ErrorCategory, OperationLimits, OperationResultMetadata, SandboxError
from mem_sandbox.session import (
    ApplyPatchRequest,
    FileMutationResult,
    PatchMutationResult,
    ReadFileRequest,
    ReadFileResult,
    SessionExecuteRequest,
    SessionExecuteResult,
    SessionExpectedFileHash,
    SessionOperationCancelled,
    WriteFileRequest,
)
from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    ContentHashMustEqual,
    PathMustNotExist,
    SandboxPath,
)
from mem_sandbox_openai_agents.adapter import (
    InMemorySandboxSession,
    resolve_in_memory_sandbox_session,
)

_PROVIDER_TYPE = "mem_sandbox"
_LOGGER = logging.getLogger(__name__)
_DEFAULT_COMMAND_LIMITS = CommandLimits()
_DEFAULT_OPERATION_LIMITS = OperationLimits()
_MAX_EXECUTE_TIMEOUT_SECONDS = min(
    _DEFAULT_COMMAND_LIMITS.timeout_seconds,
    _DEFAULT_OPERATION_LIMITS.timeout_seconds,
)
_MAX_EXECUTE_OUTPUT_BYTES = min(
    _DEFAULT_COMMAND_LIMITS.max_stdout_bytes,
    _DEFAULT_COMMAND_LIMITS.max_stderr_bytes,
)


class _ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)


class _InvalidToolInput(Exception):
    pass


class _ExecuteInput(_ToolInput):
    command: str = Field(min_length=1)
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        le=_MAX_EXECUTE_TIMEOUT_SECONDS,
    )
    max_output_bytes: int | None = Field(
        default=None,
        gt=0,
        le=_MAX_EXECUTE_OUTPUT_BYTES,
    )


class _ReadFileInput(_ToolInput):
    path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_line_range(self) -> _ReadFileInput:
        if self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must not be before start_line")
        return self


class _WriteFileInput(_ToolInput):
    path: str = Field(min_length=1)
    content: str
    write_condition: Literal[
        "any_current_state",
        "path_must_not_exist",
        "content_hash_must_equal",
    ]
    expected_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    create_parents: bool = False

    @model_validator(mode="after")
    def _validate_expected_hash(self) -> _WriteFileInput:
        requires_hash = self.write_condition == "content_hash_must_equal"
        if requires_hash and self.expected_hash is None:
            raise ValueError("expected_hash is required for content_hash_must_equal")
        if not requires_hash and self.expected_hash is not None:
            raise ValueError("expected_hash is only valid for content_hash_must_equal")
        return self


class _ExpectedHashInput(_ToolInput):
    path: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def _empty_expected_hashes() -> list[_ExpectedHashInput]:
    return []


class _ApplyPatchInput(_ToolInput):
    patch: str
    expected_hashes: list[_ExpectedHashInput] = Field(default_factory=_empty_expected_hashes)


class InMemorySandboxCapability(Capability):
    """Expose the constrained four-tool MemSandbox profile to an OpenAI agent."""

    type: Literal["mem_sandbox"] = _PROVIDER_TYPE  # pyright: ignore[reportIncompatibleVariableOverride]
    _provider_session: InMemorySandboxSession | None = PrivateAttr(default=None)

    def bind(self, session: BaseSandboxSession) -> None:
        self._provider_session = resolve_in_memory_sandbox_session(session)
        super().bind(session)

    def bind_workspace_scope(self, scope: SandboxWorkspaceScope) -> None:
        if scope.cwd is not None:
            raise ValueError("InMemorySandboxCapability does not support SandboxRunConfig.cwd")
        super().bind_workspace_scope(scope)

    def tools(self) -> list[Tool]:
        session = self.session
        if session is None:
            raise ValueError("InMemorySandboxCapability is not bound")
        provider_session = self._provider_session
        if provider_session is None:
            raise ValueError("InMemorySandboxCapability is not bound")
        domain = provider_session.core_session

        async def execute_tool(arguments: _ExecuteInput) -> SessionExecuteResult:
            provider_session.require_available()
            try:
                command_limits = _DEFAULT_COMMAND_LIMITS
                operation_limits = _DEFAULT_OPERATION_LIMITS
                if arguments.timeout_seconds is not None:
                    timeout = arguments.timeout_seconds
                    command_limits = replace(command_limits, timeout_seconds=timeout)
                    operation_limits = replace(
                        operation_limits,
                        timeout_seconds=timeout,
                        terminal_event_reserve_seconds=min(1.0, timeout / 2),
                    )
                if arguments.max_output_bytes is not None:
                    command_limits = replace(
                        command_limits,
                        max_stdout_bytes=arguments.max_output_bytes,
                        max_stderr_bytes=arguments.max_output_bytes,
                    )
                request = SessionExecuteRequest(
                    command=arguments.command,
                    command_limits=command_limits,
                    limits=operation_limits,
                )
            except (TypeError, ValueError) as error:
                raise _InvalidToolInput from error
            return await domain.execute(request)

        async def read_file_tool(arguments: _ReadFileInput) -> ReadFileResult:
            provider_session.require_available()
            try:
                request = ReadFileRequest(
                    path=arguments.path,
                    start_line=arguments.start_line,
                    end_line=arguments.end_line,
                )
            except (TypeError, ValueError) as error:
                raise _InvalidToolInput from error
            return await domain.read_file(request)

        async def write_file_tool(arguments: _WriteFileInput) -> FileMutationResult:
            provider_session.require_available()
            try:
                if arguments.write_condition == "any_current_state":
                    precondition = AnyCurrentState()
                elif arguments.write_condition == "path_must_not_exist":
                    precondition = PathMustNotExist()
                else:
                    assert arguments.expected_hash is not None
                    precondition = ContentHashMustEqual(ContentHash(arguments.expected_hash))
                request = WriteFileRequest(
                    path=arguments.path,
                    content=arguments.content,
                    precondition=precondition,
                    create_parents=arguments.create_parents,
                )
            except (TypeError, ValueError) as error:
                raise _InvalidToolInput from error
            return await domain.write_file(request)

        async def apply_patch_tool(arguments: _ApplyPatchInput) -> PatchMutationResult:
            provider_session.require_available()
            try:
                workspace_limits = provider_session.state.workspace_limits
                resolved_paths: set[SandboxPath] = set()
                for item in arguments.expected_hashes:
                    resolved_path = SandboxPath.resolve(
                        item.path,
                        cwd=domain.cwd,
                        max_path_bytes=workspace_limits.max_path_bytes,
                        max_segment_bytes=workspace_limits.max_segment_bytes,
                    )
                    if resolved_path in resolved_paths:
                        raise _InvalidToolInput
                    resolved_paths.add(resolved_path)
                expected_hashes = tuple(
                    SessionExpectedFileHash(item.path, ContentHash(item.content_hash))
                    for item in arguments.expected_hashes
                )
                request = ApplyPatchRequest(
                    patch=arguments.patch,
                    expected_hashes=expected_hashes,
                )
            except (TypeError, ValueError) as error:
                raise _InvalidToolInput from error
            return await domain.apply_patch(request)

        return [
            _function_tool(
                name="execute",
                description=(
                    "Execute one constrained MemSandbox command. This is not a host shell."
                ),
                input_type=_ExecuteInput,
                invoke=execute_tool,
                serialize=_serialize_execute,
            ),
            _function_tool(
                name="read_file",
                description="Read UTF-8 text from the in-memory workspace.",
                input_type=_ReadFileInput,
                invoke=read_file_tool,
                serialize=_serialize_read,
            ),
            _function_tool(
                name="write_file",
                description="Write UTF-8 text with an explicit concurrency precondition.",
                input_type=_WriteFileInput,
                invoke=write_file_tool,
                serialize=_serialize_write,
            ),
            _function_tool(
                name="apply_patch",
                description="Atomically apply a constrained unified diff to workspace files.",
                input_type=_ApplyPatchInput,
                invoke=apply_patch_tool,
                serialize=_serialize_patch,
            ),
        ]


def _function_tool[Input: _ToolInput, Result](
    *,
    name: str,
    description: str,
    input_type: type[Input],
    invoke: Callable[[Input], Awaitable[Result]],
    serialize: Callable[[Result], dict[str, object]],
) -> FunctionTool:
    async def on_invoke_tool(_context: Any, input_json: str) -> str:
        try:
            arguments = input_type.model_validate_json(input_json)
            request = arguments
        except (ValidationError, ValueError, TypeError):
            return _render_error(
                category=ErrorCategory.INVALID_REQUEST,
                code="invalid_tool_input",
                message=f"Invalid input for {name}",
                correctable=True,
                retryable=False,
            )

        try:
            result = await invoke(request)
        except _InvalidToolInput:
            return _render_error(
                category=ErrorCategory.INVALID_REQUEST,
                code="invalid_tool_input",
                message=f"Invalid input for {name}",
                correctable=True,
                retryable=False,
            )
        except SessionOperationCancelled:
            raise
        except SandboxError as error:
            return _render_sandbox_error(error)
        except Exception:
            correlation_id = str(uuid.uuid4())
            _LOGGER.exception(
                "Unexpected MemSandbox tool failure; correlation_id=%s",
                correlation_id,
            )
            return _render_error(
                category=ErrorCategory.INTERNAL,
                code="internal_error",
                message="The sandbox operation failed unexpectedly",
                correctable=False,
                retryable=False,
                correlation_id=correlation_id,
            )
        return _render({"ok": True, "result": serialize(result)})

    return FunctionTool(
        name=name,
        description=description,
        params_json_schema=input_type.model_json_schema(),
        on_invoke_tool=on_invoke_tool,
        strict_json_schema=True,
    )


def _serialize_metadata(metadata: OperationResultMetadata) -> dict[str, object]:
    return {
        "session_id": str(metadata.session_id),
        "operation_id": str(metadata.operation_id),
        "workspace_revision": metadata.workspace_revision.value,
        "started_at": metadata.started_at.isoformat(),
        "completed_at": metadata.completed_at.isoformat(),
    }


def _serialize_execute(result: SessionExecuteResult) -> dict[str, object]:
    return {
        "metadata": _serialize_metadata(result.metadata),
        "exit_code": result.exit_code,
        "failure_code": None if result.failure_code is None else result.failure_code.value,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_original_bytes": result.stdout_original_bytes,
        "stderr_original_bytes": result.stderr_original_bytes,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "duration_ms": result.duration_ms,
        "resulting_cwd": result.resulting_cwd.value,
        "environment_changes": [
            {"name": change.name, "value": change.value} for change in result.environment_changes
        ],
    }


def _serialize_read(result: ReadFileResult) -> dict[str, object]:
    return {
        "metadata": _serialize_metadata(result.metadata),
        "path": result.path.value,
        "content": result.content,
        "start_line": result.start_line,
        "end_line": result.end_line,
        "total_lines": result.total_lines,
        "content_hash": result.content_hash.value,
    }


def _serialize_write(result: FileMutationResult) -> dict[str, object]:
    return {
        "metadata": _serialize_metadata(result.metadata),
        "path": result.path.value,
        "created": result.created,
        "changed": result.changed,
        "previous_hash": None if result.previous_hash is None else result.previous_hash.value,
        "current_hash": None if result.current_hash is None else result.current_hash.value,
    }


def _serialize_patch(result: PatchMutationResult) -> dict[str, object]:
    return {
        "metadata": _serialize_metadata(result.metadata),
        "files": [
            {
                "path": item.path.value,
                "previous_hash": item.previous_hash.value,
                "current_hash": item.current_hash.value,
            }
            for item in result.files
        ],
    }


def _render_sandbox_error(error: SandboxError) -> str:
    if error.category is ErrorCategory.INTERNAL:
        correlation_id = str(uuid.uuid4())
        _LOGGER.exception(
            "Internal MemSandbox tool failure; correlation_id=%s",
            correlation_id,
        )
        return _render_error(
            category=ErrorCategory.INTERNAL,
            code="internal_error",
            message="The sandbox operation failed unexpectedly",
            correctable=False,
            retryable=False,
            correlation_id=correlation_id,
            operation_id=_operation_id(error),
        )
    return _render_error(
        category=error.category,
        code=error.code,
        message=str(error),
        correctable=error.category
        in {
            ErrorCategory.INVALID_REQUEST,
            ErrorCategory.NOT_FOUND,
            ErrorCategory.CONFLICT,
            ErrorCategory.QUOTA_EXCEEDED,
            ErrorCategory.TIMEOUT,
        },
        retryable=error.category is ErrorCategory.TIMEOUT,
        operation_id=_operation_id(error),
    )


def _operation_id(error: SandboxError) -> str | None:
    operation_id = getattr(error, "operation_id", None)
    return None if operation_id is None else str(operation_id)


def _render_error(
    *,
    category: ErrorCategory,
    code: str,
    message: str,
    correctable: bool,
    retryable: bool,
    operation_id: str | None = None,
    correlation_id: str | None = None,
) -> str:
    details: dict[str, object] = {
        "category": category.value,
        "code": code,
        "message": message,
        "correctable": correctable,
        "retryable": retryable,
    }
    if operation_id is not None:
        details["operation_id"] = operation_id
    if correlation_id is not None:
        details["correlation_id"] = correlation_id
    return _render({"ok": False, "error": details})


def _render(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
