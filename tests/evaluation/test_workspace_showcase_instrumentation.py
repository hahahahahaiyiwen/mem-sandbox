from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import cast

import pytest
from agents import ModelResponse, Usage
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import TResponseInputItem, TResponseOutputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.sandbox import Manifest
from agents.tool import FunctionTool, Tool
from agents.tool_context import ToolContext
from evaluations.workspace_showcase.instrumentation import (
    InstrumentedModel,
    PerformanceClock,
    ProviderCostSource,
    WorkspaceShowcaseRecorder,
)
from evaluations.workspace_showcase.schema import (
    SCHEMA_VERSION,
    ArtifactVerification,
    FailureAttribution,
    FailureCategory,
    MetadataSnapshot,
    Outcome,
    StageOutcome,
    TimingCategory,
    TokenCompleteness,
    VerificationCheckKind,
    WorkspaceShowcaseRecord,
    build_run_metadata,
)
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_prompt_param import ResponsePromptParam
from tests.integrations.openai_agents.support import create_service_bundle

from mem_sandbox_openai_agents import InMemorySandboxClient

_CANARY = "CANARY-live-evaluation-secret-9c862"
_HASH = "a" * 64


class FakeClock(PerformanceClock):
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def now_ns(self) -> int:
        return next(self._values)


class QueueModel(Model):
    def __init__(self, *results: ModelResponse | BaseException) -> None:
        self._results = iter(results)
        self.call_count = 0
        self.closed = False
        self.cleaned_owner: object | None = None

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
        self.call_count += 1
        result = next(self._results)
        if isinstance(result, BaseException):
            raise result
        return result

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

    async def close(self) -> None:
        self.closed = True

    async def _cleanup_on_run_end(self, owner: object) -> None:
        self.cleaned_owner = owner


class QueueCostSource(ProviderCostSource):
    def __init__(self, *costs: Decimal | None) -> None:
        self._costs = iter(costs)
        self.calls = 0

    def cost_for_response(self, response: ModelResponse) -> Decimal | None:
        _ = response
        self.calls += 1
        return next(self._costs)


def _response(text: str, usage: Usage) -> ModelResponse:
    message = ResponseOutputMessage(
        id="message_1",
        content=[
            ResponseOutputText(
                annotations=[],
                text=text,
                type="output_text",
            )
        ],
        role="assistant",
        status="completed",
        type="message",
    )
    return ModelResponse(
        output=cast(list[TResponseOutputItem], [message]),
        usage=usage,
        response_id="response_1",
    )


async def _call(model: Model, input_text: str = "input") -> ModelResponse:
    return await model.get_response(
        None,
        input_text,
        ModelSettings(),
        [],
        None,
        [],
        ModelTracing.DISABLED,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )


def _metadata():
    return build_run_metadata(
        provider="openai",
        model_or_deployment="gpt-test",
        provider_api_version=None,
        approval_reference="issue-76-comment-1",
        scenario="document-review",
        fixture_version=1,
        snapshot=MetadataSnapshot(
            started_at_utc="2026-09-11T20:00:00+00:00",
            source_commit="c" * 40,
            source_dirty=False,
            mem_sandbox_version="0.2.0",
            mem_sandbox_openai_agents_version="0.2.0",
            openai_agents_version="0.22.0",
            openai_version="2.44.0",
            python_implementation="CPython",
            python_version="3.12.10",
            operating_system="Windows",
            architecture="AMD64",
        ),
    )


def _record(
    recorder: WorkspaceShowcaseRecorder,
    *,
    outcome: Outcome = Outcome.SUCCEEDED,
    failure: FailureAttribution | None = None,
) -> WorkspaceShowcaseRecord:
    return WorkspaceShowcaseRecord(
        schema_version=SCHEMA_VERSION,
        metadata=_metadata(),
        outcome=outcome,
        stages=(StageOutcome(1, "editor", outcome),),
        artifacts=(
            ArtifactVerification(
                path="/workspace/result.txt",
                check_kinds=(VerificationCheckKind.OUTPUT_CORRECTNESS,),
                expected_sha256=_HASH,
                actual_sha256=_HASH,
                matched=True,
            ),
        ),
        tool_interactions=recorder.tool_interactions,
        timings=recorder.timings,
        usage=recorder.usage_summary(),
        failure=failure,
    )


