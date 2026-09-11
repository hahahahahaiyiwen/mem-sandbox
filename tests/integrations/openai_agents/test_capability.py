from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from agents import ModelResponse, RunConfig, Runner, Usage
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.sandbox import Capability, Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.session import BaseSandboxSession
from agents.sandbox.snapshot import NoopSnapshot
from agents.sandbox.workspace_paths import SandboxWorkspaceScope
from agents.tool import FunctionTool, Tool
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam

from mem_sandbox.command_executor import CommandFailureCode, CommandLimits, EnvironmentChange
from mem_sandbox.core import (
    OperationId,
    OperationLimits,
    OperationResultMetadata,
    Revision,
    SessionId,
)
from mem_sandbox.service import SandboxService
from mem_sandbox.session import (
    ApplyPatchRequest,
    FileMutationResult,
    PatchMutationResult,
    ReadFileRequest,
    ReadFileResult,
    SandboxSessionState,
    SessionExecuteRequest,
    SessionExecuteResult,
    SessionExpectedFileHash,
    SessionOperationCancelled,
    SessionOperationTimeout,
    SessionPolicyDenied,
    WriteFileRequest,
)
from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    ContentHashMustEqual,
    PatchedFile,
    PathMustNotExist,
    PathNotFoundError,
    SandboxPath,
)
from mem_sandbox_openai_agents import (
    InMemorySandboxCapability,
    InMemorySandboxClient,
    InMemorySandboxClientOptions,
    InMemorySandboxSession,
    InMemorySandboxSessionState,
)

from .support import create_service_bundle

_SESSION_ID = SessionId(UUID("11111111-1111-1111-1111-111111111111"))
_OPERATION_ID = OperationId(UUID("22222222-2222-2222-2222-222222222222"))
_STARTED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
_COMPLETED_AT = datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC)


def _metadata(revision: int) -> OperationResultMetadata:
    return OperationResultMetadata(
        session_id=_SESSION_ID,
        operation_id=_OPERATION_ID,
        workspace_revision=Revision(revision),
        started_at=_STARTED_AT,
        completed_at=_COMPLETED_AT,
    )


class RecordingDomainSession:
    def __init__(self) -> None:
        self.session_id = _SESSION_ID
        self.state = SandboxSessionState.RUNNING
        self.cwd = SandboxPath("/workspace")
        self.execute_requests: list[SessionExecuteRequest] = []
        self.read_requests: list[ReadFileRequest] = []
        self.write_requests: list[WriteFileRequest] = []
        self.patch_requests: list[ApplyPatchRequest] = []
        self.failure: BaseException | None = None

    def _raise_failure(self) -> None:
        if self.failure is not None:
            raise self.failure

    async def execute(self, request: SessionExecuteRequest) -> SessionExecuteResult:
        self.execute_requests.append(request)
        self._raise_failure()
        return SessionExecuteResult(
            metadata=_metadata(3),
            exit_code=7,
            failure_code=CommandFailureCode.NO_MATCH,
            stdout="out",
            stderr="err",
            stdout_original_bytes=9,
            stderr_original_bytes=8,
            stdout_truncated=True,
            stderr_truncated=False,
            duration_ms=12.5,
            resulting_cwd=SandboxPath("/workspace/project"),
            environment_changes=(EnvironmentChange("MODE", "test"),),
        )

    async def read_file(self, request: ReadFileRequest) -> ReadFileResult:
        self.read_requests.append(request)
        self._raise_failure()
        return ReadFileResult(
            metadata=_metadata(4),
            path=SandboxPath("/workspace/read.txt"),
            content="line one\n",
            start_line=2,
            end_line=2,
            total_lines=5,
            content_hash=ContentHash("1" * 64),
        )

    async def write_file(self, request: WriteFileRequest) -> FileMutationResult:
        self.write_requests.append(request)
        self._raise_failure()
        return FileMutationResult(
            metadata=_metadata(5),
            path=SandboxPath("/workspace/write.txt"),
            created=False,
            changed=True,
            previous_hash=ContentHash("2" * 64),
            current_hash=ContentHash("3" * 64),
        )

    async def apply_patch(self, request: ApplyPatchRequest) -> PatchMutationResult:
        self.patch_requests.append(request)
        self._raise_failure()
        return PatchMutationResult(
            metadata=_metadata(6),
            files=(
                PatchedFile(
                    path=SandboxPath("/workspace/a.txt"),
                    previous_hash=ContentHash("4" * 64),
                    current_hash=ContentHash("5" * 64),
                ),
                PatchedFile(
                    path=SandboxPath("/workspace/b.txt"),
                    previous_hash=ContentHash("6" * 64),
                    current_hash=ContentHash("7" * 64),
                ),
            ),
        )


