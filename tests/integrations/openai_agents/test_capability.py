from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
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

from mem_sandbox.command_executor import CommandFailureCode, EnvironmentChange
from mem_sandbox.core import (
    OperationId,
    OperationResultMetadata,
    Revision,
    SandboxError,
    SessionId,
)
from mem_sandbox.integrations.openai_agents import (
    InMemorySandboxCapability,
    InMemorySandboxClient,
    InMemorySandboxClientOptions,
    InMemorySandboxSession,
    InMemorySandboxSessionState,
)
from mem_sandbox.service import (
    OwnerId,
    ResumeSandboxRequest,
    SandboxNotFound,
    SandboxService,
)
from mem_sandbox.session import (
    ApplyPatchRequest,
    CreateSnapshotRequest,
    FileMutationResult,
    PatchMutationResult,
    ReadFileRequest,
    ReadFileResult,
    RestoreSnapshotRequest,
    SandboxSessionState,
    SessionExecuteRequest,
    SessionExecuteResult,
    SessionExpectedFileHash,
    SessionOperationCancelled,
    SessionOperationTimeout,
    SessionPolicyDenied,
    WriteFileRequest,
)
from mem_sandbox.snapshots import JsonSessionSnapshotCodec
from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    ContentHashMustEqual,
    PatchedFile,
    PathMustNotExist,
    PathNotFoundError,
    SandboxPath,
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


class _ScenarioDriver(Protocol):
    async def execute(self, command: str) -> dict[str, object]: ...

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, object]: ...

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        write_condition: Literal[
            "any_current_state",
            "path_must_not_exist",
            "content_hash_must_equal",
        ],
        expected_hash: str | None = None,
        create_parents: bool = False,
    ) -> dict[str, object]: ...

    async def apply_patch(
        self,
        patch: str,
        expected_hashes: list[dict[str, str]],
    ) -> dict[str, object]: ...


class _DirectScenarioDriver:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def execute(self, command: str) -> dict[str, object]:
        result = await self._session.execute(SessionExecuteRequest(command=command))
        return {
            "ok": True,
            "revision": result.metadata.workspace_revision.value,
            "exit_code": result.exit_code,
            "failure_code": (None if result.failure_code is None else result.failure_code.value),
            "stdout": result.stdout,
            "resulting_cwd": result.resulting_cwd.value,
            "environment_changes": [
                (change.name, change.value) for change in result.environment_changes
            ],
        }

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, object]:
        result = await self._session.read_file(
            ReadFileRequest(path=path, start_line=start_line, end_line=end_line)
        )
        return {
            "ok": True,
            "revision": result.metadata.workspace_revision.value,
            "content": result.content,
            "content_hash": result.content_hash.value,
        }

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        write_condition: Literal[
            "any_current_state",
            "path_must_not_exist",
            "content_hash_must_equal",
        ],
        expected_hash: str | None = None,
        create_parents: bool = False,
    ) -> dict[str, object]:
        if write_condition == "any_current_state":
            precondition = AnyCurrentState()
        elif write_condition == "path_must_not_exist":
            precondition = PathMustNotExist()
        else:
            assert expected_hash is not None
            precondition = ContentHashMustEqual(ContentHash(expected_hash))
        try:
            result = await self._session.write_file(
                WriteFileRequest(
                    path=path,
                    content=content,
                    precondition=precondition,
                    create_parents=create_parents,
                )
            )
        except SandboxError as error:
            return {
                "ok": False,
                "category": error.category.value,
                "code": error.code,
            }
        return {
            "ok": True,
            "revision": result.metadata.workspace_revision.value,
            "created": result.created,
            "changed": result.changed,
            "current_hash": (None if result.current_hash is None else result.current_hash.value),
        }

    async def apply_patch(
        self,
        patch: str,
        expected_hashes: list[dict[str, str]],
    ) -> dict[str, object]:
        result = await self._session.apply_patch(
            ApplyPatchRequest(
                patch=patch,
                expected_hashes=tuple(
                    SessionExpectedFileHash(
                        item["path"],
                        ContentHash(item["content_hash"]),
                    )
                    for item in expected_hashes
                ),
            )
        )
        return {
            "ok": True,
            "revision": result.metadata.workspace_revision.value,
            "files": [
                {
                    "path": item.path.value,
                    "previous_hash": item.previous_hash.value,
                    "current_hash": item.current_hash.value,
                }
                for item in result.files
            ],
        }


