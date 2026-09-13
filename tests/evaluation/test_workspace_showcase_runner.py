from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from agents import ModelResponse, Usage
from agents.agent_output import AgentOutputSchemaBase
from agents.exceptions import (
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRefusalError,
    UserError,
)
from agents.handoffs import Handoff
from agents.items import TResponseInputItem, TResponseOutputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.tool import Tool
from evaluations.workspace_showcase.__main__ import async_main, build_parser
from evaluations.workspace_showcase.fixtures import (
    FixtureDriftError,
    get_evaluation_fixture,
    list_evaluation_fixtures,
)
from evaluations.workspace_showcase.instrumentation import (
    PerformanceClock,
    ProviderCostSource,
)
from evaluations.workspace_showcase.reports import render_markdown
from evaluations.workspace_showcase.runner import (
    ApprovalRequiredError,
    LiveProviderRuntime,
    ProviderKind,
    ScenarioExecution,
    load_live_provider,
    run_workspace_showcase,
)
from evaluations.workspace_showcase.schema import (
    Completeness,
    FailureCategory,
    MetadataSnapshot,
    Outcome,
    TimingCategory,
    ToolInteraction,
    VerificationCheckKind,
    WorkspaceShowcaseRecord,
    read_record,
)
from openai import OpenAIError
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_prompt_param import ResponsePromptParam
from samples.openai_agents_sdk.providers.openai.config import OpenAISettings
from samples.openai_agents_sdk.runner import (
    LifecycleTimingCategory,
    LifecycleTimingObservation,
    RunnerOutcome,
    ScenarioResult,
    ScenarioVerificationError,
    StageFinishObservation,
    StageStartObservation,
    VerifiedArtifact,
)
from samples.openai_agents_sdk.scenarios import (
    ArtifactExpectation,
    StagedScenario,
    get_scenario,
)
from samples.shared.service import SampleServiceBundle

from mem_sandbox.core import InvalidRequestError, SystemClock
from mem_sandbox.service import InMemorySandboxService
from mem_sandbox.session import SessionPolicyEngine
from mem_sandbox.snapshots import InMemorySnapshotStore

_APPROVAL = "https://github.com/example/project/issues/76#issuecomment-123"
_CANARY_EXCEPTION = "CANARY-provider-exception-secret"
_CANARY_INPUT = "CANARY-model-input-secret"
_CANARY_OUTPUT = "CANARY-model-output-secret"


class IncrementingClock(PerformanceClock):
    def __init__(self) -> None:
        self.value = 0

    def now_ns(self) -> int:
        self.value += 10
        return self.value


class QueueModel(Model):
    def __init__(self, *results: ModelResponse | BaseException) -> None:
        self._results = iter(results)

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


class FakeClient:
    def __init__(self, close_error: BaseException | None = None) -> None:
        self.close_error = close_error
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FakeService:
    def __init__(self, close_error: BaseException | None = None) -> None:
        self.close_error = close_error
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FakeProviderLoader:
    def __init__(self, runtime: LiveProviderRuntime) -> None:
        self.runtime = runtime
        self.calls: list[ProviderKind] = []

    def __call__(self, provider: ProviderKind) -> LiveProviderRuntime:
        self.calls.append(provider)
        return self.runtime


class FakeServiceBundleFactory:
    def __init__(self, service: FakeService) -> None:
        self.service = service
        self.calls = 0

    def __call__(
        self,
        *,
        policy_engine: SessionPolicyEngine | None = None,
    ) -> SampleServiceBundle:
        _ = policy_engine
        self.calls += 1
        return SampleServiceBundle(
            service=cast(InMemorySandboxService, self.service),
            snapshot_store=cast(InMemorySnapshotStore, object()),
            clock=cast(SystemClock, object()),
        )


class PartialCostSource(ProviderCostSource):
    def __init__(self) -> None:
        self._costs = iter((None, Decimal("0.125")))

    def cost_for_response(self, response: ModelResponse) -> Decimal | None:
        _ = response
        return next(self._costs)