class DeterministicToolModel(Model):
    def __init__(self) -> None:
        self.calls = 0
        self.tool_names: list[list[str]] = []
        self.inputs: list[str | list[TResponseInputItem]] = []

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        _ = (
            system_instructions,
            model_settings,
            output_schema,
            handoffs,
            tracing,
            previous_response_id,
            conversation_id,
            prompt,
        )
        self.calls += 1
        self.tool_names.append([tool.name for tool in tools])
        self.inputs.append(input)
        if self.calls == 1:
            output = [
                ResponseFunctionToolCall(
                    arguments=json.dumps(
                        {
                            "path": "/workspace/from-runner.txt",
                            "content": "runner\n",
                            "write_condition": "path_must_not_exist",
                            "create_parents": False,
                        }
                    ),
                    call_id="call_1",
                    name="write_file",
                    type="function_call",
                    status="completed",
                )
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="message_1",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="done",
                            type="output_text",
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        return ModelResponse(
            output=cast(Any, output),
            usage=Usage(),
            response_id=f"response_{self.calls}",
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        _ = (
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id,
            conversation_id,
            prompt,
        )
        raise NotImplementedError
        yield


def _provider_session(
    domain: RecordingDomainSession | None = None,
) -> tuple[InMemorySandboxSession, RecordingDomainSession]:
    selected = domain or RecordingDomainSession()
    state = InMemorySandboxSessionState(
        sandbox_handle="33333333-3333-3333-3333-333333333333",
        core_session_id=str(_SESSION_ID),
        snapshot=NoopSnapshot(id="capability"),
        manifest=Manifest(),
    )
    provider = InMemorySandboxSession(
        state=state,
        service=cast(SandboxService, object()),
        session=cast(Any, selected),
    )
    return provider, selected


def _bound_capability(
    domain: RecordingDomainSession | None = None,
) -> tuple[InMemorySandboxCapability, RecordingDomainSession]:
    provider, selected = _provider_session(domain)
    capability = InMemorySandboxCapability()
    capability.bind(provider)
    return capability, selected


async def _invoke(tool: Any, payload: dict[str, object]) -> dict[str, Any]:
    rendered = await tool.on_invoke_tool(cast(Any, None), json.dumps(payload))
    assert isinstance(rendered, str)
    parsed = json.loads(rendered)
    assert isinstance(parsed, dict)
    return cast(dict[str, Any], parsed)


def test_capability_contract_clones_unbound_and_exposes_exact_four_tools() -> None:
    provider, _ = _provider_session()
    original = InMemorySandboxCapability()
    cloned = cast(InMemorySandboxCapability, original.clone())
    cloned.bind(provider)

    tools = cloned.tools()

    assert isinstance(original, Capability)
    assert original.type == "mem_sandbox"
    assert original.session is None
    assert cloned is not original
    assert cloned.session is provider
    assert [tool.name for tool in tools] == [
        "execute",
        "read_file",
        "write_file",
        "apply_patch",
    ]
    for tool in tools:
        assert isinstance(tool, FunctionTool)
        serialized_schema = json.dumps(tool.params_json_schema)
        assert "sandbox_handle" not in serialized_schema
        assert "session_id" not in serialized_schema
        assert "shell" not in serialized_schema
        assert "tty" not in serialized_schema


def test_capability_rejects_unbound_or_wrong_provider_session() -> None:
    capability = InMemorySandboxCapability()

    with pytest.raises(ValueError, match="not bound"):
        capability.tools()
    with pytest.raises(TypeError, match="InMemorySandboxSession"):
        capability.bind(cast(BaseSandboxSession, object()))
    with pytest.raises(ValueError, match=r"does not support SandboxRunConfig\.cwd"):
        capability.bind_workspace_scope(SandboxWorkspaceScope.from_cwd("project"))


@pytest.mark.asyncio
async def test_execute_translates_limits_once_and_returns_domain_faithful_result() -> None:
    capability, domain = _bound_capability()
    execute = capability.tools()[0]

    output = await _invoke(
        execute,
        {
            "command": "grep needle /workspace/data.txt",
            "timeout_seconds": 2.5,
            "max_output_bytes": 1234,
        },
    )

    assert len(domain.execute_requests) == 1
    request = domain.execute_requests[0]
    assert request.command == "grep needle /workspace/data.txt"
    assert request.command_limits.timeout_seconds == 2.5
    assert request.command_limits.max_stdout_bytes == 1234
    assert request.command_limits.max_stderr_bytes == 1234
    assert request.limits.timeout_seconds == 2.5
    assert output == {
        "ok": True,
        "result": {
            "metadata": {
                "session_id": str(_SESSION_ID),
                "operation_id": str(_OPERATION_ID),
                "workspace_revision": 3,
                "started_at": _STARTED_AT.isoformat(),
                "completed_at": _COMPLETED_AT.isoformat(),
            },
            "exit_code": 7,
            "failure_code": "no_match",
            "stdout": "out",
            "stderr": "err",
            "stdout_original_bytes": 9,
            "stderr_original_bytes": 8,
            "stdout_truncated": True,
            "stderr_truncated": False,
            "duration_ms": 12.5,
            "resulting_cwd": "/workspace/project",
            "environment_changes": [{"name": "MODE", "value": "test"}],
        },
    }


@pytest.mark.asyncio
async def test_execute_accepts_fixed_profile_limit_boundaries() -> None:
    capability, domain = _bound_capability()
    execute = capability.tools()[0]
    command_limits = CommandLimits()
    operation_limits = OperationLimits()

    output = await _invoke(
        execute,
        {
            "command": "pwd",
            "timeout_seconds": min(
                command_limits.timeout_seconds,
                operation_limits.timeout_seconds,
            ),
            "max_output_bytes": min(
                command_limits.max_stdout_bytes,
                command_limits.max_stderr_bytes,
            ),
        },
    )

    assert output["ok"] is True
    request = domain.execute_requests[0]
    assert request.command_limits.timeout_seconds == command_limits.timeout_seconds
    assert request.limits.timeout_seconds == operation_limits.timeout_seconds
    assert request.command_limits.max_stdout_bytes == command_limits.max_stdout_bytes
    assert request.command_limits.max_stderr_bytes == command_limits.max_stderr_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", OperationLimits().timeout_seconds + 0.1),
        ("max_output_bytes", CommandLimits().max_stdout_bytes + 1),
    ],
)
async def test_execute_rejects_limits_above_fixed_profile_ceiling(
    field: str,
    value: float | int,
) -> None:
    capability, domain = _bound_capability()
    execute = capability.tools()[0]

    output = await _invoke(execute, {"command": "pwd", field: value})

    assert output["error"]["code"] == "invalid_tool_input"
    assert domain.execute_requests == []


