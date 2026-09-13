"""Provider-neutral OpenAI Agents SDK scenario runner over MemSandbox."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from agents import RunConfig, Runner
from agents.models.interface import Model
from agents.sandbox import SandboxAgent, SandboxRunConfig
from agents.sandbox.session import SandboxSession as OpenAISandboxSession

from mem_sandbox.core import Clock
from mem_sandbox.service import FactorySnapshotStore, SandboxHandle, SandboxService
from mem_sandbox.session import ReadBytesRequest, SandboxSession, SessionOperationCancelled
from mem_sandbox_openai_agents import (
    InMemorySandboxCapability,
    InMemorySandboxClient,
    InMemorySandboxSessionState,
    InMemorySandboxSnapshotSpec,
)
from samples.openai_agents_sdk.scenarios import (
    AgentStage,
    ArtifactExpectation,
    PauseContinueScenario,
    ScenarioDefinition,
    SnapshotBranchingScenario,
    StagedScenario,
    get_scenario,
)

_OWNER_ID = "openai-agents-sample"


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    """One host-verified file from a scenario session."""

    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class VerifiedBranchResult:
    """One independently verified result collected from a workspace fork."""

    name: str
    output: str
    artifacts: tuple[VerifiedArtifact, ...]


@dataclass(frozen=True, slots=True)
class ResumeLifecycleEvidence:
    """Host-observed identities and JSON-safe state for a pause/continue run."""

    saved_state_json: str
    source_handle: str
    live_reattached_handle: str
    replacement_handle: str


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Verified outputs, collected branches, and host selection from one scenario."""

    scenario_name: str
    stage_outputs: tuple[str, ...]
    artifacts: tuple[VerifiedArtifact, ...]
    selected_branch: str | None = None
    branch_results: tuple[VerifiedBranchResult, ...] = ()
    baseline_artifacts: tuple[VerifiedArtifact, ...] = ()
    resume_evidence: ResumeLifecycleEvidence | None = None


@dataclass(frozen=True, slots=True)
class InspectionContext:
    """Success or failure information available before session cleanup."""

    scenario_name: str
    result: ScenarioResult | None
    error: BaseException | None