def _metadata_snapshot() -> MetadataSnapshot:
    return MetadataSnapshot(
        started_at_utc="2026-09-11T22:00:00+00:00",
        source_commit="d" * 40,
        source_dirty=False,
        mem_sandbox_version="0.2.0",
        mem_sandbox_openai_agents_version="0.2.0",
        openai_agents_version="0.22.0",
        openai_version="2.44.0",
        python_implementation="CPython",
        python_version="3.12.10",
        operating_system="Windows",
        architecture="AMD64",
    )


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


async def _call_model(model: Model) -> ModelResponse:
    return await model.get_response(
        None,
        _CANARY_INPUT,
        ModelSettings(),
        [],
        None,
        [],
        ModelTracing.DISABLED,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )


def _runtime(
    *,
    provider: ProviderKind = ProviderKind.OPENAI,
    model: Model | None = None,
    client: FakeClient | None = None,
) -> tuple[LiveProviderRuntime, FakeClient]:
    selected_client = client or FakeClient()
    return (
        LiveProviderRuntime(
            provider=provider.value,
            model_or_deployment=(
                "gpt-test" if provider is ProviderKind.OPENAI else "deployment-test"
            ),
            api_version=None if provider is ProviderKind.OPENAI else "2026-01-01",
            client=selected_client,
            model=model or QueueModel(),
        ),
        selected_client,
    )


def _verified_result(scenario_name: str) -> ScenarioResult:
    fixture = get_evaluation_fixture(scenario_name)
    return ScenarioResult(
        scenario_name=scenario_name,
        stage_outputs=tuple("ok" for _ in fixture.stage_keys),
        artifacts=tuple(
            VerifiedArtifact(path=artifact.path, content=artifact.expected_content)
            for artifact in fixture.artifacts
        ),
    )


async def _observe_success(request: ScenarioExecution) -> ScenarioResult:
    scenario = cast(StagedScenario, request.scenario)
    for stage in scenario.stages:
        request.observer.observe_stage_started(StageStartObservation(stage.key))
        request.stage_capability_factory(stage.key)
        request.observer.observe_lifecycle_timing(
            LifecycleTimingObservation(
                category=LifecycleTimingCategory.WORKSPACE_SEED,
                operation="client_create",
                stage_key=stage.key,
                outcome=RunnerOutcome.SUCCEEDED,
                duration_ns=17,
            )
        )
        await _call_model(request.stage_model_factory(stage.key))
        request.observer.observe_stage_finished(
            StageFinishObservation(stage.key, RunnerOutcome.SUCCEEDED)
        )
    request.observer.observe_lifecycle_timing(
        LifecycleTimingObservation(
            category=LifecycleTimingCategory.CLEANUP,
            operation="sdk_session_close",
            stage_key=None,
            outcome=RunnerOutcome.SUCCEEDED,
            duration_ns=19,
        )
    )
    request.observer.observe_lifecycle_timing(
        LifecycleTimingObservation(
            category=LifecycleTimingCategory.CLEANUP,
            operation="backend_delete",
            stage_key=None,
            outcome=RunnerOutcome.SUCCEEDED,
            duration_ns=11,
        )
    )
    return _verified_result(scenario.name)


@pytest.mark.parametrize(
    ("allow_billable_provider_run", "approval_reference"),
    [
        (False, _APPROVAL),
        (True, None),
        (True, ""),
        (True, "invalid\nreference"),
    ],
)
@pytest.mark.asyncio
async def test_approval_gate_blocks_loader_service_and_output_without_both_inputs(
    tmp_path: Path,
    allow_billable_provider_run: bool,
    approval_reference: str | None,
) -> None:
    runtime, _ = _runtime()
    loader = FakeProviderLoader(runtime)
    service_factory = FakeServiceBundleFactory(FakeService())
    output = tmp_path / "blocked"

    with pytest.raises(ApprovalRequiredError):
        await run_workspace_showcase(
            provider=ProviderKind.OPENAI,
            scenario="document-review",
            output=output,
            allow_billable_provider_run=allow_billable_provider_run,
            approval_reference=approval_reference,
            provider_loader=loader,
            service_bundle_factory=service_factory,
            scenario_executor=_observe_success,
            metadata_snapshot=_metadata_snapshot(),
        )

    assert loader.calls == []
    assert service_factory.calls == 0
    assert not output.exists()