@pytest.mark.asyncio
async def test_async_timing_records_success_failure_and_cancellation_then_rethrows() -> None:
    recorder = WorkspaceShowcaseRecorder(FakeClock(10, 25, 30, 50, 60, 90))

    async def succeed() -> int:
        return 7

    async def fail() -> int:
        raise RuntimeError("failure")

    async def cancel() -> int:
        raise asyncio.CancelledError

    assert (
        await recorder.time_async(
            TimingCategory.CLEANUP,
            "service.close",
            succeed,
        )
        == 7
    )
    with pytest.raises(RuntimeError):
        await recorder.time_async(TimingCategory.HOST_VERIFICATION, "verify", fail)
    with pytest.raises(asyncio.CancelledError):
        await recorder.time_async(TimingCategory.SNAPSHOT_RESTORE, "resume", cancel)

    assert [(sample.outcome, sample.duration_ns) for sample in recorder.timings] == [
        (Outcome.SUCCEEDED, 15),
        (Outcome.FAILED, 20),
        (Outcome.CANCELLED, 30),
    ]


@pytest.mark.asyncio
async def test_model_wrapper_delegates_and_aggregates_partial_usage_and_injected_cost() -> None:
    cost_source = QueueCostSource(None, Decimal("0.125"))
    recorder = WorkspaceShowcaseRecorder(
        FakeClock(100, 160, 200, 275, 300, 330),
        cost_source=cost_source,
    )
    error = RuntimeError(_CANARY)
    delegate = QueueModel(
        _response(_CANARY, Usage()),
        _response(
            "ok",
            Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15),
        ),
        error,
    )
    wrapped = InstrumentedModel(delegate, stage="editor", recorder=recorder)

    assert (await _call(wrapped, _CANARY)).response_id == "response_1"
    assert (await _call(wrapped)).usage.total_tokens == 15
    with pytest.raises(RuntimeError) as raised:
        await _call(wrapped)
    await wrapped.close()
    owner = object()
    await wrapped._cleanup_on_run_end(owner)  # pyright: ignore[reportPrivateUsage]

    usage = recorder.usage_summary()
    assert usage.model_call_count == 3
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (10, 5, 15)
    assert usage.token_completeness is TokenCompleteness.PARTIAL
    assert usage.provider_cost == "0.125"
    assert usage.cost_completeness is TokenCompleteness.PARTIAL
    assert cost_source.calls == 2
    assert delegate.closed
    assert delegate.cleaned_owner is owner
    assert [(sample.outcome, sample.duration_ns) for sample in recorder.timings] == [
        (Outcome.SUCCEEDED, 60),
        (Outcome.SUCCEEDED, 75),
        (Outcome.FAILED, 30),
    ]

    failure = FailureAttribution.from_exception(
        FailureCategory.PROVIDER_INTEGRATION,
        raised.value,
        stage="editor",
    )
    assert (
        _CANARY
        not in _record(
            recorder,
            outcome=Outcome.FAILED,
            failure=failure,
        ).to_json()
    )


@pytest.mark.asyncio
async def test_default_cost_source_never_invents_provider_cost() -> None:
    recorder = WorkspaceShowcaseRecorder(FakeClock(1, 2))
    wrapped = recorder.model_for_stage(
        "editor",
        QueueModel(
            _response(
                "ok",
                Usage(requests=1, input_tokens=2, output_tokens=3, total_tokens=5),
            )
        ),
    )

    await _call(wrapped)

    usage = recorder.usage_summary()
    assert usage.provider_cost is None
    assert usage.cost_completeness is TokenCompleteness.UNAVAILABLE


@pytest.mark.asyncio
async def test_model_wrapper_records_cancellation_and_rethrows() -> None:
    recorder = WorkspaceShowcaseRecorder(FakeClock(10, 40))
    wrapped = recorder.stage_model_factory(QueueModel(asyncio.CancelledError(_CANARY)))("editor")

    with pytest.raises(asyncio.CancelledError):
        await _call(wrapped)

    assert recorder.usage_summary().model_call_count == 1
    assert recorder.usage_summary().token_completeness is TokenCompleteness.UNAVAILABLE
    assert len(recorder.timings) == 1
    assert recorder.timings[0].outcome is Outcome.CANCELLED
    assert recorder.timings[0].duration_ns == 30