class _CapabilityScenarioDriver:
    def __init__(self, capability: InMemorySandboxCapability) -> None:
        self._tools = {tool.name: tool for tool in capability.tools()}

    async def execute(self, command: str) -> dict[str, object]:
        output = await _invoke(self._tools["execute"], {"command": command})
        if not output["ok"]:
            return _normalized_error(output)
        result = cast(dict[str, Any], output["result"])
        metadata = cast(dict[str, Any], result["metadata"])
        return {
            "ok": True,
            "revision": metadata["workspace_revision"],
            "exit_code": result["exit_code"],
            "failure_code": result["failure_code"],
            "stdout": result["stdout"],
            "resulting_cwd": result["resulting_cwd"],
            "environment_changes": [
                (item["name"], item["value"])
                for item in cast(list[dict[str, Any]], result["environment_changes"])
            ],
        }

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, object]:
        output = await _invoke(
            self._tools["read_file"],
            {"path": path, "start_line": start_line, "end_line": end_line},
        )
        if not output["ok"]:
            return _normalized_error(output)
        result = cast(dict[str, Any], output["result"])
        metadata = cast(dict[str, Any], result["metadata"])
        return {
            "ok": True,
            "revision": metadata["workspace_revision"],
            "content": result["content"],
            "content_hash": result["content_hash"],
        }

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        write_condition: Literal[
            "any_current_state",
            "path_must_not_exist",
            "content_hash_must_equal",
        ],
        expected_hash: str | None = None,
        create_parents: bool = False,
    ) -> dict[str, object]:
        output = await _invoke(
            self._tools["write_file"],
            {
                "path": path,
                "content": content,
                "write_condition": write_condition,
                "expected_hash": expected_hash,
                "create_parents": create_parents,
            },
        )
        if not output["ok"]:
            return _normalized_error(output)
        result = cast(dict[str, Any], output["result"])
        metadata = cast(dict[str, Any], result["metadata"])
        return {
            "ok": True,
            "revision": metadata["workspace_revision"],
            "created": result["created"],
            "changed": result["changed"],
            "current_hash": result["current_hash"],
        }

    async def apply_patch(
        self,
        patch: str,
        expected_hashes: list[dict[str, str]],
    ) -> dict[str, object]:
        output = await _invoke(
            self._tools["apply_patch"],
            {"patch": patch, "expected_hashes": expected_hashes},
        )
        if not output["ok"]:
            return _normalized_error(output)
        result = cast(dict[str, Any], output["result"])
        metadata = cast(dict[str, Any], result["metadata"])
        return {
            "ok": True,
            "revision": metadata["workspace_revision"],
            "files": result["files"],
        }


def _normalized_error(output: dict[str, Any]) -> dict[str, object]:
    error = cast(dict[str, Any], output["error"])
    return {
        "ok": False,
        "category": error["category"],
        "code": error["code"],
    }


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
async def test_model_free_capability_conformance_preserves_state_and_fork_isolation() -> None:
    direct_trace = await _run_stateful_tool_scenario(use_capability=False)
    capability_trace = await _run_stateful_tool_scenario(use_capability=True)

    assert direct_trace["first_write"] == {
        "ok": True,
        "revision": 1,
        "created": True,
        "changed": True,
        "current_hash": ContentHash.from_bytes(b"alpha\nbeta\ngamma\n").value,
    }
    assert cast(dict[str, object], direct_trace["second_write"])["revision"] == 2
    assert cast(dict[str, object], direct_trace["execute"])["stdout"] == (
        "/workspace/project\nalpha\nbeta\ngamma\n"
    )
    assert cast(dict[str, object], direct_trace["bounded"])["content"] == "beta\ngamma"
    assert cast(dict[str, object], direct_trace["patched"])["revision"] == 3
    assert direct_trace["stale"] == {
        "ok": False,
        "category": "conflict",
        "code": "stale_content",
    }
    assert cast(dict[str, object], direct_trace["after_stale"])["revision"] == 3
    assert direct_trace["checkpoint_revision"] == 3
    assert direct_trace["checkpoint_root_hash"] == direct_trace["restored_root_hash"]
    assert direct_trace["checkpoint_cwd"] == direct_trace["restored_cwd"] == "/workspace/project"
    assert direct_trace["checkpoint_mode"] == direct_trace["restored_mode"] == "base"
    assert direct_trace["restored_snapshot_matches"] is True
    assert cast(dict[str, object], direct_trace["unsupported"])["failure_code"] == (
        "command_not_found"
    )
    assert direct_trace["source_identity_preserved"] is True
    assert direct_trace["fork_identity_changed"] is True
    assert direct_trace["fork_content"] == "alpha\ndelta\ngamma"
    assert direct_trace["fork_hash"] == direct_trace["patched_hash"]
    assert direct_trace["source_cleanup"] is True
    assert direct_trace["fork_cleanup"] is True
    assert direct_trace["snapshot_count"] == 0
    assert capability_trace == direct_trace