def test_fixtures_pin_exact_stage_artifact_and_check_contracts() -> None:
    fixtures = list_evaluation_fixtures()

    assert tuple(fixture.scenario for fixture in fixtures) == (
        "document-review",
        "multi-agent-handoff",
    )
    assert all(fixture.version == 1 for fixture in fixtures)
    document = get_evaluation_fixture("document-review")
    assert document.stage_keys == (
        "document-review-editor",
        "document-review-reviewer",
    )
    assert tuple((artifact.path, artifact.check_kinds) for artifact in document.artifacts) == (
        (
            "/workspace/source/release-brief.txt",
            (VerificationCheckKind.UNRELATED_CONTENT_PRESERVATION,),
        ),
        (
            "/workspace/review/instructions.md",
            (VerificationCheckKind.UNRELATED_CONTENT_PRESERVATION,),
        ),
        (
            "/workspace/drafts/release-notes.md",
            (
                VerificationCheckKind.OUTPUT_CORRECTNESS,
                VerificationCheckKind.UNRELATED_CONTENT_PRESERVATION,
            ),
        ),
        (
            "/workspace/review/findings.md",
            (VerificationCheckKind.EVIDENCE_ACCURACY,),
        ),
    )
    handoff = get_evaluation_fixture("multi-agent-handoff")
    assert handoff.stage_keys == (
        "multi-agent-plan",
        "multi-agent-implement",
        "multi-agent-review",
    )
    assert tuple((artifact.path, artifact.check_kinds) for artifact in handoff.artifacts) == (
        ("/workspace/app.conf", (VerificationCheckKind.OUTPUT_CORRECTNESS,)),
        ("/workspace/handoff/plan.txt", (VerificationCheckKind.OUTPUT_CORRECTNESS,)),
        ("/workspace/handoff/review.txt", (VerificationCheckKind.EVIDENCE_ACCURACY,)),
    )


@pytest.mark.asyncio
async def test_fixture_drift_fails_before_provider_construction(tmp_path: Path) -> None:
    original = cast(StagedScenario, get_scenario("document-review"))
    drifted = replace(
        original,
        expected_artifacts=(
            ArtifactExpectation(
                original.expected_artifacts[0].path,
                b"drifted fixture content\n",
            ),
            *original.expected_artifacts[1:],
        ),
    )
    runtime, _ = _runtime()
    loader = FakeProviderLoader(runtime)
    service_factory = FakeServiceBundleFactory(FakeService())
    output = tmp_path / "drift"

    with pytest.raises(FixtureDriftError):
        await run_workspace_showcase(
            provider="openai",
            scenario="document-review",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=loader,
            service_bundle_factory=service_factory,
            scenario_resolver=lambda name: drifted,
            scenario_executor=_observe_success,
            metadata_snapshot=_metadata_snapshot(),
        )

    assert loader.calls == []
    assert service_factory.calls == 0
    assert not output.exists()