@pytest.mark.asyncio
async def test_file_tools_preserve_domain_requests_and_results() -> None:
    capability, domain = _bound_capability()
    read_file, write_file, apply_patch = capability.tools()[1:]

    read_output = await _invoke(
        read_file,
        {"path": "/workspace/read.txt", "start_line": 2, "end_line": 2},
    )
    write_output = await _invoke(
        write_file,
        {
            "path": "/workspace/write.txt",
            "content": "updated",
            "write_condition": "content_hash_must_equal",
            "expected_hash": "2" * 64,
            "create_parents": True,
        },
    )
    patch_output = await _invoke(
        apply_patch,
        {
            "patch": "--- a.txt\n+++ a.txt\n@@ -1 +1 @@\n-old\n+new\n",
            "expected_hashes": [
                {"path": "/workspace/a.txt", "content_hash": "4" * 64},
                {"path": "/workspace/b.txt", "content_hash": "6" * 64},
            ],
        },
    )

    assert domain.read_requests == [
        ReadFileRequest(path="/workspace/read.txt", start_line=2, end_line=2)
    ]
    assert domain.write_requests == [
        WriteFileRequest(
            path="/workspace/write.txt",
            content="updated",
            precondition=ContentHashMustEqual(ContentHash("2" * 64)),
            create_parents=True,
        )
    ]
    assert domain.patch_requests == [
        ApplyPatchRequest(
            patch="--- a.txt\n+++ a.txt\n@@ -1 +1 @@\n-old\n+new\n",
            expected_hashes=(
                SessionExpectedFileHash("/workspace/a.txt", ContentHash("4" * 64)),
                SessionExpectedFileHash("/workspace/b.txt", ContentHash("6" * 64)),
            ),
        )
    ]
    assert read_output["result"]["content_hash"] == "1" * 64
    assert read_output["result"]["metadata"]["workspace_revision"] == 4
    assert write_output["result"] == {
        "metadata": {
            "session_id": str(_SESSION_ID),
            "operation_id": str(_OPERATION_ID),
            "workspace_revision": 5,
            "started_at": _STARTED_AT.isoformat(),
            "completed_at": _COMPLETED_AT.isoformat(),
        },
        "path": "/workspace/write.txt",
        "created": False,
        "changed": True,
        "previous_hash": "2" * 64,
        "current_hash": "3" * 64,
    }
    assert [item["path"] for item in patch_output["result"]["files"]] == [
        "/workspace/a.txt",
        "/workspace/b.txt",
    ]
    assert patch_output["result"]["metadata"]["workspace_revision"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write_condition", "expected_hash", "expected_type"),
    [
        ("any_current_state", None, AnyCurrentState),
        ("path_must_not_exist", None, PathMustNotExist),
    ],
)
async def test_write_conditions_map_without_implicit_overwrite(
    write_condition: str,
    expected_hash: str | None,
    expected_type: type[Any],
) -> None:
    capability, domain = _bound_capability()
    write_file = capability.tools()[2]
    payload: dict[str, object] = {
        "path": "/workspace/write.txt",
        "content": "content",
        "write_condition": write_condition,
        "create_parents": False,
    }
    if expected_hash is not None:
        payload["expected_hash"] = expected_hash

    await _invoke(write_file, payload)

    assert len(domain.write_requests) == 1
    assert isinstance(domain.write_requests[0].precondition, expected_type)