@pytest.mark.asyncio
async def test_tool_wrapper_never_retains_inputs_outputs_or_exception_messages() -> None:
    recorder = WorkspaceShowcaseRecorder(FakeClock(10, 20, 30, 50, 60, 90))

    async def secret_tool(_context: ToolContext[object], input_json: str) -> str:
        assert _CANARY in input_json
        return json.dumps({"ok": True, "result": {"content": _CANARY}})

    async def failing_tool(_context: ToolContext[object], _input_json: str) -> str:
        raise RuntimeError(_CANARY)

    async def cancelled_tool(_context: ToolContext[object], _input_json: str) -> str:
        raise asyncio.CancelledError(_CANARY)

    base = FunctionTool("read_file", "read", {}, secret_tool)
    failed = FunctionTool("write_file", "write", {}, failing_tool)
    cancelled = FunctionTool("apply_patch", "patch", {}, cancelled_tool)
    context = cast(ToolContext[object], object())

    assert _CANARY in cast(
        str,
        await recorder.instrument_function_tool("editor", base).on_invoke_tool(
            context,
            json.dumps({"path": "/workspace/input.txt", "content": _CANARY}),
        ),
    )
    with pytest.raises(RuntimeError):
        await recorder.instrument_function_tool("editor", failed).on_invoke_tool(context, "{}")
    with pytest.raises(asyncio.CancelledError):
        await recorder.instrument_function_tool("editor", cancelled).on_invoke_tool(context, "{}")

    failure = FailureAttribution.from_exception(
        FailureCategory.MEM_SANDBOX,
        RuntimeError(_CANARY),
        stage="editor",
    )
    serialized = _record(recorder, outcome=Outcome.FAILED, failure=failure).to_json()
    assert _CANARY not in serialized
    assert [item.outcome for item in recorder.tool_interactions] == [
        Outcome.SUCCEEDED,
        Outcome.FAILED,
        Outcome.CANCELLED,
    ]


@pytest.mark.asyncio
async def test_token_fields_are_aggregated_without_substituting_omitted_zeros() -> None:
    recorder = WorkspaceShowcaseRecorder(FakeClock(10, 20, 30, 40))
    wrapped = InstrumentedModel(
        QueueModel(
            _response(
                "first",
                Usage(requests=1, input_tokens=17, output_tokens=0, total_tokens=0),
            ),
            _response(
                "second",
                Usage(requests=1, input_tokens=0, output_tokens=8, total_tokens=10),
            ),
        ),
        stage="editor",
        recorder=recorder,
    )

    await _call(wrapped)
    await _call(wrapped)

    usage = recorder.usage_summary()
    assert usage.model_call_count == 2
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (17, 8, 10)
    assert usage.token_completeness is TokenCompleteness.PARTIAL


@pytest.mark.asyncio
async def test_stage_capability_preserves_four_tools_and_links_immediate_same_tool_repair() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    sdk_session = await client.create(manifest=Manifest())
    recorder = WorkspaceShowcaseRecorder(FakeClock(10, 20, 30, 45))
    configured_capability = recorder.stage_capability_factory()("editor")
    capability = configured_capability.clone()
    capability.bind(sdk_session)
    tools = capability.tools()
    context = cast(ToolContext[object], object())

    try:
        assert [tool.name for tool in tools] == [
            "execute",
            "read_file",
            "write_file",
            "apply_patch",
        ]
        assert all(isinstance(tool, FunctionTool) for tool in tools)

        execute = cast(FunctionTool, tools[0])
        unsupported = cast(
            str,
            await execute.on_invoke_tool(
                context,
                json.dumps({"command": "python3 -c 'print(3)'"}),
            ),
        )
        repaired = cast(
            str,
            await execute.on_invoke_tool(
                context,
                json.dumps({"command": f"echo {_CANARY}"}),
            ),
        )

        assert json.loads(unsupported)["result"]["failure_code"] == "command_not_found"
        assert json.loads(repaired)["ok"] is True
        refreshed_execute = cast(FunctionTool, capability.tools()[0])
        assert execute.params_json_schema == refreshed_execute.params_json_schema
        assert recorder.tool_interactions[0].unsupported
        assert recorder.tool_interactions[0].outcome is Outcome.FAILED
        assert recorder.tool_interactions[0].error_code == "command_not_found"
        assert recorder.tool_interactions[1].repair_of_sequence == 1
        assert [sample.category for sample in recorder.timings] == [
            TimingCategory.WORKSPACE_EXECUTE,
            TimingCategory.WORKSPACE_EXECUTE,
        ]
        assert _CANARY not in _record(recorder).to_json()
    finally:
        try:
            await sdk_session.aclose()
        finally:
            try:
                await client.delete(sdk_session)
            finally:
                await bundle.service.close()