async def _run_stateful_tool_scenario(*, use_capability: bool) -> dict[str, object]:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    sdk_session = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(owner_id="scenario-source"),
    )
    await sdk_session.start()
    provider = cast(InMemorySandboxSession, cast(Any, sdk_session)._inner)
    driver: _ScenarioDriver
    if use_capability:
        capability = InMemorySandboxCapability()
        capability.bind(sdk_session)
        driver = _CapabilityScenarioDriver(capability)
    else:
        driver = _DirectScenarioDriver(provider.core_session)

    first_write = await driver.write_file(
        "/workspace/project/app.txt",
        "alpha\nbeta\ngamma\n",
        write_condition="path_must_not_exist",
        create_parents=True,
    )
    second_write = await driver.write_file(
        "/workspace/project/second.txt",
        "second\n",
        write_condition="path_must_not_exist",
    )
    executed = await driver.execute("cd /workspace/project; export MODE=base; pwd; cat app.txt")
    bounded = await driver.read_file(
        "app.txt",
        start_line=2,
        end_line=3,
    )
    bounded_hash = cast(str, bounded["content_hash"])
    patched = await driver.apply_patch(
        (
            "--- /workspace/project/app.txt\n"
            "+++ /workspace/project/app.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " alpha\n"
            "-beta\n"
            "+delta\n"
            " gamma\n"
        ),
        [
            {
                "path": "app.txt",
                "content_hash": bounded_hash,
            }
        ],
    )
    stale = await driver.write_file(
        "app.txt",
        "stale\n",
        write_condition="content_hash_must_equal",
        expected_hash=bounded_hash,
    )
    after_stale = await driver.read_file("app.txt")
    checkpoint = await provider.core_session.create_snapshot(CreateSnapshotRequest())
    checkpoint_data = await bundle.snapshot_store.load(checkpoint.snapshot_ref)
    checkpoint_state = JsonSessionSnapshotCodec().decode(checkpoint_data)

    await driver.write_file(
        "app.txt",
        "mutated\n",
        write_condition="content_hash_must_equal",
        expected_hash=cast(str, after_stale["content_hash"]),
    )
    await driver.execute("cd /workspace; export MODE=mutated")
    restored = await provider.core_session.restore_snapshot(
        RestoreSnapshotRequest(snapshot_ref=checkpoint.snapshot_ref)
    )
    restored_content = await driver.read_file("/workspace/project/app.txt")
    restored_checkpoint = await provider.core_session.create_snapshot(CreateSnapshotRequest())
    restored_data = await bundle.snapshot_store.load(restored_checkpoint.snapshot_ref)
    restored_state = JsonSessionSnapshotCodec().decode(restored_data)
    unsupported = await driver.execute('python -c \'open("host-canary", "w")\'')

    source_session_id = provider.core_session.session_id
    await client.delete(sdk_session)
    source_missing = False
    try:
        await bundle.service.get_session(provider.handle)
    except SandboxNotFound:
        source_missing = True
    fork_handle = await bundle.service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("scenario-fork"),
            snapshot_ref=checkpoint.snapshot_ref,
        )
    )
    fork = await bundle.service.get_session(fork_handle)
    fork_content = await fork.read_file(ReadFileRequest(path="app.txt"))
    await fork.write_file(
        WriteFileRequest(
            path="fork.txt",
            content="fork\n",
            create_parents=False,
        )
    )
    await fork.close()
    await bundle.service.delete(fork_handle)
    fork_missing = False
    try:
        await bundle.service.get_session(fork_handle)
    except SandboxNotFound:
        fork_missing = True
    await bundle.snapshot_store.delete(checkpoint.snapshot_ref)
    await bundle.snapshot_store.delete(restored_checkpoint.snapshot_ref)
    snapshot_count = (await bundle.snapshot_store.stats()).snapshot_count
    await bundle.service.close()

    patched_files = cast(list[dict[str, Any]], patched["files"])
    return {
        "first_write": first_write,
        "second_write": second_write,
        "execute": executed,
        "bounded": bounded,
        "patched": patched,
        "stale": stale,
        "after_stale": after_stale,
        "checkpoint_revision": checkpoint_state.workspace.workspace_revision.value,
        "checkpoint_root_hash": checkpoint_state.workspace.root_hash.value,
        "checkpoint_cwd": checkpoint_state.cwd.value,
        "checkpoint_mode": checkpoint_state.approved_environment.get("MODE"),
        "restored_revision": restored.metadata.workspace_revision.value,
        "restored_content": restored_content,
        "restored_root_hash": restored_state.workspace.root_hash.value,
        "restored_cwd": provider.core_session.cwd.value,
        "restored_mode": provider.core_session.environment.get("MODE"),
        "restored_snapshot_matches": restored_checkpoint.content_hash == checkpoint.content_hash,
        "unsupported": unsupported,
        "source_identity_preserved": restored.metadata.session_id == source_session_id,
        "fork_identity_changed": fork.session_id != source_session_id,
        "fork_content": fork_content.content,
        "fork_hash": fork_content.content_hash.value,
        "patched_hash": patched_files[0]["current_hash"],
        "source_cleanup": source_missing,
        "fork_cleanup": fork_missing,
        "snapshot_count": snapshot_count,
    }


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

    result = await Runner.run(
        agent,
        "write the file",
        run_config=RunConfig(
            tracing_disabled=True,
            sandbox=SandboxRunConfig(session=sdk_session),
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

    await client.delete(sdk_session)
    await bundle.service.close()
