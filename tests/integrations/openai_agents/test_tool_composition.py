from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, cast

import pytest
from agents import ModelResponse, RunConfig, Runner, Usage, function_tool
from agents.agent_output import AgentOutputSchemaBase
from agents.exceptions import UserError
from agents.handoffs import Handoff
from agents.items import TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.session import SandboxSession as OpenAISandboxSession
from agents.tool import FunctionTool, Tool
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam
from pydantic import BaseModel, ConfigDict, Field

from mem_sandbox.service import SandboxNotFound
from mem_sandbox.session import ReadFileRequest, SandboxSessionState, SessionClosed
from mem_sandbox_openai_agents import (
    InMemorySandboxCapability,
    InMemorySandboxClient,
    InMemorySandboxClientOptions,
    InMemorySandboxSession,
)

from .support import RecordingService, create_service_bundle

_RELEASE_ID = "release-2026-09"
_HOST_TOOL_NAME = "lookup_release_status"
_ARTIFACT_PATH = "/workspace/release-status.json"
_ARTIFACT_CONTENT = '{"release_id":"release-2026-09","status":"approved"}'
_EXPECTED_TOOL_NAMES = [
    _HOST_TOOL_NAME,
    "execute",
    "read_file",
    "write_file",
    "apply_patch",
]


class ReleaseStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    release_id: str = Field(min_length=1)


class ReleaseStatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    release_id: str
    status: Literal["approved", "blocked"]


class ReleaseStatusGateway(Protocol):
    async def lookup(self, request: ReleaseStatusRequest) -> ReleaseStatusResult: ...


class HostReleaseStatusError(RuntimeError):
    pass


class RecordingReleaseStatusGateway:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.requests: list[ReleaseStatusRequest] = []

    async def lookup(self, request: ReleaseStatusRequest) -> ReleaseStatusResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return ReleaseStatusResult(release_id=request.release_id, status="approved")


def _release_status_tool(
    gateway: ReleaseStatusGateway,
    *,
    name: str = _HOST_TOOL_NAME,
) -> FunctionTool:
    @function_tool(
        name_override=name,
        failure_error_function=None,
    )
    async def lookup_release_status(
        request: ReleaseStatusRequest,
    ) -> ReleaseStatusResult:
        """Look up release status through the host-owned gateway.

        Args:
            request: Narrow release identity owned by the host application.
        """
        return await gateway.lookup(request)

    return lookup_release_status