class RunnerOutcome(StrEnum):
    """Terminal outcome emitted by optional runner instrumentation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LifecycleTimingCategory(StrEnum):
    """Runner-owned lifecycle timing categories."""

    WORKSPACE_SEED = "workspace_seed"
    WORKSPACE_MUTATION = "workspace_mutation"
    SNAPSHOT_PERSIST = "snapshot_persist"
    SNAPSHOT_RESTORE = "snapshot_restore"
    HOST_VERIFICATION = "host_verification"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class LifecycleTimingObservation:
    """One elapsed runner lifecycle boundary."""

    category: LifecycleTimingCategory
    operation: str
    stage_key: str | None
    outcome: RunnerOutcome
    duration_ns: int


@dataclass(frozen=True, slots=True)
class StageStartObservation:
    """Notification that a scenario stage has started."""

    stage_key: str


@dataclass(frozen=True, slots=True)
class StageFinishObservation:
    """Terminal outcome for one started scenario stage."""

    stage_key: str
    outcome: RunnerOutcome


class PerformanceClock(Protocol):
    def now_ns(self) -> int:
        """Return a monotonic performance timestamp in nanoseconds."""
        ...


class SystemPerformanceClock:
    def now_ns(self) -> int:
        return time.perf_counter_ns()


class ScenarioRunObserver(Protocol):
    def observe_lifecycle_timing(self, observation: LifecycleTimingObservation) -> None:
        """Observe one completed lifecycle boundary."""
        ...

    def observe_stage_started(self, observation: StageStartObservation) -> None:
        """Observe the start of one scenario stage."""
        ...

    def observe_stage_finished(self, observation: StageFinishObservation) -> None:
        """Observe the terminal outcome of one started scenario stage."""
        ...


class ScenarioVerificationError(RuntimeError):
    """A scenario completed without producing its required sandbox state."""


type SessionInspector = Callable[[InspectionContext, SandboxSession], Awaitable[None]]
type StageModelFactory = Callable[[str], Model]
type StageCapabilityFactory = Callable[[str], InMemorySandboxCapability]


async def run_scenario(
    *,
    model: Model,
    service: SandboxService,
    scenario: ScenarioDefinition,
    inspector: SessionInspector | None = None,
    inspect_success: bool = False,
    inspect_on_failure: bool = False,
    snapshot_store: FactorySnapshotStore | None = None,
    clock: Clock | None = None,
    stage_model_factory: StageModelFactory | None = None,
    stage_capability_factory: StageCapabilityFactory | None = None,
    performance_clock: PerformanceClock | None = None,
    observer: ScenarioRunObserver | None = None,
) -> ScenarioResult:
    """Run one scenario and keep its selected session inspectable until cleanup."""
    resolved_model_factory = (
        stage_model_factory
        if stage_model_factory is not None
        else _fixed_stage_model_factory(model)
    )
    resolved_capability_factory = (
        stage_capability_factory
        if stage_capability_factory is not None
        else _default_stage_capability_factory
    )
    resolved_performance_clock = (
        performance_clock if performance_clock is not None else SystemPerformanceClock()
    )
    client = InMemorySandboxClient(
        service,
        snapshot_store=snapshot_store,
        clock=clock,
    )
    sessions: list[OpenAISandboxSession] = []
    discard_before_close: list[OpenAISandboxSession] = []
    active_session: OpenAISandboxSession | None = None
    inspection_attempted = False
    try:
        if isinstance(scenario, StagedScenario):
            active_session = await _observe_lifecycle(
                category=LifecycleTimingCategory.WORKSPACE_SEED,
                operation="client_create",
                stage_key=None,
                action=lambda: client.create(
                    manifest=scenario.manifest_factory(),
                    options=scenario.options_factory().model_copy(update={"owner_id": _OWNER_ID}),
                ),
                clock=resolved_performance_clock,
                observer=observer,
            )
            sessions.append(active_session)
            stage_outputs = await _run_stages(
                active_session,
                scenario.stages,
                stage_model_factory=resolved_model_factory,
                stage_capability_factory=resolved_capability_factory,
                observer=observer,
            )
            artifacts = await _observe_artifact_verification(
                service=service,
                session=active_session,
                expected=scenario.expected_artifacts,
                operation="final_artifacts",
                stage_key=scenario.stages[-1].key if scenario.stages else None,
                clock=resolved_performance_clock,
                observer=observer,
            )
            result = ScenarioResult(
                scenario_name=scenario.name,
                stage_outputs=stage_outputs,
                artifacts=artifacts,
            )
        elif isinstance(scenario, SnapshotBranchingScenario):
            if snapshot_store is None or clock is None:
                raise ValueError(
                    "snapshot-backed scenarios require snapshot_store and clock dependencies"
                )
            (
                result,
                active_session,
            ) = await _run_snapshot_scenario(
                service=service,
                client=client,
                scenario=scenario,
                tracked_sessions=sessions,
                stage_model_factory=resolved_model_factory,
                stage_capability_factory=resolved_capability_factory,
                performance_clock=resolved_performance_clock,
                observer=observer,
            )
        else:
            if snapshot_store is None or clock is None:
                raise ValueError(
                    "snapshot-backed scenarios require snapshot_store and clock dependencies"
                )
            (
                result,
                active_session,
            ) = await _run_pause_continue_scenario(
                service=service,
                client=client,
                scenario=scenario,
                tracked_sessions=sessions,
                discard_before_close=discard_before_close,
                stage_model_factory=resolved_model_factory,
                stage_capability_factory=resolved_capability_factory,
                performance_clock=resolved_performance_clock,
                observer=observer,
            )
        if inspect_success and inspector is not None:
            inspection_attempted = True
            await inspector(
                InspectionContext(
                    scenario_name=scenario.name,
                    result=result,
                    error=None,
                ),
                await _core_session(service, active_session),
            )
        return result
    except BaseException as error:
        inspection_session = active_session
        if inspection_session is None and sessions:
            inspection_session = sessions[-1]
        if (
            inspect_on_failure
            and inspector is not None
            and inspection_session is not None
            and not inspection_attempted
        ):
            inspection_attempted = True
            try:
                await inspector(
                    InspectionContext(
                        scenario_name=scenario.name,
                        result=None,
                        error=error,
                    ),
                    await _core_session(service, inspection_session),
                )
            except BaseException as inspection_error:
                error.add_note(f"secondary inspection failure: {inspection_error}")
        raise
    finally:
        await _cleanup_sdk_sessions(
            client=client,
            sessions=sessions,
            primary=sys.exception(),
            discard_before_close=discard_before_close,
            performance_clock=resolved_performance_clock,
            observer=observer,
        )


async def run_sample(
    *,
    model: Model,
    service: SandboxService,
    inspector: SessionInspector | None = None,
    inspect_success: bool = False,
    inspect_on_failure: bool = False,
    snapshot_store: FactorySnapshotStore | None = None,
    clock: Clock | None = None,
    stage_model_factory: StageModelFactory | None = None,
    stage_capability_factory: StageCapabilityFactory | None = None,
    performance_clock: PerformanceClock | None = None,
    observer: ScenarioRunObserver | None = None,
) -> ScenarioResult:
    """Backward-compatible entry point for the workspace-edit scenario."""
    return await run_scenario(
        model=model,
        service=service,
        scenario=get_scenario("workspace-edit"),
        inspector=inspector,
        inspect_success=inspect_success,
        inspect_on_failure=inspect_on_failure,
        snapshot_store=snapshot_store,
        clock=clock,
        stage_model_factory=stage_model_factory,
        stage_capability_factory=stage_capability_factory,
        performance_clock=performance_clock,
        observer=observer,
    )


async def _run_snapshot_scenario(
    *,
    service: SandboxService,
    client: InMemorySandboxClient,
    scenario: SnapshotBranchingScenario,
    tracked_sessions: list[OpenAISandboxSession],
    stage_model_factory: StageModelFactory,
    stage_capability_factory: StageCapabilityFactory,
    performance_clock: PerformanceClock,
    observer: ScenarioRunObserver | None,
) -> tuple[ScenarioResult, OpenAISandboxSession]:
    source = await _observe_lifecycle(
        category=LifecycleTimingCategory.WORKSPACE_SEED,
        operation="client_create",
        stage_key=None,
        action=lambda: client.create(
            snapshot=InMemorySandboxSnapshotSpec(),
            manifest=scenario.manifest_factory(),
            options=scenario.options_factory().model_copy(update={"owner_id": _OWNER_ID}),
        ),
        clock=performance_clock,
        observer=observer,
    )
    tracked_sessions.append(source)
    baseline_output = await _run_stage(
        source,
        scenario.baseline_stage,
        stage_model_factory=stage_model_factory,
        stage_capability_factory=stage_capability_factory,
        observer=observer,
    )
    checkpoint_session = await _resume_and_start(
        client=client,
        state=_provider_state(source),
        tracked_sessions=tracked_sessions,
        operation="live_reattachment",
        stage_key=scenario.baseline_stage.key,
        performance_clock=performance_clock,
        observer=observer,
    )
    for artifact in scenario.checkpoint_artifacts:
        await _observe_lifecycle(
            category=LifecycleTimingCategory.WORKSPACE_MUTATION,
            operation="checkpoint_write",
            stage_key=scenario.baseline_stage.key,
            action=lambda artifact=artifact: checkpoint_session.write(
                Path(artifact.path),
                io.BytesIO(artifact.content),
            ),
            clock=performance_clock,
            observer=observer,
        )
    persisted_state = await _observe_lifecycle(
        category=LifecycleTimingCategory.SNAPSHOT_PERSIST,
        operation="checkpoint_persist",
        stage_key=scenario.baseline_stage.key,
        action=lambda: _stop_and_copy_state(checkpoint_session),
        clock=performance_clock,
        observer=observer,
    )
    await _retire_sdk_sessions(
        client=client,
        tracked_sessions=tracked_sessions,
        retiring=(source, checkpoint_session),
        performance_clock=performance_clock,
        observer=observer,
    )

    branch_outputs: list[str] = [baseline_output]
    branch_results: list[VerifiedBranchResult] = []
    selected_session: OpenAISandboxSession | None = None
    selected_artifacts: tuple[VerifiedArtifact, ...] | None = None
    for branch in scenario.branches:
        session = await _resume_and_start(
            client=client,
            state=persisted_state,
            tracked_sessions=tracked_sessions,
            operation="branch_resume",
            stage_key=branch.stage.key,
            performance_clock=performance_clock,
            observer=observer,
        )
        branch_output = await _run_stage(
            session,
            branch.stage,
            stage_model_factory=stage_model_factory,
            stage_capability_factory=stage_capability_factory,
            observer=observer,
        )
        branch_outputs.append(branch_output)
        artifacts = await _observe_artifact_verification(
            service=service,
            session=session,
            expected=branch.expected_artifacts,
            operation="branch_artifacts",
            stage_key=branch.stage.key,
            clock=performance_clock,
            observer=observer,
        )
        branch_results.append(
            VerifiedBranchResult(
                name=branch.name,
                output=branch_output,
                artifacts=artifacts,
            )
        )
        if branch.name == scenario.selected_branch:
            selected_session = session
            selected_artifacts = artifacts

    if selected_session is None or selected_artifacts is None:
        raise ScenarioVerificationError(
            f"selected snapshot branch {scenario.selected_branch!r} was not produced"
        )
    baseline_artifacts: tuple[VerifiedArtifact, ...] = ()
    if scenario.baseline_expected_artifacts:
        baseline_session = await _resume_and_start(
            client=client,
            state=persisted_state,
            tracked_sessions=tracked_sessions,
            operation="baseline_resume",
            stage_key=scenario.baseline_stage.key,
            performance_clock=performance_clock,
            observer=observer,
        )
        baseline_artifacts = await _observe_artifact_verification(
            service=service,
            session=baseline_session,
            expected=scenario.baseline_expected_artifacts,
            operation="baseline_artifacts",
            stage_key=scenario.baseline_stage.key,
            clock=performance_clock,
            observer=observer,
        )
    handles = {_provider_state(session).sandbox_handle for session in tracked_sessions}
    if len(handles) != len(tracked_sessions):
        raise ScenarioVerificationError("snapshot branches did not receive distinct handles")
    return (
        ScenarioResult(
            scenario_name=scenario.name,
            stage_outputs=tuple(branch_outputs),
            artifacts=selected_artifacts,
            selected_branch=scenario.selected_branch,
            branch_results=tuple(branch_results),
            baseline_artifacts=baseline_artifacts,
        ),
        selected_session,
    )


async def _run_pause_continue_scenario(
    *,
    service: SandboxService,
    client: InMemorySandboxClient,
    scenario: PauseContinueScenario,
    tracked_sessions: list[OpenAISandboxSession],
    discard_before_close: list[OpenAISandboxSession],
    stage_model_factory: StageModelFactory,
    stage_capability_factory: StageCapabilityFactory,
    performance_clock: PerformanceClock,
    observer: ScenarioRunObserver | None,
) -> tuple[ScenarioResult, OpenAISandboxSession]:
    source = await _observe_lifecycle(
        category=LifecycleTimingCategory.WORKSPACE_SEED,
        operation="client_create",
        stage_key=None,
        action=lambda: client.create(
            snapshot=InMemorySandboxSnapshotSpec(),
            manifest=scenario.manifest_factory(),
            options=scenario.options_factory().model_copy(update={"owner_id": _OWNER_ID}),
        ),
        clock=performance_clock,
        observer=observer,
    )
    tracked_sessions.append(source)
    live_reattached: OpenAISandboxSession | None = None
    try:
        initial_output = await _run_stage(
            source,
            scenario.initial_stage,
            stage_model_factory=stage_model_factory,
            stage_capability_factory=stage_capability_factory,
            observer=observer,
        )
        checkpoint_artifacts = await _observe_artifact_verification(
            service=service,
            session=source,
            expected=scenario.checkpoint_artifacts,
            operation="checkpoint_artifacts",
            stage_key=scenario.initial_stage.key,
            clock=performance_clock,
            observer=observer,
        )
        source_handle = _provider_state(source).sandbox_handle

        live_reattached = await _resume_and_start(
            client=client,
            state=_provider_state(source),
            tracked_sessions=tracked_sessions,
            operation="live_reattachment",
            stage_key=scenario.initial_stage.key,
            performance_clock=performance_clock,
            observer=observer,
        )
        live_reattached_handle = _provider_state(live_reattached).sandbox_handle
        if live_reattached_handle != source_handle:
            raise ScenarioVerificationError("live reattachment did not reuse the source handle")
        live_artifacts = await _observe_artifact_verification(
            service=service,
            session=live_reattached,
            expected=scenario.checkpoint_artifacts,
            operation="live_reattachment_artifacts",
            stage_key=scenario.initial_stage.key,
            clock=performance_clock,
            observer=observer,
        )
        if live_artifacts != checkpoint_artifacts:
            raise ScenarioVerificationError("live reattachment did not preserve checkpoint bytes")

        saved_state_json, restored_state = await _observe_lifecycle(
            category=LifecycleTimingCategory.SNAPSHOT_PERSIST,
            operation="checkpoint_persist",
            stage_key=scenario.initial_stage.key,
            action=lambda: _persist_pause_state(client, live_reattached),
            clock=performance_clock,
            observer=observer,
        )
    except BaseException:
        discard_before_close.append(tracked_sessions[-1])
        raise

    await _retire_sdk_sessions(
        client=client,
        tracked_sessions=tracked_sessions,
        retiring=(source, live_reattached),
        performance_clock=performance_clock,
        observer=observer,
    )

    continuation = await _resume_and_start(
        client=client,
        state=restored_state,
        tracked_sessions=tracked_sessions,
        operation="replacement_resume",
        stage_key=scenario.continuation_stage.key,
        performance_clock=performance_clock,
        observer=observer,
    )
    replacement_handle = _provider_state(continuation).sandbox_handle
    if replacement_handle == source_handle:
        raise ScenarioVerificationError("replacement resume reused the deleted source handle")
    await _observe_artifact_verification(
        service=service,
        session=continuation,
        expected=scenario.checkpoint_artifacts,
        operation="replacement_checkpoint_artifacts",
        stage_key=scenario.continuation_stage.key,
        clock=performance_clock,
        observer=observer,
    )
    continuation_output = await _run_stage(
        continuation,
        scenario.continuation_stage,
        stage_model_factory=stage_model_factory,
        stage_capability_factory=stage_capability_factory,
        observer=observer,
    )
    artifacts = await _observe_artifact_verification(
        service=service,
        session=continuation,
        expected=scenario.expected_artifacts,
        operation="final_artifacts",
        stage_key=scenario.continuation_stage.key,
        clock=performance_clock,
        observer=observer,
    )
    return (
        ScenarioResult(
            scenario_name=scenario.name,
            stage_outputs=(initial_output, continuation_output),
            artifacts=artifacts,
            resume_evidence=ResumeLifecycleEvidence(
                saved_state_json=saved_state_json,
                source_handle=source_handle,
                live_reattached_handle=live_reattached_handle,
                replacement_handle=replacement_handle,
            ),
        ),
        continuation,
    )


async def _retire_sdk_sessions(
    *,
    client: InMemorySandboxClient,
    tracked_sessions: list[OpenAISandboxSession],
    retiring: tuple[OpenAISandboxSession, ...],
    performance_clock: PerformanceClock,
    observer: ScenarioRunObserver | None,
) -> None:
    for session in retiring:
        tracked_sessions.remove(session)
    try:
        await _cleanup_sdk_sessions(
            client=client,
            sessions=list(retiring),
            primary=None,
            performance_clock=performance_clock,
            observer=observer,
        )
    except BaseException:
        tracked_sessions.extend(retiring)
        raise


async def _run_stages(
    session: OpenAISandboxSession,
    stages: tuple[AgentStage, ...],
    *,
    stage_model_factory: StageModelFactory,
    stage_capability_factory: StageCapabilityFactory,
    observer: ScenarioRunObserver | None,
) -> tuple[str, ...]:
    outputs: list[str] = []
    for stage in stages:
        outputs.append(
            await _run_stage(
                session,
                stage,
                stage_model_factory=stage_model_factory,
                stage_capability_factory=stage_capability_factory,
                observer=observer,
            )
        )
    return tuple(outputs)


async def _run_stage(
    session: OpenAISandboxSession,
    stage: AgentStage,
    *,
    stage_model_factory: StageModelFactory,
    stage_capability_factory: StageCapabilityFactory,
    observer: ScenarioRunObserver | None,
) -> str:
    _observe_stage_started(observer, stage.key)
    try:
        agent = SandboxAgent(
            name=stage.name,
            instructions=f"{stage.instructions}\nScenario stage key: {stage.key}",
            model=stage_model_factory(stage.key),
            capabilities=[stage_capability_factory(stage.key)],
        )
        result = await Runner.run(
            agent,
            stage.prompt,
            max_turns=stage.max_turns,
            run_config=RunConfig(
                tracing_disabled=True,
                sandbox=SandboxRunConfig(session=session),
                tool_name_collision_policy="error",
            ),
        )
        if not isinstance(result.final_output, str):
            raise ScenarioVerificationError(
                f"scenario stage {stage.key!r} final output was not text"
            )
    except (asyncio.CancelledError, SessionOperationCancelled) as error:
        _observe_stage_finished_preserving(
            observer,
            stage.key,
            RunnerOutcome.CANCELLED,
            error,
        )
        raise
    except BaseException as error:
        _observe_stage_finished_preserving(
            observer,
            stage.key,
            RunnerOutcome.FAILED,
            error,
        )
        raise
    _observe_stage_finished(observer, stage.key, RunnerOutcome.SUCCEEDED)
    return result.final_output


def _fixed_stage_model_factory(model: Model) -> StageModelFactory:
    def create(stage_key: str) -> Model:
        _ = stage_key
        return model

    return create


def _default_stage_capability_factory(stage_key: str) -> InMemorySandboxCapability:
    _ = stage_key
    return InMemorySandboxCapability()


async def _observe_lifecycle[Result](
    *,
    category: LifecycleTimingCategory,
    operation: str,
    stage_key: str | None,
    action: Callable[[], Awaitable[Result]],
    clock: PerformanceClock,
    observer: ScenarioRunObserver | None,
) -> Result:
    if observer is None:
        return await action()

    started_ns = clock.now_ns()
    try:
        result = await action()
    except (asyncio.CancelledError, SessionOperationCancelled) as error:
        _observe_lifecycle_finished_preserving(
            observer=observer,
            clock=clock,
            started_ns=started_ns,
            category=category,
            operation=operation,
            stage_key=stage_key,
            outcome=RunnerOutcome.CANCELLED,
            primary=error,
        )
        raise
    except BaseException as error:
        _observe_lifecycle_finished_preserving(
            observer=observer,
            clock=clock,
            started_ns=started_ns,
            category=category,
            operation=operation,
            stage_key=stage_key,
            outcome=RunnerOutcome.FAILED,
            primary=error,
        )
        raise

    _observe_lifecycle_finished(
        observer=observer,
        clock=clock,
        started_ns=started_ns,
        category=category,
        operation=operation,
        stage_key=stage_key,
        outcome=RunnerOutcome.SUCCEEDED,
    )
    return result


def _observe_lifecycle_finished(
    *,
    observer: ScenarioRunObserver,
    clock: PerformanceClock,
    started_ns: int,
    category: LifecycleTimingCategory,
    operation: str,
    stage_key: str | None,
    outcome: RunnerOutcome,
) -> None:
    completed_ns = clock.now_ns()
    if completed_ns < started_ns:
        raise ValueError("performance clock moved backwards")
    observer.observe_lifecycle_timing(
        LifecycleTimingObservation(
            category=category,
            operation=operation,
            stage_key=stage_key,
            outcome=outcome,
            duration_ns=completed_ns - started_ns,
        )
    )


def _observe_lifecycle_finished_preserving(
    *,
    observer: ScenarioRunObserver,
    clock: PerformanceClock,
    started_ns: int,
    category: LifecycleTimingCategory,
    operation: str,
    stage_key: str | None,
    outcome: RunnerOutcome,
    primary: BaseException,
) -> None:
    try:
        _observe_lifecycle_finished(
            observer=observer,
            clock=clock,
            started_ns=started_ns,
            category=category,
            operation=operation,
            stage_key=stage_key,
            outcome=outcome,
        )
    except BaseException as observation_error:
        primary.add_note(f"secondary lifecycle observation failure: {observation_error}")


def _observe_stage_started(observer: ScenarioRunObserver | None, stage_key: str) -> None:
    if observer is not None:
        observer.observe_stage_started(StageStartObservation(stage_key))


def _observe_stage_finished(
    observer: ScenarioRunObserver | None,
    stage_key: str,
    outcome: RunnerOutcome,
) -> None:
    if observer is not None:
        observer.observe_stage_finished(StageFinishObservation(stage_key, outcome))


def _observe_stage_finished_preserving(
    observer: ScenarioRunObserver | None,
    stage_key: str,
    outcome: RunnerOutcome,
    primary: BaseException,
) -> None:
    try:
        _observe_stage_finished(observer, stage_key, outcome)
    except BaseException as observation_error:
        primary.add_note(f"secondary stage observation failure: {observation_error}")


async def _resume_and_start(
    *,
    client: InMemorySandboxClient,
    state: InMemorySandboxSessionState,
    tracked_sessions: list[OpenAISandboxSession],
    operation: str,
    stage_key: str,
    performance_clock: PerformanceClock,
    observer: ScenarioRunObserver | None,
) -> OpenAISandboxSession:
    async def resume() -> OpenAISandboxSession:
        session = await client.resume(state)
        tracked_sessions.append(session)
        await session.start()
        return session

    return await _observe_lifecycle(
        category=LifecycleTimingCategory.SNAPSHOT_RESTORE,
        operation=operation,
        stage_key=stage_key,
        action=resume,
        clock=performance_clock,
        observer=observer,
    )


async def _stop_and_copy_state(
    session: OpenAISandboxSession,
) -> InMemorySandboxSessionState:
    await session.stop()
    return _provider_state(session).model_copy(deep=True)


async def _persist_pause_state(
    client: InMemorySandboxClient,
    session: OpenAISandboxSession,
) -> tuple[str, InMemorySandboxSessionState]:
    persisted_state = await _stop_and_copy_state(session)
    saved_state_json = json.dumps(
        client.serialize_session_state(persisted_state),
        sort_keys=True,
        separators=(",", ":"),
    )
    decoded_state = json.loads(saved_state_json)
    if not isinstance(decoded_state, dict):
        raise ScenarioVerificationError("serialized sandbox state was not a JSON object")
    restored_state = client.deserialize_session_state(cast(dict[str, object], decoded_state))
    return saved_state_json, restored_state


async def _observe_artifact_verification(
    *,
    service: SandboxService,
    session: OpenAISandboxSession,
    expected: tuple[ArtifactExpectation, ...],
    operation: str,
    stage_key: str | None,
    clock: PerformanceClock,
    observer: ScenarioRunObserver | None,
) -> tuple[VerifiedArtifact, ...]:
    async def verify() -> tuple[VerifiedArtifact, ...]:
        return await _verify_artifacts(
            await _core_session(service, session),
            expected,
        )

    return await _observe_lifecycle(
        category=LifecycleTimingCategory.HOST_VERIFICATION,
        operation=operation,
        stage_key=stage_key,
        action=verify,
        clock=clock,
        observer=observer,
    )


async def _verify_artifacts(
    session: SandboxSession,
    expected: tuple[ArtifactExpectation, ...],
) -> tuple[VerifiedArtifact, ...]:
    verified: list[VerifiedArtifact] = []
    for artifact in expected:
        result = await session.read_bytes(ReadBytesRequest(path=artifact.path))
        if result.content != artifact.content:
            raise ScenarioVerificationError(
                f"{artifact.path} did not contain the required final content"
            )
        verified.append(VerifiedArtifact(artifact.path, result.content))
    return tuple(verified)


async def _core_session(
    service: SandboxService,
    sdk_session: OpenAISandboxSession,
) -> SandboxSession:
    state = _provider_state(sdk_session)
    return await service.get_session(SandboxHandle(UUID(state.sandbox_handle)))


def _provider_state(session: OpenAISandboxSession) -> InMemorySandboxSessionState:
    state = session.state
    if not isinstance(state, InMemorySandboxSessionState):
        raise ScenarioVerificationError("unexpected sandbox provider state")
    return state


async def _cleanup_sdk_sessions(
    *,
    client: InMemorySandboxClient,
    sessions: list[OpenAISandboxSession],
    primary: BaseException | None,
    discard_before_close: list[OpenAISandboxSession] | None = None,
    performance_clock: PerformanceClock,
    observer: ScenarioRunObserver | None,
) -> None:
    cleanup_errors: list[tuple[str, BaseException]] = []
    for session in reversed(sessions):
        discard = discard_before_close is not None and any(
            session is candidate for candidate in discard_before_close
        )
        if discard:
            try:
                await _observe_lifecycle(
                    category=LifecycleTimingCategory.CLEANUP,
                    operation="backend_delete",
                    stage_key=None,
                    action=lambda session=session: client.delete(session),
                    clock=performance_clock,
                    observer=observer,
                )
            except BaseException as error:
                cleanup_errors.append(("backend delete", error))
        try:
            await _observe_lifecycle(
                category=LifecycleTimingCategory.CLEANUP,
                operation="sdk_session_close",
                stage_key=None,
                action=session.aclose,
                clock=performance_clock,
                observer=observer,
            )
        except BaseException as error:
            cleanup_errors.append(("SDK session close", error))
        if not discard:
            try:
                await _observe_lifecycle(
                    category=LifecycleTimingCategory.CLEANUP,
                    operation="backend_delete",
                    stage_key=None,
                    action=lambda session=session: client.delete(session),
                    clock=performance_clock,
                    observer=observer,
                )
            except BaseException as error:
                cleanup_errors.append(("backend delete", error))
    _surface_cleanup_errors(primary, cleanup_errors)


def _surface_cleanup_errors(
    primary: BaseException | None,
    cleanup_errors: list[tuple[str, BaseException]],
) -> None:
    if not cleanup_errors:
        return
    if primary is not None:
        for operation, error in cleanup_errors:
            primary.add_note(f"secondary {operation} failure: {error}")
        return

    operation, failure = cleanup_errors[0]
    failure.add_note(f"{operation} failed during scenario cleanup")
    for secondary_operation, secondary in cleanup_errors[1:]:
        failure.add_note(f"secondary {secondary_operation} failure: {secondary}")
    raise failure
