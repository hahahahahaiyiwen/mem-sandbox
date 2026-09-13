"""Secret-safe timing and interaction instrumentation for live evaluations."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Protocol, cast

from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.retry import ModelRetryAdvice, ModelRetryAdviceRequest
from agents.tool import FunctionTool, Tool
from agents.tool_context import ToolContext
from agents.usage import Usage
from openai.types.responses.response_prompt_param import ResponsePromptParam
from pydantic import PrivateAttr

from evaluations.workspace_showcase.schema import (
    Completeness,
    Outcome,
    TimingCategory,
    TimingSample,
    ToolInteraction,
    UsageSummary,
)
from mem_sandbox.session import SessionOperationCancelled
from mem_sandbox_openai_agents import InMemorySandboxCapability

_TOOL_TIMING_CATEGORIES: dict[str, TimingCategory] = {
    "execute": TimingCategory.WORKSPACE_EXECUTE,
    "read_file": TimingCategory.WORKSPACE_READ,
    "write_file": TimingCategory.WORKSPACE_MUTATION,
    "apply_patch": TimingCategory.WORKSPACE_MUTATION,
}
_UNSUPPORTED_CODES = frozenset(
    {
        "command_not_found",
        "unsupported_command",
        "unsupported_syntax",
    }
)
_STABLE_CODE_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


class PerformanceClock(Protocol):
    def now_ns(self) -> int:
        """Return a monotonic performance timestamp in nanoseconds."""
        ...


class SystemPerformanceClock:
    def now_ns(self) -> int:
        return time.perf_counter_ns()


class ProviderCostSource(Protocol):
    def cost_for_response(self, response: ModelResponse) -> Decimal | None:
        """Return provider-reported cost for one response, or None when unavailable."""
        ...


class NoProviderCostSource:
    def cost_for_response(self, response: ModelResponse) -> Decimal | None:
        _ = response
        return None


@dataclass(frozen=True, slots=True)
class _ToolResultClassification:
    outcome: Outcome
    error_category: str | None
    error_code: str | None
    unsupported: bool


class WorkspaceShowcaseRecorder:
    def __init__(
        self,
        clock: PerformanceClock | None = None,
        *,
        cost_source: ProviderCostSource | None = None,
    ) -> None:
        self._clock = clock or SystemPerformanceClock()
        self._cost_source = cost_source or NoProviderCostSource()
        self._timings: list[TimingSample] = []
        self._tool_interactions: list[ToolInteraction] = []
        self._model_call_count = 0
        self._input_token_observation_count = 0
        self._output_token_observation_count = 0
        self._total_token_observation_count = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._cost_observation_count = 0
        self._provider_cost = Decimal(0)

    @property
    def timings(self) -> tuple[TimingSample, ...]:
        return tuple(self._timings)

    @property
    def tool_interactions(self) -> tuple[ToolInteraction, ...]:
        return tuple(self._tool_interactions)

    async def time_async[Result](
        self,
        category: TimingCategory,
        operation: str,
        action: Callable[[], Awaitable[Result]],
        *,
        stage: str | None = None,
    ) -> Result:
        started = self._clock.now_ns()
        try:
            result = await action()
        except asyncio.CancelledError:
            self._append_timing(
                category,
                operation,
                stage,
                Outcome.CANCELLED,
                self._elapsed_since(started),
            )
            raise
        except Exception:
            self._append_timing(
                category,
                operation,
                stage,
                Outcome.FAILED,
                self._elapsed_since(started),
            )
            raise
        self._append_timing(
            category,
            operation,
            stage,
            Outcome.SUCCEEDED,
            self._elapsed_since(started),
        )
        return result

    def observe_lifecycle_timing(
        self,
        *,
        category: TimingCategory,
        operation: str,
        stage: str | None,
        outcome: Outcome,
        duration_ns: int,
    ) -> None:
        self._append_timing(category, operation, stage, outcome, duration_ns)

    def model_for_stage(self, stage: str, model: Model) -> InstrumentedModel:
        return InstrumentedModel(model, stage=stage, recorder=self)

    def stage_model_factory(self, model: Model) -> Callable[[str], Model]:
        def create(stage: str) -> Model:
            return self.model_for_stage(stage, model)

        return create

    def capability_for_stage(self, stage: str) -> InstrumentedInMemorySandboxCapability:
        return InstrumentedInMemorySandboxCapability(stage=stage, recorder=self)

    def stage_capability_factory(
        self,
    ) -> Callable[[str], InMemorySandboxCapability]:
        def create(stage: str) -> InMemorySandboxCapability:
            return self.capability_for_stage(stage)

        return create

    def instrument_function_tool(self, stage: str, tool: FunctionTool) -> FunctionTool:
        _validate_stage(stage)
        category = _tool_category(tool.name)
        original = tool.on_invoke_tool

        async def invoke(context: ToolContext[Any], input_json: str) -> Any:
            started = self._clock.now_ns()
            try:
                output = await original(context, input_json)
            except asyncio.CancelledError:
                duration_ns = self._elapsed_since(started)
                self._append_tool_interaction(
                    stage=stage,
                    tool_name=tool.name,
                    duration_ns=duration_ns,
                    outcome=Outcome.CANCELLED,
                    error_category="cancellation",
                    error_code=None,
                    unsupported=False,
                )
                self._append_timing(
                    category,
                    tool.name,
                    stage,
                    Outcome.CANCELLED,
                    duration_ns,
                )
                raise
            except SessionOperationCancelled:
                duration_ns = self._elapsed_since(started)
                self._append_tool_interaction(
                    stage=stage,
                    tool_name=tool.name,
                    duration_ns=duration_ns,
                    outcome=Outcome.CANCELLED,
                    error_category="cancellation",
                    error_code="session_operation_cancelled",
                    unsupported=False,
                )
                self._append_timing(
                    category,
                    tool.name,
                    stage,
                    Outcome.CANCELLED,
                    duration_ns,
                )
                raise
            except Exception:
                duration_ns = self._elapsed_since(started)
                self._append_tool_interaction(
                    stage=stage,
                    tool_name=tool.name,
                    duration_ns=duration_ns,
                    outcome=Outcome.FAILED,
                    error_category="exception",
                    error_code=None,
                    unsupported=False,
                )
                self._append_timing(
                    category,
                    tool.name,
                    stage,
                    Outcome.FAILED,
                    duration_ns,
                )
                raise

            duration_ns = self._elapsed_since(started)
            classification = _classify_tool_result(tool.name, output)
            self._append_tool_interaction(
                stage=stage,
                tool_name=tool.name,
                duration_ns=duration_ns,
                outcome=classification.outcome,
                error_category=classification.error_category,
                error_code=classification.error_code,
                unsupported=classification.unsupported,
            )
            self._append_timing(
                category,
                tool.name,
                stage,
                classification.outcome,
                duration_ns,
            )
            return output

        return replace(tool, on_invoke_tool=invoke)

    def usage_summary(self) -> UsageSummary:
        token_completeness = _token_completeness(
            self._model_call_count,
            (
                self._input_token_observation_count,
                self._output_token_observation_count,
                self._total_token_observation_count,
            ),
        )
        cost_completeness = _completeness(
            self._model_call_count,
            self._cost_observation_count,
        )
        return UsageSummary(
            model_call_count=self._model_call_count,
            input_tokens=(None if self._input_token_observation_count == 0 else self._input_tokens),
            output_tokens=(
                None if self._output_token_observation_count == 0 else self._output_tokens
            ),
            total_tokens=(None if self._total_token_observation_count == 0 else self._total_tokens),
            token_completeness=token_completeness,
            provider_cost=(
                None
                if cost_completeness is Completeness.UNAVAILABLE
                else format(self._provider_cost, "f")
            ),
            cost_completeness=cost_completeness,
        )

    def start_model_call(self) -> int:
        self._model_call_count += 1
        return self._clock.now_ns()

    def finish_model_call(
        self,
        *,
        stage: str,
        started_ns: int,
        outcome: Outcome,
    ) -> None:
        self._append_timing(
            TimingCategory.PROVIDER_MODEL,
            "get_response",
            stage,
            outcome,
            self._elapsed_since(started_ns),
        )

    def record_model_response(self, response: ModelResponse) -> None:
        usage = response.usage
        _require_nonnegative_usage(usage)
        if usage.input_tokens > 0:
            self._input_token_observation_count += 1
            self._input_tokens += usage.input_tokens
        if usage.output_tokens > 0:
            self._output_token_observation_count += 1
            self._output_tokens += usage.output_tokens
        if usage.total_tokens > 0:
            self._total_token_observation_count += 1
            self._total_tokens += usage.total_tokens

        cost = self._cost_source.cost_for_response(response)
        if cost is None:
            return
        untrusted_cost = cast(object, cost)
        if not isinstance(untrusted_cost, Decimal):
            raise TypeError("provider cost sources must return Decimal or None")
        if not untrusted_cost.is_finite() or untrusted_cost < 0:
            raise ValueError("provider-reported cost must be finite and nonnegative")
        self._cost_observation_count += 1
        self._provider_cost += untrusted_cost

    def _append_tool_interaction(
        self,
        *,
        stage: str,
        tool_name: str,
        duration_ns: int,
        outcome: Outcome,
        error_category: str | None,
        error_code: str | None,
        unsupported: bool,
    ) -> None:
        sequence = len(self._tool_interactions) + 1
        repair_of_sequence: int | None = None
        if outcome is Outcome.SUCCEEDED and self._tool_interactions:
            previous = self._tool_interactions[-1]
            if (
                previous.outcome is Outcome.FAILED
                and previous.stage == stage
                and previous.tool_name == tool_name
            ):
                repair_of_sequence = previous.sequence
        interaction = ToolInteraction(
            sequence=sequence,
            stage=stage,
            tool_name=tool_name,
            duration_ns=duration_ns,
            outcome=outcome,
            error_category=error_category,
            error_code=error_code,
            unsupported=unsupported,
            repair_of_sequence=repair_of_sequence,
        )
        self._tool_interactions.append(interaction)

    def _append_timing(
        self,
        category: TimingCategory,
        operation: str,
        stage: str | None,
        outcome: Outcome,
        duration_ns: int,
    ) -> None:
        self._timings.append(
            TimingSample(
                category=category,
                operation=operation,
                stage=stage,
                outcome=outcome,
                duration_ns=duration_ns,
            )
        )

    def _elapsed_since(self, started_ns: int) -> int:
        completed_ns = self._clock.now_ns()
        if completed_ns < started_ns:
            raise ValueError("performance clock moved backwards")
        return completed_ns - started_ns


class InstrumentedModel(Model):
    def __init__(
        self,
        delegate: Model,
        *,
        stage: str,
        recorder: WorkspaceShowcaseRecorder,
    ) -> None:
        self._delegate = delegate
        self._stage = _validate_stage(stage)
        self._recorder = recorder

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
        started = self._recorder.start_model_call()
        try:
            response = await self._delegate.get_response(
                system_instructions,
                input,
                model_settings,
                tools,
                output_schema,
                handoffs,
                tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            )
        except asyncio.CancelledError:
            self._recorder.finish_model_call(
                stage=self._stage,
                started_ns=started,
                outcome=Outcome.CANCELLED,
            )
            raise
        except Exception:
            self._recorder.finish_model_call(
                stage=self._stage,
                started_ns=started,
                outcome=Outcome.FAILED,
            )
            raise
        self._recorder.finish_model_call(
            stage=self._stage,
            started_ns=started,
            outcome=Outcome.SUCCEEDED,
        )
        self._recorder.record_model_response(response)
        return response

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
        return self._delegate.stream_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )

    async def close(self) -> None:
        await self._delegate.close()

    async def _cleanup_on_run_end(self, owner: object) -> None:
        await self._delegate._cleanup_on_run_end(owner)  # pyright: ignore[reportPrivateUsage]

    def get_retry_advice(
        self,
        request: ModelRetryAdviceRequest,
    ) -> ModelRetryAdvice | None:
        return self._delegate.get_retry_advice(request)


class InstrumentedInMemorySandboxCapability(InMemorySandboxCapability):
    _evaluation_stage: str = PrivateAttr()
    _evaluation_recorder: WorkspaceShowcaseRecorder = PrivateAttr()

    def __init__(
        self,
        *,
        stage: str,
        recorder: WorkspaceShowcaseRecorder,
    ) -> None:
        super().__init__()
        self._evaluation_stage = _validate_stage(stage)
        self._evaluation_recorder = recorder

    def clone(self) -> InstrumentedInMemorySandboxCapability:
        cloned = super().clone()
        if not isinstance(cloned, InstrumentedInMemorySandboxCapability):
            raise TypeError("instrumented capability clone changed type")
        cloned._evaluation_stage = self._evaluation_stage
        cloned._evaluation_recorder = self._evaluation_recorder
        return cloned

    def tools(self) -> list[Tool]:
        tools = super().tools()
        if [tool.name for tool in tools] != list(_TOOL_TIMING_CATEGORIES):
            raise RuntimeError("MemSandbox capability tool surface changed")
        instrumented: list[Tool] = []
        for tool in tools:
            if not isinstance(tool, FunctionTool):
                raise TypeError("MemSandbox capability must expose FunctionTool values")
            instrumented.append(
                self._evaluation_recorder.instrument_function_tool(
                    self._evaluation_stage,
                    tool,
                )
            )
        return instrumented


def _tool_category(tool_name: str) -> TimingCategory:
    try:
        return _TOOL_TIMING_CATEGORIES[tool_name]
    except KeyError as error:
        raise ValueError(f"unsupported instrumented tool name: {tool_name}") from error


def _validate_stage(stage: str) -> str:
    TimingSample(
        category=TimingCategory.PROVIDER_MODEL,
        operation="get_response",
        stage=stage,
        outcome=Outcome.INCOMPLETE,
        duration_ns=0,
    )
    return stage


def _classify_tool_result(tool_name: str, output: object) -> _ToolResultClassification:
    if not isinstance(output, str):
        return _invalid_tool_result()
    try:
        parsed = cast(object, json.loads(output))
    except json.JSONDecodeError:
        return _invalid_tool_result()
    if not isinstance(parsed, dict):
        return _invalid_tool_result()
    untyped_value = cast(dict[object, object], parsed)
    if not all(isinstance(key, str) for key in untyped_value):
        return _invalid_tool_result()
    value = cast(dict[str, object], untyped_value)
    ok = value.get("ok")
    if type(ok) is not bool:
        return _invalid_tool_result()
    if not ok:
        error = value.get("error")
        if not isinstance(error, dict):
            return _invalid_tool_result()
        untyped_error = cast(dict[object, object], error)
        if not all(isinstance(key, str) for key in untyped_error):
            return _invalid_tool_result()
        details = cast(dict[str, object], untyped_error)
        category = _stable_code_or_none(details.get("category"))
        code = _stable_code_or_none(details.get("code"))
        unsupported = category == "unsupported" or code in _UNSUPPORTED_CODES
        return _ToolResultClassification(Outcome.FAILED, category, code, unsupported)
    if tool_name != "execute":
        return _ToolResultClassification(Outcome.SUCCEEDED, None, None, False)
    result = value.get("result")
    if not isinstance(result, dict):
        return _invalid_tool_result()
    untyped_result = cast(dict[object, object], result)
    if not all(isinstance(key, str) for key in untyped_result):
        return _invalid_tool_result()
    details = cast(dict[str, object], untyped_result)
    failure_code_value = details.get("failure_code")
    if failure_code_value is None:
        return _ToolResultClassification(Outcome.SUCCEEDED, None, None, False)
    failure_code = _stable_code_or_none(failure_code_value)
    if failure_code is None:
        return _invalid_tool_result()
    return _ToolResultClassification(
        Outcome.FAILED,
        "command",
        failure_code,
        failure_code in _UNSUPPORTED_CODES,
    )


def _invalid_tool_result() -> _ToolResultClassification:
    return _ToolResultClassification(
        Outcome.FAILED,
        "instrumentation",
        "invalid_tool_result",
        False,
    )


def _stable_code_or_none(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    if value[0] not in "abcdefghijklmnopqrstuvwxyz":
        return None
    if any(character not in _STABLE_CODE_CHARACTERS for character in value):
        return None
    return value


def _require_nonnegative_usage(usage: Usage) -> None:
    for name, value in (
        ("requests", usage.requests),
        ("input_tokens", usage.input_tokens),
        ("output_tokens", usage.output_tokens),
        ("total_tokens", usage.total_tokens),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"model usage {name} must be a nonnegative integer")


def _completeness(call_count: int, observation_count: int) -> Completeness:
    if observation_count == 0:
        return Completeness.UNAVAILABLE
    if observation_count == call_count:
        return Completeness.COMPLETE
    return Completeness.PARTIAL


def _token_completeness(
    call_count: int,
    observation_counts: tuple[int, int, int],
) -> Completeness:
    if not any(observation_counts):
        return Completeness.UNAVAILABLE
    if all(count == call_count for count in observation_counts):
        return Completeness.COMPLETE
    return Completeness.PARTIAL