@pytest.mark.asyncio
async def test_expected_domain_errors_are_structured_and_cancellation_propagates() -> None:
    capability, domain = _bound_capability()
    read_file = capability.tools()[1]
    domain.failure = PathNotFoundError("/workspace/missing.txt does not exist")

    missing = await _invoke(read_file, {"path": "/workspace/missing.txt"})

    assert missing == {
        "ok": False,
        "error": {
            "category": "not_found",
            "code": "path_not_found",
            "message": "/workspace/missing.txt does not exist",
            "correctable": True,
            "retryable": False,
        },
    }

    domain.failure = SessionPolicyDenied(
        "operation denied by policy",
        reason_code="read_denied",
        operation_id=_OPERATION_ID,
    )
    denied = await _invoke(read_file, {"path": "/workspace/secret.txt"})
    assert denied["error"] == {
        "category": "policy_denied",
        "code": "session_policy_denied",
        "message": "operation denied by policy",
        "correctable": False,
        "retryable": False,
        "operation_id": str(_OPERATION_ID),
    }

    domain.failure = SessionOperationTimeout(
        "operation timed out",
        operation_id=_OPERATION_ID,
    )
    timed_out = await _invoke(read_file, {"path": "/workspace/read.txt"})
    assert timed_out["error"] == {
        "category": "timeout",
        "code": "session_operation_timeout",
        "message": "operation timed out",
        "correctable": True,
        "retryable": True,
        "operation_id": str(_OPERATION_ID),
    }

    domain.failure = SessionOperationCancelled(
        "operation cancelled",
        operation_id=_OPERATION_ID,
    )
    with pytest.raises(SessionOperationCancelled):
        await _invoke(read_file, {"path": "/workspace/read.txt"})


