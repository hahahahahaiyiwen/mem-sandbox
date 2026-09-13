"""Approval-gated orchestration for workspace-showcase live evaluations."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from agents.exceptions import (
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRefusalError,
    UserError,
)
from agents.models.interface import Model
from openai import OpenAIError
from samples.openai_agents_sdk.providers.azure_openai import (
    AzureOpenAISettings,
    create_azure_model,
)
from samples.openai_agents_sdk.providers.openai import (
    OpenAISettings,
    create_openai_model,
)
from samples.openai_agents_sdk.runner import (
    LifecycleTimingObservation,
    ScenarioResult,
    ScenarioRunObserver,
    ScenarioVerificationError,
    StageFinishObservation,
    StageStartObservation,
    run_scenario,
)
from samples.openai_agents_sdk.scenarios import (
    ScenarioDefinition,
    get_scenario,
)
from samples.shared.service import SampleServiceBundle, create_sample_service_bundle

from evaluations.workspace_showcase.fixtures import (
    WorkspaceShowcaseFixture,
    get_evaluation_fixture,
    validate_fixture_against_scenario,
)
from evaluations.workspace_showcase.instrumentation import (
    PerformanceClock,
    ProviderCostSource,
    SystemPerformanceClock,
    WorkspaceShowcaseRecorder,
)
from evaluations.workspace_showcase.reports import render_markdown
from evaluations.workspace_showcase.schema import (
    SCHEMA_VERSION,
    ArtifactVerification,
    FailureAttribution,
    FailureCategory,
    MetadataSnapshot,
    Outcome,
    StageOutcome,
    TimingCategory,
    WorkspaceShowcaseRecord,
    build_run_metadata,
    capture_metadata_snapshot,
    write_record,
)
from mem_sandbox.core import Clock, OperationCancelledError, SandboxError
from mem_sandbox.service import FactorySnapshotStore, SandboxService
from mem_sandbox.session import SessionPolicyEngine
from mem_sandbox_openai_agents import InMemorySandboxCapability

_APPROVAL_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~:/?#@!$&'()*+,;=%-]{0,511}$")
_DESCRIPTOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,127}$")


class ApprovalRequiredError(ValueError):
    """A live evaluation was requested without both required approval inputs."""


class ProviderKind(StrEnum):
    """Supported provider compositions for live evaluation."""

    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"


class AsyncModelClient(Protocol):
    """Minimum provider-client lifetime owned by one evaluation run."""

    async def close(self) -> None:
        """Release provider client resources."""
        ...


@dataclass(frozen=True, slots=True)
class LiveProviderRuntime:
    """Secret-free provider descriptor plus the constructed live runtime."""

    provider: str
    model_or_deployment: str
    api_version: str | None
    client: AsyncModelClient
    model: Model

    def __post_init__(self) -> None:
        ProviderKind(self.provider)
        _require_descriptor("model_or_deployment", self.model_or_deployment)
        if self.api_version is not None:
            _require_descriptor("api_version", self.api_version)


class ProviderLoader(Protocol):
    """Construct one explicitly selected provider runtime."""

    def __call__(self, provider: ProviderKind) -> LiveProviderRuntime:
        """Load settings and construct the requested provider runtime."""
        ...


class ScenarioResolver(Protocol):
    """Resolve one registered scenario without constructing provider resources."""

    def __call__(self, name: str) -> ScenarioDefinition:
        """Return the registered scenario with the requested name."""
        ...


class ServiceBundleFactory(Protocol):
    """Construct the MemSandbox services for one scenario."""

    def __call__(
        self,
        *,
        policy_engine: SessionPolicyEngine | None = None,
    ) -> SampleServiceBundle:
        """Create one independently owned service bundle."""
        ...


@dataclass(frozen=True, slots=True)
class ScenarioExecution:
    """Injected scenario-execution request used by the network-free tests."""

    model: Model
    service: SandboxService
    scenario: ScenarioDefinition
    snapshot_store: FactorySnapshotStore | None
    clock: Clock | None
    stage_model_factory: Callable[[str], Model]
    stage_capability_factory: Callable[[str], InMemorySandboxCapability]
    performance_clock: PerformanceClock
    observer: ScenarioRunObserver


class ScenarioExecutor(Protocol):
    """Execute one fully composed scenario request."""

    async def __call__(self, request: ScenarioExecution) -> ScenarioResult:
        """Run the scenario and return its host-verified result."""
        ...


@dataclass(slots=True)
class _RunState:
    runtime: LiveProviderRuntime | None = None
    bundle: SampleServiceBundle | None = None
    result: ScenarioResult | None = None
    artifacts: tuple[ArtifactVerification, ...] | None = None
    provider_failure_code: str | None = None
    service_failure_code: str | None = None
    verification_failure_code: str | None = None
    cleanup_failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class _FailureDetails:
    category: FailureCategory
    stable_code: str


class _RecorderObserver(ScenarioRunObserver):
    def __init__(
        self,
        *,
        stage_keys: tuple[str, ...],
        recorder: WorkspaceShowcaseRecorder,
    ) -> None:
        self._stage_keys = stage_keys
        self._stage_set = frozenset(stage_keys)
        self._recorder = recorder
        self._next_start = 0
        self._started: set[str] = set()
        self._finished: dict[str, Outcome] = {}
        self._active_stage: str | None = None

    def observe_lifecycle_timing(self, observation: LifecycleTimingObservation) -> None:
        stage = observation.stage_key
        if stage is not None and stage not in self._stage_set:
            raise ValueError("runner lifecycle timing referenced an unknown stage")
        self._recorder.observe_lifecycle_timing(
            category=TimingCategory(observation.category.value),
            operation=observation.operation,
            stage=stage,
            outcome=Outcome(observation.outcome.value),
            duration_ns=observation.duration_ns,
        )

    def observe_stage_started(self, observation: StageStartObservation) -> None:
        if self._active_stage is not None:
            raise ValueError("runner started a stage before the prior stage finished")
        if self._next_start >= len(self._stage_keys):
            raise ValueError("runner started more stages than the evaluation fixture defines")
        expected = self._stage_keys[self._next_start]
        if observation.stage_key != expected:
            raise ValueError("runner stage start order drifted from the evaluation fixture")
        self._started.add(observation.stage_key)
        self._active_stage = observation.stage_key
        self._next_start += 1

    def observe_stage_finished(self, observation: StageFinishObservation) -> None:
        if observation.stage_key != self._active_stage:
            raise ValueError("runner finished a stage that was not started")
        if observation.stage_key in self._finished:
            raise ValueError("runner emitted more than one terminal outcome for a stage")
        self._finished[observation.stage_key] = Outcome(observation.outcome.value)
        self._active_stage = None

    def failure_stage(self) -> str | None:
        for stage in self._stage_keys:
            outcome = self._finished.get(stage)
            if outcome in {Outcome.FAILED, Outcome.CANCELLED}:
                return stage
        for stage in self._stage_keys:
            if stage in self._started and stage not in self._finished:
                return stage
        return None

    def stage_outcomes(
        self,
        *,
        terminal_outcome: Outcome,
        failure_stage: str | None,
        stable_code: str | None,
    ) -> tuple[StageOutcome, ...]:
        outcomes: list[StageOutcome] = []
        for sequence, stage in enumerate(self._stage_keys, start=1):
            outcome = self._finished.get(stage)
            if outcome is None:
                if stage in self._started and terminal_outcome in {
                    Outcome.FAILED,
                    Outcome.CANCELLED,
                }:
                    outcome = terminal_outcome
                else:
                    outcome = Outcome.INCOMPLETE
            error_code = (
                stable_code
                if stage == failure_stage and outcome in {Outcome.FAILED, Outcome.CANCELLED}
                else None
            )
            outcomes.append(
                StageOutcome(
                    sequence=sequence,
                    stage=stage,
                    outcome=outcome,
                    error_code=error_code,
                )
            )
        return tuple(outcomes)


def load_live_provider(provider: ProviderKind) -> LiveProviderRuntime:
    """Load environment settings and construct one maintained provider runtime."""
    if provider is ProviderKind.OPENAI:
        settings = OpenAISettings.from_environment()
        _require_descriptor("model_or_deployment", settings.model)
        client, model = create_openai_model(settings)
        return LiveProviderRuntime(
            provider=provider.value,
            model_or_deployment=settings.model,
            api_version=None,
            client=client,
            model=model,
        )

    settings = AzureOpenAISettings.from_environment()
    _require_descriptor("model_or_deployment", settings.deployment)
    _require_descriptor("api_version", settings.api_version)
    client, model = create_azure_model(settings)
    return LiveProviderRuntime(
        provider=provider.value,
        model_or_deployment=settings.deployment,
        api_version=settings.api_version,
        client=client,
        model=model,
    )


async def execute_scenario(request: ScenarioExecution) -> ScenarioResult:
    """Adapt the evaluation-owned execution request to the maintained runner."""
    return await run_scenario(
        model=request.model,
        service=request.service,
        scenario=request.scenario,
        snapshot_store=request.snapshot_store,
        clock=request.clock,
        stage_model_factory=request.stage_model_factory,
        stage_capability_factory=request.stage_capability_factory,
        performance_clock=request.performance_clock,
        observer=request.observer,
    )


async def run_workspace_showcase(
    *,
    provider: ProviderKind | str,
    scenario: str,
    output: Path,
    allow_billable_provider_run: bool,
    approval_reference: str | None,
    provider_loader: ProviderLoader = load_live_provider,
    scenario_resolver: ScenarioResolver = get_scenario,
    service_bundle_factory: ServiceBundleFactory = create_sample_service_bundle,
    scenario_executor: ScenarioExecutor = execute_scenario,
    performance_clock: PerformanceClock | None = None,
    cost_source: ProviderCostSource | None = None,
    metadata_snapshot: MetadataSnapshot | None = None,
) -> WorkspaceShowcaseRecord:
    """Run one approved live evaluation, persist its record, and preserve failures."""
    normalized_approval = _require_approval(
        allow_billable_provider_run=allow_billable_provider_run,
        approval_reference=approval_reference,
    )
    provider_kind = _coerce_provider(provider)
    fixture = get_evaluation_fixture(scenario)
    registered_scenario = validate_fixture_against_scenario(
        fixture,
        scenario_resolver(scenario),
    )
    snapshot = metadata_snapshot or capture_metadata_snapshot()
    output.mkdir(parents=True, exist_ok=False)

    resolved_clock = performance_clock or SystemPerformanceClock()
    recorder = WorkspaceShowcaseRecorder(resolved_clock, cost_source=cost_source)
    observer = _RecorderObserver(stage_keys=fixture.stage_keys, recorder=recorder)
    state = _RunState()

    async def execute_lifecycle() -> ScenarioResult:
        try:
            try:
                state.runtime = provider_loader(provider_kind)
            except BaseException:
                state.provider_failure_code = "provider_construction_failed"
                raise
            try:
                _validate_runtime_selection(state.runtime, provider_kind)
            except BaseException:
                state.provider_failure_code = "provider_runtime_invalid"
                raise
            try:
                state.bundle = service_bundle_factory(
                    policy_engine=registered_scenario.policy_engine_factory()
                )
            except BaseException:
                state.service_failure_code = "service_construction_failed"
                raise
            result = await scenario_executor(
                ScenarioExecution(
                    model=state.runtime.model,
                    service=state.bundle.service,
                    scenario=registered_scenario,
                    snapshot_store=state.bundle.snapshot_store,
                    clock=state.bundle.clock,
                    stage_model_factory=recorder.stage_model_factory(state.runtime.model),
                    stage_capability_factory=recorder.stage_capability_factory(),
                    performance_clock=resolved_clock,
                    observer=observer,
                )
            )
            state.result = result

            async def verify_result() -> None:
                state.artifacts, state.verification_failure_code = await _verify_result_artifacts(
                    fixture, result
                )
                if state.verification_failure_code is not None:
                    raise ScenarioVerificationError("evaluation artifact verification failed")

            await recorder.time_async(
                TimingCategory.HOST_VERIFICATION,
                "evaluation_artifact_hashes",
                verify_result,
            )
            return result
        finally:
            await _close_resources(
                state=state,
                recorder=recorder,
                primary=sys.exception(),
            )

    try:
        await recorder.time_async(
            TimingCategory.END_TO_END,
            "workspace_showcase",
            execute_lifecycle,
        )
    except BaseException as error:
        _persist_failure_record_preserving(
            output=output,
            provider=provider_kind,
            approval_reference=normalized_approval,
            fixture=fixture,
            snapshot=snapshot,
            state=state,
            observer=observer,
            recorder=recorder,
            primary=error,
        )
        raise

    try:
        record = _success_record(
            approval_reference=normalized_approval,
            fixture=fixture,
            snapshot=snapshot,
            state=state,
            observer=observer,
            recorder=recorder,
        )
    except BaseException as error:
        _persist_failure_record_preserving(
            output=output,
            provider=provider_kind,
            approval_reference=normalized_approval,
            fixture=fixture,
            snapshot=snapshot,
            state=state,
            observer=observer,
            recorder=recorder,
            primary=error,
        )
        raise
    _persist_record(output, record)
    return record


async def _verify_result_artifacts(
    fixture: WorkspaceShowcaseFixture,
    result: ScenarioResult,
) -> tuple[tuple[ArtifactVerification, ...], str | None]:
    if result.scenario_name != fixture.scenario:
        scenario_failure = "scenario_result_mismatch"
    else:
        scenario_failure = None

    actual_by_path: dict[str, bytes] = {}
    duplicate_found = False
    for artifact in result.artifacts:
        if artifact.path in actual_by_path:
            duplicate_found = True
        actual_by_path[artifact.path] = artifact.content

    expected_paths = {artifact.path for artifact in fixture.artifacts}
    unexpected_found = any(path not in expected_paths for path in actual_by_path)
    missing_found = False
    mismatch_found = False
    records: list[ArtifactVerification] = []
    for expected in fixture.artifacts:
        expected_hash = sha256(expected.expected_content).hexdigest()
        actual_content = actual_by_path.get(expected.path)
        if actual_content is None:
            missing_found = True
            records.append(
                ArtifactVerification(
                    path=expected.path,
                    check_kinds=expected.check_kinds,
                    expected_sha256=expected_hash,
                    actual_sha256=None,
                    matched=False,
                    error_code="artifact_missing",
                )
            )
            continue
        actual_hash = sha256(actual_content).hexdigest()
        matched = actual_hash == expected_hash
        if not matched:
            mismatch_found = True
        records.append(
            ArtifactVerification(
                path=expected.path,
                check_kinds=expected.check_kinds,
                expected_sha256=expected_hash,
                actual_sha256=actual_hash,
                matched=matched,
                error_code=None if matched else "artifact_mismatch",
            )
        )

    failure_code = scenario_failure
    if duplicate_found:
        failure_code = "duplicate_artifact"
    elif unexpected_found:
        failure_code = "unexpected_artifact"
    elif missing_found:
        failure_code = "artifact_missing"
    elif mismatch_found:
        failure_code = "artifact_mismatch"
    return tuple(records), failure_code


async def _close_resources(
    *,
    state: _RunState,
    recorder: WorkspaceShowcaseRecorder,
    primary: BaseException | None,
) -> None:
    cleanup_errors: list[tuple[str, str, BaseException]] = []
    if state.bundle is not None:
        try:
            await recorder.time_async(
                TimingCategory.CLEANUP,
                "service.close",
                state.bundle.service.close,
            )
        except BaseException as error:
            cleanup_errors.append(("MemSandbox service close", "service_close_failed", error))
    if state.runtime is not None:
        try:
            await recorder.time_async(
                TimingCategory.CLEANUP,
                "provider_client.close",
                state.runtime.client.close,
            )
        except BaseException as error:
            cleanup_errors.append(("provider client close", "provider_client_close_failed", error))
    if cleanup_errors and primary is None:
        state.cleanup_failure_code = cleanup_errors[0][1]
    _surface_cleanup_errors(primary, cleanup_errors)


def _surface_cleanup_errors(
    primary: BaseException | None,
    cleanup_errors: list[tuple[str, str, BaseException]],
) -> None:
    if not cleanup_errors:
        return
    if primary is not None:
        for operation, _code, error in cleanup_errors:
            primary.add_note(f"secondary {operation} failure: {error}")
        return

    operation, _code, failure = cleanup_errors[0]
    failure.add_note(f"{operation} failed during workspace-showcase cleanup")
    for secondary_operation, _secondary_code, secondary in cleanup_errors[1:]:
        failure.add_note(f"secondary {secondary_operation} failure: {secondary}")
    raise failure


def _success_record(
    *,
    approval_reference: str,
    fixture: WorkspaceShowcaseFixture,
    snapshot: MetadataSnapshot,
    state: _RunState,
    observer: _RecorderObserver,
    recorder: WorkspaceShowcaseRecorder,
) -> WorkspaceShowcaseRecord:
    runtime = _require_runtime(state)
    artifacts = state.artifacts
    if artifacts is None:
        raise RuntimeError("successful evaluation did not produce artifact verification")
    return WorkspaceShowcaseRecord(
        schema_version=SCHEMA_VERSION,
        metadata=build_run_metadata(
            provider=runtime.provider,
            model_or_deployment=runtime.model_or_deployment,
            provider_api_version=runtime.api_version,
            approval_reference=approval_reference,
            scenario=fixture.scenario,
            fixture_version=fixture.version,
            snapshot=snapshot,
        ),
        outcome=Outcome.SUCCEEDED,
        stages=observer.stage_outcomes(
            terminal_outcome=Outcome.SUCCEEDED,
            failure_stage=None,
            stable_code=None,
        ),
        artifacts=artifacts,
        tool_interactions=recorder.tool_interactions,
        timings=recorder.timings,
        usage=recorder.usage_summary(),
        failure=None,
    )


def _failure_record(
    *,
    provider: ProviderKind,
    approval_reference: str,
    fixture: WorkspaceShowcaseFixture,
    snapshot: MetadataSnapshot,
    state: _RunState,
    observer: _RecorderObserver,
    recorder: WorkspaceShowcaseRecorder,
    error: BaseException,
) -> WorkspaceShowcaseRecord:
    runtime = state.runtime
    outcome = _terminal_outcome(error)
    failure_stage = observer.failure_stage()
    details = _classify_failure(error, state)
    artifacts = state.artifacts or _incomplete_artifacts(fixture)
    return WorkspaceShowcaseRecord(
        schema_version=SCHEMA_VERSION,
        metadata=build_run_metadata(
            provider=runtime.provider if runtime is not None else provider.value,
            model_or_deployment=(
                runtime.model_or_deployment if runtime is not None else "unavailable"
            ),
            provider_api_version=runtime.api_version if runtime is not None else None,
            approval_reference=approval_reference,
            scenario=fixture.scenario,
            fixture_version=fixture.version,
            snapshot=snapshot,
        ),
        outcome=outcome,
        stages=observer.stage_outcomes(
            terminal_outcome=outcome,
            failure_stage=failure_stage,
            stable_code=details.stable_code,
        ),
        artifacts=artifacts,
        tool_interactions=recorder.tool_interactions,
        timings=recorder.timings,
        usage=recorder.usage_summary(),
        failure=FailureAttribution.from_exception(
            details.category,
            error,
            stage=failure_stage,
            stable_code=details.stable_code,
        ),
    )


def _incomplete_artifacts(
    fixture: WorkspaceShowcaseFixture,
) -> tuple[ArtifactVerification, ...]:
    return tuple(
        ArtifactVerification(
            path=artifact.path,
            check_kinds=artifact.check_kinds,
            expected_sha256=sha256(artifact.expected_content).hexdigest(),
            actual_sha256=None,
            matched=False,
            error_code="verification_incomplete",
        )
        for artifact in fixture.artifacts
    )


def _classify_failure(error: BaseException, state: _RunState) -> _FailureDetails:
    if isinstance(error, (asyncio.CancelledError, OperationCancelledError)):
        return _FailureDetails(FailureCategory.CANCELLATION, "cancelled")
    if state.cleanup_failure_code is not None:
        return _FailureDetails(FailureCategory.CLEANUP, state.cleanup_failure_code)
    if state.provider_failure_code is not None:
        return _FailureDetails(
            FailureCategory.PROVIDER_INTEGRATION,
            state.provider_failure_code,
        )
    if state.service_failure_code is not None:
        return _FailureDetails(
            FailureCategory.MEM_SANDBOX,
            state.service_failure_code,
        )
    if isinstance(error, ModelRefusalError):
        return _FailureDetails(FailureCategory.MODEL_BEHAVIOR, "model_refusal")
    if isinstance(error, MaxTurnsExceeded):
        return _FailureDetails(FailureCategory.MODEL_BEHAVIOR, "max_turns_exceeded")
    if isinstance(error, ModelBehaviorError):
        return _FailureDetails(FailureCategory.MODEL_BEHAVIOR, "model_behavior_error")
    if isinstance(error, UserError):
        return _FailureDetails(FailureCategory.SDK_BEHAVIOR, "sdk_user_error")
    if isinstance(error, ScenarioVerificationError):
        return _FailureDetails(
            FailureCategory.HOST_VERIFICATION,
            state.verification_failure_code or "scenario_verification_failed",
        )
    if isinstance(error, SandboxError):
        return _FailureDetails(FailureCategory.MEM_SANDBOX, error.code)
    if isinstance(error, OpenAIError):
        return _FailureDetails(FailureCategory.PROVIDER_INTEGRATION, "provider_error")
    return _FailureDetails(FailureCategory.UNKNOWN, "unknown_failure")


def _terminal_outcome(error: BaseException) -> Outcome:
    if isinstance(error, (asyncio.CancelledError, OperationCancelledError)):
        return Outcome.CANCELLED
    return Outcome.FAILED


def _persist_preserving(
    output: Path,
    record: WorkspaceShowcaseRecord,
    primary: BaseException,
) -> None:
    try:
        _persist_record(output, record)
    except BaseException as persistence_error:
        primary.add_note(
            f"secondary evaluation persistence failure: {type(persistence_error).__name__}"
        )


def _persist_failure_record_preserving(
    *,
    output: Path,
    provider: ProviderKind,
    approval_reference: str,
    fixture: WorkspaceShowcaseFixture,
    snapshot: MetadataSnapshot,
    state: _RunState,
    observer: _RecorderObserver,
    recorder: WorkspaceShowcaseRecorder,
    primary: BaseException,
) -> None:
    try:
        record = _failure_record(
            provider=provider,
            approval_reference=approval_reference,
            fixture=fixture,
            snapshot=snapshot,
            state=state,
            observer=observer,
            recorder=recorder,
            error=primary,
        )
    except BaseException as record_error:
        primary.add_note(
            "secondary evaluation failure-record construction failure: "
            f"{type(record_error).__name__}"
        )
        return
    _persist_preserving(output, record, primary)


def _persist_record(output: Path, record: WorkspaceShowcaseRecord) -> None:
    persistence_errors: list[tuple[str, BaseException]] = []
    try:
        write_record(output / "record.json", record)
    except BaseException as error:
        persistence_errors.append(("record write", error))
    try:
        (output / "REPORT.md").write_text(
            render_markdown(record),
            encoding="utf-8",
            newline="\n",
        )
    except BaseException as error:
        persistence_errors.append(("report write", error))
    if not persistence_errors:
        return
    operation, failure = persistence_errors[0]
    failure.add_note(f"{operation} failed during workspace-showcase persistence")
    for secondary_operation, secondary in persistence_errors[1:]:
        failure.add_note(f"secondary {secondary_operation} failure: {secondary}")
    raise failure


def _require_approval(
    *,
    allow_billable_provider_run: bool,
    approval_reference: str | None,
) -> str:
    if allow_billable_provider_run is not True:
        raise ApprovalRequiredError("billable provider run approval flag is required")
    if approval_reference is None:
        raise ApprovalRequiredError("a valid approval reference is required")
    normalized = approval_reference.strip()
    if _APPROVAL_REFERENCE_PATTERN.fullmatch(normalized) is None:
        raise ApprovalRequiredError("a valid approval reference is required")
    return normalized


def _coerce_provider(provider: ProviderKind | str) -> ProviderKind:
    try:
        return ProviderKind(provider)
    except ValueError as error:
        raise ValueError("unsupported workspace-showcase provider") from error


def _validate_runtime_selection(
    runtime: LiveProviderRuntime,
    requested: ProviderKind,
) -> None:
    if runtime.provider != requested.value:
        raise ValueError("provider runtime descriptor did not match the requested provider")


def _require_runtime(state: _RunState) -> LiveProviderRuntime:
    if state.runtime is None:
        raise RuntimeError("evaluation completed without a provider runtime")
    return state.runtime


def _require_descriptor(name: str, value: str) -> None:
    if _DESCRIPTOR_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} contains characters outside the descriptor allowlist")