@pytest.mark.asyncio
async def test_provider_construction_failure_writes_failure_before_service_creation(
    tmp_path: Path,
) -> None:
    error = RuntimeError(_CANARY_EXCEPTION)
    loader_calls: list[ProviderKind] = []
    service_factory = FakeServiceBundleFactory(FakeService())

    def loader(provider: ProviderKind) -> LiveProviderRuntime:
        loader_calls.append(provider)
        raise error

    output = tmp_path / "provider-construction"
    with pytest.raises(RuntimeError) as raised:
        await run_workspace_showcase(
            provider="openai",
            scenario="document-review",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=loader,
            service_bundle_factory=service_factory,
            scenario_executor=_observe_success,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    record = read_record(output / "record.json")
    assert raised.value is error
    assert loader_calls == [ProviderKind.OPENAI]
    assert service_factory.calls == 0
    assert record.metadata.model_or_deployment == "unavailable"
    assert record.failure is not None
    assert record.failure.category is FailureCategory.PROVIDER_INTEGRATION
    assert record.failure.stable_code == "provider_construction_failed"
    assert all(stage.outcome is Outcome.INCOMPLETE for stage in record.stages)
    assert _CANARY_EXCEPTION not in record.to_json()


def test_live_provider_validates_descriptor_before_constructing_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = OpenAISettings(api_key=_CANARY_INPUT, model="invalid model")
    model_factory_called = False

    def forbidden_model_factory(
        selected: OpenAISettings,
    ) -> tuple[FakeClient, QueueModel]:
        nonlocal model_factory_called
        _ = selected
        model_factory_called = True
        raise AssertionError("model factory must not be called")

    def load_settings(cls: type[OpenAISettings]) -> OpenAISettings:
        _ = cls
        return settings

    monkeypatch.setattr(
        OpenAISettings,
        "from_environment",
        classmethod(load_settings),
    )
    monkeypatch.setattr(
        "evaluations.workspace_showcase.runner.create_openai_model",
        forbidden_model_factory,
    )

    with pytest.raises(ValueError, match="descriptor"):
        load_live_provider(ProviderKind.OPENAI)

    assert not model_factory_called


@pytest.mark.asyncio
async def test_service_construction_failure_closes_client_and_is_attributed(
    tmp_path: Path,
) -> None:
    error = RuntimeError(_CANARY_EXCEPTION)
    runtime, client = _runtime()

    def fail_service_construction(
        *,
        policy_engine: SessionPolicyEngine | None = None,
    ) -> SampleServiceBundle:
        _ = policy_engine
        raise error

    output = tmp_path / "service-construction"
    with pytest.raises(RuntimeError) as raised:
        await run_workspace_showcase(
            provider="openai",
            scenario="document-review",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=FakeProviderLoader(runtime),
            service_bundle_factory=fail_service_construction,
            scenario_executor=_observe_success,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    record = read_record(output / "record.json")
    assert raised.value is error
    assert client.close_calls == 1
    assert record.failure is not None
    assert record.failure.category is FailureCategory.MEM_SANDBOX
    assert record.failure.stable_code == "service_construction_failed"
    assert _CANARY_EXCEPTION not in record.to_json()


@pytest.mark.asyncio
async def test_success_writes_exact_record_report_usage_lifecycle_and_cleanup(
    tmp_path: Path,
) -> None:
    model = QueueModel(
        _response(_CANARY_OUTPUT, Usage()),
        _response(
            "ok",
            Usage(requests=1, input_tokens=13, output_tokens=8, total_tokens=21),
        ),
    )
    runtime, client = _runtime(model=model)
    loader = FakeProviderLoader(runtime)
    service = FakeService()
    service_factory = FakeServiceBundleFactory(service)
    clock = IncrementingClock()
    output = tmp_path / "success"

    async def executor(request: ScenarioExecution) -> ScenarioResult:
        assert request.performance_clock is clock
        return await _observe_success(request)

    record = await run_workspace_showcase(
        provider="openai",
        scenario="document-review",
        output=output,
        allow_billable_provider_run=True,
        approval_reference=_APPROVAL,
        provider_loader=loader,
        service_bundle_factory=service_factory,
        scenario_executor=executor,
        performance_clock=clock,
        cost_source=PartialCostSource(),
        metadata_snapshot=_metadata_snapshot(),
    )

    assert read_record(output / "record.json") == record
    report = (output / "REPORT.md").read_text(encoding="utf-8")
    assert record.outcome is Outcome.SUCCEEDED
    assert [stage.outcome for stage in record.stages] == [
        Outcome.SUCCEEDED,
        Outcome.SUCCEEDED,
    ]
    assert all(artifact.matched for artifact in record.artifacts)
    assert all(artifact.actual_sha256 == artifact.expected_sha256 for artifact in record.artifacts)
    assert record.usage.model_call_count == 2
    assert record.usage.token_completeness is Completeness.PARTIAL
    assert record.usage.cost_completeness is Completeness.PARTIAL
    assert record.usage.provider_cost == "0.125"
    assert {sample.category for sample in record.timings} >= {
        TimingCategory.WORKSPACE_SEED,
        TimingCategory.PROVIDER_MODEL,
        TimingCategory.CLEANUP,
        TimingCategory.END_TO_END,
    }
    assert [
        sample.operation for sample in record.timings if sample.category is TimingCategory.CLEANUP
    ] == [
        "sdk_session_close",
        "backend_delete",
        "service.close",
        "provider_client.close",
    ]
    assert record.timings[-1].category is TimingCategory.END_TO_END
    assert record.timings[-1].outcome is Outcome.SUCCEEDED
    assert service.close_calls == 1
    assert client.close_calls == 1
    assert "/workspace/drafts/release-notes.md" in report
    assert "output_correctness, unrelated_content_preservation" in report
    assert "## Tool interactions" in report
    serialized = record.to_json() + report
    assert _CANARY_INPUT not in serialized
    assert _CANARY_OUTPUT not in serialized

    report_with_tool = render_markdown(
        replace(
            record,
            tool_interactions=(
                ToolInteraction(
                    sequence=1,
                    stage="document-review-editor",
                    tool_name="execute",
                    duration_ns=23,
                    outcome=Outcome.FAILED,
                    error_category="unsupported",
                    error_code="command_not_found",
                    unsupported=True,
                ),
            ),
        )
    )
    assert (
        "| 1 | document-review-editor | execute | failed | command_not_found | yes | none | 23 |"
    ) in report_with_tool


async def _raise_during_second_stage(
    request: ScenarioExecution,
    error: BaseException,
) -> ScenarioResult:
    scenario = cast(StagedScenario, request.scenario)
    first, second, *_later = scenario.stages
    request.observer.observe_stage_started(StageStartObservation(first.key))
    request.observer.observe_stage_finished(
        StageFinishObservation(first.key, RunnerOutcome.SUCCEEDED)
    )
    request.observer.observe_stage_started(StageStartObservation(second.key))
    try:
        await _call_model(request.stage_model_factory(second.key))
    except asyncio.CancelledError:
        request.observer.observe_stage_finished(
            StageFinishObservation(second.key, RunnerOutcome.CANCELLED)
        )
        raise
    except Exception:
        request.observer.observe_stage_finished(
            StageFinishObservation(second.key, RunnerOutcome.FAILED)
        )
        raise
    raise AssertionError(f"expected model to raise {type(error).__name__}")


@pytest.mark.asyncio
async def test_provider_failure_writes_safe_partial_record_cleans_and_rethrows(
    tmp_path: Path,
) -> None:
    error = OpenAIError(f"{_CANARY_EXCEPTION} {_CANARY_INPUT} {_CANARY_OUTPUT}")
    runtime, client = _runtime(model=QueueModel(error))
    service = FakeService()
    output = tmp_path / "provider-failure"

    async def executor(request: ScenarioExecution) -> ScenarioResult:
        return await _raise_during_second_stage(request, error)

    with pytest.raises(OpenAIError) as raised:
        await run_workspace_showcase(
            provider="openai",
            scenario="multi-agent-handoff",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=FakeProviderLoader(runtime),
            service_bundle_factory=FakeServiceBundleFactory(service),
            scenario_executor=executor,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    record = read_record(output / "record.json")
    report = (output / "REPORT.md").read_text(encoding="utf-8")
    assert raised.value is error
    assert record.outcome is Outcome.FAILED
    assert [stage.outcome for stage in record.stages] == [
        Outcome.SUCCEEDED,
        Outcome.FAILED,
        Outcome.INCOMPLETE,
    ]
    assert record.failure is not None
    assert record.failure.category is FailureCategory.PROVIDER_INTEGRATION
    assert record.failure.exception_type == "OpenAIError"
    assert record.failure.stage == "multi-agent-implement"
    assert record.failure.stable_code == "provider_error"
    assert all(not artifact.matched for artifact in record.artifacts)
    assert all(artifact.error_code == "verification_incomplete" for artifact in record.artifacts)
    assert any(
        sample.category is TimingCategory.END_TO_END and sample.outcome is Outcome.FAILED
        for sample in record.timings
    )
    assert service.close_calls == 1
    assert client.close_calls == 1
    serialized = record.to_json() + report
    assert _CANARY_EXCEPTION not in serialized
    assert _CANARY_INPUT not in serialized
    assert _CANARY_OUTPUT not in serialized


@pytest.mark.asyncio
async def test_cancellation_writes_cancelled_record_cleanup_and_rethrows(
    tmp_path: Path,
) -> None:
    error = asyncio.CancelledError(_CANARY_EXCEPTION)
    runtime, client = _runtime(model=QueueModel(error))
    service = FakeService()
    output = tmp_path / "cancelled"

    async def executor(request: ScenarioExecution) -> ScenarioResult:
        return await _raise_during_second_stage(request, error)

    with pytest.raises(asyncio.CancelledError) as raised:
        await run_workspace_showcase(
            provider="openai",
            scenario="multi-agent-handoff",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=FakeProviderLoader(runtime),
            service_bundle_factory=FakeServiceBundleFactory(service),
            scenario_executor=executor,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    record = read_record(output / "record.json")
    assert raised.value is error
    assert record.outcome is Outcome.CANCELLED
    assert [stage.outcome for stage in record.stages] == [
        Outcome.SUCCEEDED,
        Outcome.CANCELLED,
        Outcome.INCOMPLETE,
    ]
    assert record.failure is not None
    assert record.failure.category is FailureCategory.CANCELLATION
    assert record.failure.stage == "multi-agent-implement"
    assert record.failure.stable_code == "cancelled"
    assert any(
        sample.category is TimingCategory.PROVIDER_MODEL and sample.outcome is Outcome.CANCELLED
        for sample in record.timings
    )
    assert any(
        sample.category is TimingCategory.END_TO_END and sample.outcome is Outcome.CANCELLED
        for sample in record.timings
    )
    assert service.close_calls == 1
    assert client.close_calls == 1
    assert _CANARY_EXCEPTION not in record.to_json()


@pytest.mark.asyncio
async def test_cleanup_only_failure_attributes_first_error_and_attempts_both(
    tmp_path: Path,
) -> None:
    service_error = RuntimeError(f"service {_CANARY_EXCEPTION}")
    client_error = RuntimeError(f"client {_CANARY_EXCEPTION}")
    client = FakeClient(client_error)
    runtime, _ = _runtime(
        model=QueueModel(
            _response("ok", Usage()),
            _response("ok", Usage()),
        ),
        client=client,
    )
    service = FakeService(service_error)
    output = tmp_path / "cleanup-failure"

    with pytest.raises(RuntimeError) as raised:
        await run_workspace_showcase(
            provider="openai",
            scenario="document-review",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=FakeProviderLoader(runtime),
            service_bundle_factory=FakeServiceBundleFactory(service),
            scenario_executor=_observe_success,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    record = read_record(output / "record.json")
    assert raised.value is service_error
    assert service.close_calls == 1
    assert client.close_calls == 1
    assert record.outcome is Outcome.FAILED
    assert all(stage.outcome is Outcome.SUCCEEDED for stage in record.stages)
    assert all(artifact.matched for artifact in record.artifacts)
    assert record.failure is not None
    assert record.failure.category is FailureCategory.CLEANUP
    assert record.failure.stage is None
    assert record.failure.stable_code == "service_close_failed"
    assert [
        sample.outcome for sample in record.timings if sample.category is TimingCategory.CLEANUP
    ] == [
        Outcome.SUCCEEDED,
        Outcome.SUCCEEDED,
        Outcome.FAILED,
        Outcome.FAILED,
    ]
    assert _CANARY_EXCEPTION not in (
        record.to_json() + (output / "REPORT.md").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("artifact_case", "stable_code"),
    [
        ("missing", "artifact_missing"),
        ("extra", "unexpected_artifact"),
        ("mismatched", "artifact_mismatch"),
    ],
)
@pytest.mark.asyncio
async def test_host_verification_detects_missing_extra_and_mismatched_artifacts(
    tmp_path: Path,
    artifact_case: str,
    stable_code: str,
) -> None:
    runtime, _ = _runtime()
    fixture = get_evaluation_fixture("document-review")
    exact = list(_verified_result(fixture.scenario).artifacts)
    if artifact_case == "missing":
        artifacts = tuple(exact[1:])
    elif artifact_case == "extra":
        artifacts = (*exact, VerifiedArtifact("/workspace/extra.txt", _CANARY_OUTPUT.encode()))
    else:
        artifacts = (
            VerifiedArtifact(exact[0].path, _CANARY_OUTPUT.encode()),
            *exact[1:],
        )

    async def executor(request: ScenarioExecution) -> ScenarioResult:
        scenario = cast(StagedScenario, request.scenario)
        for stage in scenario.stages:
            request.observer.observe_stage_started(StageStartObservation(stage.key))
            request.observer.observe_stage_finished(
                StageFinishObservation(stage.key, RunnerOutcome.SUCCEEDED)
            )
        return replace(_verified_result(scenario.name), artifacts=artifacts)

    output = tmp_path / artifact_case
    with pytest.raises(ScenarioVerificationError):
        await run_workspace_showcase(
            provider="openai",
            scenario=fixture.scenario,
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=FakeProviderLoader(runtime),
            service_bundle_factory=FakeServiceBundleFactory(FakeService()),
            scenario_executor=executor,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    record = read_record(output / "record.json")
    assert record.failure is not None
    assert record.failure.category is FailureCategory.HOST_VERIFICATION
    assert record.failure.stable_code == stable_code
    if artifact_case == "missing":
        assert record.artifacts[0].error_code == "artifact_missing"
        assert record.artifacts[0].actual_sha256 is None
    elif artifact_case == "mismatched":
        assert record.artifacts[0].error_code == "artifact_mismatch"
        assert record.artifacts[0].actual_sha256 is not None
    else:
        assert all(artifact.matched for artifact in record.artifacts)
        assert "/workspace/extra.txt" not in record.to_json()
    assert _CANARY_OUTPUT not in (
        record.to_json() + (output / "REPORT.md").read_text(encoding="utf-8")
    )
    assert [
        sample.outcome
        for sample in record.timings
        if sample.operation == "evaluation_artifact_hashes"
    ] == [Outcome.FAILED]


@pytest.mark.asyncio
async def test_persistence_failure_adds_note_and_preserves_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = OpenAIError(_CANARY_EXCEPTION)
    runtime, client = _runtime(model=QueueModel(error))
    service = FakeService()
    persistence_error = OSError("record storage unavailable")

    def fail_record_write(path: Path, record: WorkspaceShowcaseRecord) -> None:
        _ = (path, record)
        raise persistence_error

    monkeypatch.setattr(
        "evaluations.workspace_showcase.runner.write_record",
        fail_record_write,
    )

    async def executor(request: ScenarioExecution) -> ScenarioResult:
        return await _raise_during_second_stage(request, error)

    output = tmp_path / "persistence-failure"
    with pytest.raises(OpenAIError) as raised:
        await run_workspace_showcase(
            provider="openai",
            scenario="multi-agent-handoff",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=FakeProviderLoader(runtime),
            service_bundle_factory=FakeServiceBundleFactory(service),
            scenario_executor=executor,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    assert raised.value is error
    assert error.__notes__ == ["secondary evaluation persistence failure: OSError"]
    assert not (output / "record.json").exists()
    assert (output / "REPORT.md").exists()
    assert service.close_calls == 1
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_failure_record_construction_does_not_mask_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = OpenAIError(_CANARY_EXCEPTION)
    secondary = ValueError("invalid failure record")
    runtime, client = _runtime(model=QueueModel(primary))
    service = FakeService()

    def fail_failure_record(**kwargs: object) -> WorkspaceShowcaseRecord:
        _ = kwargs
        raise secondary

    monkeypatch.setattr(
        "evaluations.workspace_showcase.runner._failure_record",
        fail_failure_record,
    )

    async def executor(request: ScenarioExecution) -> ScenarioResult:
        return await _raise_during_second_stage(request, primary)

    output = tmp_path / "failure-record-construction"
    with pytest.raises(OpenAIError) as raised:
        await run_workspace_showcase(
            provider="openai",
            scenario="multi-agent-handoff",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=FakeProviderLoader(runtime),
            service_bundle_factory=FakeServiceBundleFactory(service),
            scenario_executor=executor,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    assert raised.value is primary
    assert primary.__notes__ == [
        "secondary evaluation failure-record construction failure: ValueError"
    ]
    assert service.close_calls == 1
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("error", "category", "stable_code"),
    [
        (
            ModelBehaviorError(_CANARY_EXCEPTION),
            FailureCategory.MODEL_BEHAVIOR,
            "model_behavior_error",
        ),
        (
            ModelRefusalError(_CANARY_EXCEPTION),
            FailureCategory.MODEL_BEHAVIOR,
            "model_refusal",
        ),
        (
            MaxTurnsExceeded(_CANARY_EXCEPTION),
            FailureCategory.MODEL_BEHAVIOR,
            "max_turns_exceeded",
        ),
        (
            UserError(_CANARY_EXCEPTION),
            FailureCategory.SDK_BEHAVIOR,
            "sdk_user_error",
        ),
        (
            ScenarioVerificationError(_CANARY_EXCEPTION),
            FailureCategory.HOST_VERIFICATION,
            "scenario_verification_failed",
        ),
        (
            InvalidRequestError(_CANARY_EXCEPTION),
            FailureCategory.MEM_SANDBOX,
            "invalid_request",
        ),
        (
            RuntimeError(_CANARY_EXCEPTION),
            FailureCategory.UNKNOWN,
            "unknown_failure",
        ),
    ],
)
@pytest.mark.asyncio
async def test_failure_classification_is_stable_and_message_free(
    tmp_path: Path,
    error: Exception,
    category: FailureCategory,
    stable_code: str,
) -> None:
    runtime, _ = _runtime(model=QueueModel(error))
    output = tmp_path / type(error).__name__

    async def executor(request: ScenarioExecution) -> ScenarioResult:
        return await _raise_during_second_stage(request, error)

    with pytest.raises(type(error)):
        await run_workspace_showcase(
            provider="openai",
            scenario="multi-agent-handoff",
            output=output,
            allow_billable_provider_run=True,
            approval_reference=_APPROVAL,
            provider_loader=FakeProviderLoader(runtime),
            service_bundle_factory=FakeServiceBundleFactory(FakeService()),
            scenario_executor=executor,
            performance_clock=IncrementingClock(),
            metadata_snapshot=_metadata_snapshot(),
        )

    record = read_record(output / "record.json")
    assert record.failure is not None
    assert record.failure.category is category
    assert record.failure.stable_code == stable_code
    assert _CANARY_EXCEPTION not in record.to_json()


@pytest.mark.asyncio
async def test_cli_selects_azure_through_injected_loader_without_environment_access(
    tmp_path: Path,
) -> None:
    runtime, client = _runtime(
        provider=ProviderKind.AZURE_OPENAI,
        model=QueueModel(
            _response("ok", Usage()),
            _response("ok", Usage()),
            _response("ok", Usage()),
        ),
    )
    loader = FakeProviderLoader(runtime)
    service = FakeService()
    output = tmp_path / "cli"

    record = await async_main(
        [
            "--provider",
            "azure_openai",
            "--scenario",
            "multi-agent-handoff",
            "--output",
            str(output),
            "--allow-billable-provider-run",
            "--approval-reference",
            _APPROVAL,
        ],
        provider_loader=loader,
        service_bundle_factory=FakeServiceBundleFactory(service),
        scenario_executor=_observe_success,
        performance_clock=IncrementingClock(),
        metadata_snapshot=_metadata_snapshot(),
    )

    assert loader.calls == [ProviderKind.AZURE_OPENAI]
    assert record.metadata.provider == "azure_openai"
    assert record.metadata.model_or_deployment == "deployment-test"
    assert record.metadata.provider_api_version == "2026-01-01"
    assert client.close_calls == 1
    assert service.close_calls == 1
    help_text = build_parser().format_help()
    assert "billable" in help_text
    assert "non-gating" in help_text