@pytest.mark.asyncio
async def test_invalid_tool_input_is_correctable_without_calling_domain() -> None:
    capability, domain = _bound_capability()
    write_file = capability.tools()[2]

    output = await _invoke(
        write_file,
        {
            "path": "/workspace/write.txt",
            "content": "content",
            "write_condition": "content_hash_must_equal",
            "create_parents": False,
        },
    )

    assert domain.write_requests == []
    assert output["ok"] is False
    assert output["error"]["category"] == "invalid_request"
    assert output["error"]["code"] == "invalid_tool_input"
    assert output["error"]["correctable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duplicate_path",
    [
        "/workspace/a.txt",
        "a.txt",
        "/workspace/./a.txt",
    ],
)
async def test_duplicate_patch_expected_hashes_are_correctable_without_calling_domain(
    duplicate_path: str,
) -> None:
    capability, domain = _bound_capability()
    apply_patch = capability.tools()[3]

    output = await _invoke(
        apply_patch,
        {
            "patch": "--- a.txt\n+++ a.txt\n@@ -1 +1 @@\n-old\n+new\n",
            "expected_hashes": [
                {"path": "/workspace/a.txt", "content_hash": "4" * 64},
                {"path": duplicate_path, "content_hash": "5" * 64},
            ],
        },
    )

    assert domain.patch_requests == []
    assert output == {
        "ok": False,
        "error": {
            "category": "invalid_request",
            "code": "invalid_tool_input",
            "message": "Invalid input for apply_patch",
            "correctable": True,
            "retryable": False,
        },
    }


@pytest.mark.asyncio
async def test_unexpected_failures_are_redacted_with_a_correlation_id() -> None:
    capability, domain = _bound_capability()
    domain.failure = RuntimeError("secret implementation detail")

    output = await _invoke(
        capability.tools()[1],
        {"path": "/workspace/read.txt"},
    )

    error = output["error"]
    assert error["category"] == "internal"
    assert error["code"] == "internal_error"
    assert error["message"] == "The sandbox operation failed unexpectedly"
    assert "secret implementation detail" not in json.dumps(output)
    UUID(cast(str, error["correlation_id"]))


@pytest.mark.asyncio
async def test_runner_clones_binds_and_uses_only_mem_sandbox_tools_without_network() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    sdk_session = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(owner_id="runner"),
    )
    capability = InMemorySandboxCapability()
    model = DeterministicToolModel()
    agent = SandboxAgent(
        name="deterministic",
        model=model,
        capabilities=[capability],
    )

    try:
        result = await Runner.run(
            agent,
            "write the file",
            run_config=RunConfig(
                tracing_disabled=True,
                sandbox=SandboxRunConfig(session=sdk_session),
                tool_name_collision_policy="error",
            ),
        )

        assert result.final_output == "done"
        assert capability.session is None
        assert model.tool_names == [
            ["execute", "read_file", "write_file", "apply_patch"],
            ["execute", "read_file", "write_file", "apply_patch"],
        ]
        second_input = model.inputs[1]
        assert isinstance(second_input, list)
        tool_outputs: list[dict[str, Any]] = [
            cast(dict[str, Any], item)
            for item in second_input
            if item.get("type") == "function_call_output"
        ]
        assert len(tool_outputs) == 1
        parsed_output = json.loads(cast(str, tool_outputs[0]["output"]))
        assert parsed_output["ok"] is True

        provider = cast(InMemorySandboxSession, cast(Any, sdk_session)._inner)
        created = await provider.core_session.read_file(
            ReadFileRequest(path="/workspace/from-runner.txt")
        )
        assert created.content == "runner"
        assert provider.core_session.state is SandboxSessionState.RUNNING
    finally:
        try:
            await sdk_session.aclose()
        finally:
            try:
                await client.delete(sdk_session)
            finally:
                await bundle.service.close()