class CompositionModel(Model):
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
                    arguments=json.dumps({"request": {"release_id": _RELEASE_ID}}),
                    call_id="host_lookup",
                    name=_HOST_TOOL_NAME,
                    type="function_call",
                    status="completed",
                )
            ]
        elif self.calls == 2:
            output = [
                ResponseFunctionToolCall(
                    arguments=json.dumps(
                        {
                            "path": _ARTIFACT_PATH,
                            "content": _ARTIFACT_CONTENT,
                            "write_condition": "path_must_not_exist",
                            "create_parents": False,
                        }
                    ),
                    call_id="sandbox_write",
                    name="write_file",
                    type="function_call",
                    status="completed",
                )
            ]
        elif self.calls == 3:
            output = [
                ResponseFunctionToolCall(
                    arguments=json.dumps({"path": _ARTIFACT_PATH}),
                    call_id="sandbox_read",
                    name="read_file",
                    type="function_call",
                    status="completed",
                )
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="composition_complete",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="release status recorded",
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
            response_id=f"composition_{self.calls}",
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


def _create_agent(
    *,
    model: Model,
    gateway: ReleaseStatusGateway,
    host_tool_name: str = _HOST_TOOL_NAME,
) -> tuple[SandboxAgent[Any], FunctionTool]:
    host_tool = _release_status_tool(gateway, name=host_tool_name)
    return (
        SandboxAgent(
            name="host-tool-composition",
            model=model,
            tools=[host_tool],
            capabilities=[InMemorySandboxCapability()],
        ),
        host_tool,
    )


def _run_config(session: OpenAISandboxSession) -> RunConfig:
    return RunConfig(
        tracing_disabled=True,
        sandbox=SandboxRunConfig(session=session),
        tool_name_collision_policy="error",
    )


async def _close_session_and_backend(
    *,
    client: InMemorySandboxClient,
    session: OpenAISandboxSession,
    service: RecordingService,
    provider: InMemorySandboxSession,
) -> None:
    try:
        await session.aclose()
        with pytest.raises(SessionClosed, match="sandbox backend is closed"):
            provider.require_available()
        await client.delete(session)
        assert provider.core_session.state is SandboxSessionState.CLOSED
        assert service.deleted_handles == service.created_handles
        with pytest.raises(SandboxNotFound):
            await service.delegate.get_session(provider.handle)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_runner_composes_typed_host_tool_with_only_mem_sandbox_capability() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    session = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(owner_id="host-tool-composition"),
    )
    provider = cast(InMemorySandboxSession, cast(Any, session)._inner)
    gateway = RecordingReleaseStatusGateway()
    model = CompositionModel()
    agent, host_tool = _create_agent(model=model, gateway=gateway)
    get_session_calls_before_run = service.get_session_calls

    try:
        result = await Runner.run(
            agent,
            "Look up the release status and record it in the workspace.",
            run_config=_run_config(session),
        )

        assert result.final_output == "release status recorded"
        assert gateway.requests == [ReleaseStatusRequest(release_id=_RELEASE_ID)]
        assert service.get_session_calls == get_session_calls_before_run
        assert list(ReleaseStatusRequest.model_fields) == ["release_id"]
        assert set(host_tool.params_json_schema["properties"]) == {"request"}
        serialized_schema = json.dumps(host_tool.params_json_schema)
        assert "sandbox_handle" not in serialized_schema
        assert "session_id" not in serialized_schema
        assert model.tool_names == [_EXPECTED_TOOL_NAMES] * 4
        assert "exec_command" not in _EXPECTED_TOOL_NAMES
        assert "view_image" not in _EXPECTED_TOOL_NAMES
        assert "compact" not in _EXPECTED_TOOL_NAMES

        artifact = await provider.core_session.read_file(ReadFileRequest(path=_ARTIFACT_PATH))
        assert artifact.content == _ARTIFACT_CONTENT
    finally:
        await _close_session_and_backend(
            client=client,
            session=session,
            service=service,
            provider=provider,
        )


@pytest.mark.asyncio
async def test_duplicate_host_and_sandbox_tool_names_fail_before_model_or_tool_call() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    session = await client.create(options=InMemorySandboxClientOptions(owner_id="conflict"))
    provider = cast(InMemorySandboxSession, cast(Any, session)._inner)
    gateway = RecordingReleaseStatusGateway()
    model = CompositionModel()
    agent, _ = _create_agent(
        model=model,
        gateway=gateway,
        host_tool_name="read_file",
    )

    try:
        with pytest.raises(
            UserError,
            match=r"tool name `read_file` is used by multiple tools",
        ):
            await Runner.run(
                agent,
                "This ambiguous composition must not run.",
                run_config=_run_config(session),
            )

        assert model.calls == 0
        assert gateway.requests == []
    finally:
        await _close_session_and_backend(
            client=client,
            session=session,
            service=service,
            provider=provider,
        )


@pytest.mark.asyncio
async def test_host_tool_failure_preserves_host_boundary_and_cleans_sandbox() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    session = await client.create(options=InMemorySandboxClientOptions(owner_id="host-failure"))
    provider = cast(InMemorySandboxSession, cast(Any, session)._inner)
    failure = HostReleaseStatusError("host release-status provider unavailable")
    gateway = RecordingReleaseStatusGateway(failure=failure)
    model = CompositionModel()
    agent, _ = _create_agent(model=model, gateway=gateway)

    try:
        with pytest.raises(
            UserError,
            match=(
                "Error running tool lookup_release_status: host release-status provider unavailable"
            ),
        ) as captured:
            await Runner.run(
                agent,
                "Look up the release status.",
                run_config=_run_config(session),
            )

        assert captured.value.__cause__ is failure
        assert model.tool_names == [_EXPECTED_TOOL_NAMES]
        assert gateway.requests == [ReleaseStatusRequest(release_id=_RELEASE_ID)]
        assert provider.core_session.state is SandboxSessionState.RUNNING
    finally:
        await _close_session_and_backend(
            client=client,
            session=session,
            service=service,
            provider=provider,
        )
