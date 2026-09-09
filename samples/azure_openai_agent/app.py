"""Shared Azure OpenAI scenario runner over the MemSandbox SDK adapter."""

from __future__ import annotations

import io
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from agents import RunConfig, Runner
from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.sandbox import SandboxAgent, SandboxRunConfig
from agents.sandbox.session import SandboxSession as OpenAISandboxSession
from openai import AsyncAzureOpenAI

from mem_sandbox.core import Clock
from mem_sandbox.integrations.openai_agents import (
    InMemorySandboxCapability,
    InMemorySandboxClient,
    InMemorySandboxSessionState,
    InMemorySandboxSnapshotSpec,
)
from mem_sandbox.service import FactorySnapshotStore, SandboxHandle, SandboxService
from mem_sandbox.session import ReadBytesRequest, SandboxSession
from samples.azure_openai_agent.config import AzureOpenAISettings
from samples.azure_openai_agent.scenarios import (
    AgentStage,
    ArtifactExpectation,
    ScenarioDefinition,
    SnapshotBranchingScenario,
    StagedScenario,
    get_scenario,
)

_OWNER_ID = "azure-openai-agent-sample"


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    """One host-verified file from the selected scenario session."""

    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Verified outputs from one completed scenario."""

    scenario_name: str
    stage_outputs: tuple[str, ...]
    artifacts: tuple[VerifiedArtifact, ...]
    selected_branch: str | None = None


@dataclass(frozen=True, slots=True)
class InspectionContext:
    """Success or failure information available before session cleanup."""

    scenario_name: str
    result: ScenarioResult | None
    error: BaseException | None


class ScenarioVerificationError(RuntimeError):
    """A scenario completed without producing its required sandbox state."""


type SessionInspector = Callable[[InspectionContext, SandboxSession], Awaitable[None]]


def create_azure_model(
    settings: AzureOpenAISettings,
) -> tuple[AsyncAzureOpenAI, OpenAIChatCompletionsModel]:
    """Construct the live Azure client and Agents SDK Chat Completions model."""
    client = AsyncAzureOpenAI(
        azure_endpoint=settings.endpoint,
        api_version=settings.api_version,
        api_key=settings.api_key,
    )
    model = OpenAIChatCompletionsModel(
        model=settings.deployment,
        openai_client=client,
        strict_feature_validation=True,
    )
    return client, model


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
) -> ScenarioResult:
    """Run one scenario and keep its selected session inspectable until cleanup."""
    client = InMemorySandboxClient(
        service,
        snapshot_store=snapshot_store,
        clock=clock,
    )
    sessions: list[OpenAISandboxSession] = []
    active_session: OpenAISandboxSession | None = None
    inspection_attempted = False
    try:
        if isinstance(scenario, StagedScenario):
            active_session = await client.create(
                manifest=scenario.manifest_factory(),
                options=scenario.options_factory().model_copy(update={"owner_id": _OWNER_ID}),
            )
            sessions.append(active_session)
            stage_outputs = await _run_stages(model, active_session, scenario.stages)
            core_session = await _core_session(service, active_session)
            artifacts = await _verify_artifacts(
                core_session,
                scenario.expected_artifacts,
            )
            result = ScenarioResult(
                scenario_name=scenario.name,
                stage_outputs=stage_outputs,
                artifacts=artifacts,
            )
        else:
            if snapshot_store is None or clock is None:
                raise ValueError(
                    "snapshot-branching requires snapshot_store and clock dependencies"
                )
            (
                result,
                active_session,
            ) = await _run_snapshot_scenario(
                model=model,
                service=service,
                client=client,
                scenario=scenario,
                tracked_sessions=sessions,
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
    )


async def _run_snapshot_scenario(
    *,
    model: Model,
    service: SandboxService,
    client: InMemorySandboxClient,
    scenario: SnapshotBranchingScenario,
    tracked_sessions: list[OpenAISandboxSession],
) -> tuple[ScenarioResult, OpenAISandboxSession]:
    source = await client.create(
        snapshot=InMemorySandboxSnapshotSpec(),
        manifest=scenario.manifest_factory(),
        options=scenario.options_factory().model_copy(update={"owner_id": _OWNER_ID}),
    )
    tracked_sessions.append(source)
    baseline_output = await _run_stage(model, source, scenario.baseline_stage)
    checkpoint_session = await client.resume(_provider_state(source))
    tracked_sessions.append(checkpoint_session)
    await checkpoint_session.start()
    for artifact in scenario.checkpoint_artifacts:
        await checkpoint_session.write(
            Path(artifact.path),
            io.BytesIO(artifact.content),
        )
    await checkpoint_session.stop()
    state = checkpoint_session.state
    if not isinstance(state, InMemorySandboxSessionState):
        raise ScenarioVerificationError("unexpected sandbox provider state")
    persisted_state = state.model_copy(deep=True)
    tracked_sessions.remove(checkpoint_session)
    tracked_sessions.remove(source)
    try:
        await _cleanup_sdk_sessions(
            client=client,
            sessions=[source, checkpoint_session],
            primary=None,
        )
    except BaseException:
        tracked_sessions.extend((source, checkpoint_session))
        raise

    branch_outputs: list[str] = [baseline_output]
    selected_session: OpenAISandboxSession | None = None
    selected_artifacts: tuple[VerifiedArtifact, ...] | None = None
    for branch in scenario.branches:
        session = await client.resume(persisted_state)
        tracked_sessions.append(session)
        await session.start()
        branch_outputs.append(await _run_stage(model, session, branch.stage))
        artifacts = await _verify_artifacts(
            await _core_session(service, session),
            branch.expected_artifacts,
        )
        if branch.name == scenario.selected_branch:
            selected_session = session
            selected_artifacts = artifacts

    if selected_session is None or selected_artifacts is None:
        raise ScenarioVerificationError(
            f"selected snapshot branch {scenario.selected_branch!r} was not produced"
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
        ),
        selected_session,
    )


async def _run_stages(
    model: Model,
    session: OpenAISandboxSession,
    stages: tuple[AgentStage, ...],
) -> tuple[str, ...]:
    outputs: list[str] = []
    for stage in stages:
        outputs.append(await _run_stage(model, session, stage))
    return tuple(outputs)


async def _run_stage(
    model: Model,
    session: OpenAISandboxSession,
    stage: AgentStage,
) -> str:
    agent = SandboxAgent(
        name=stage.name,
        instructions=f"{stage.instructions}\nScenario stage key: {stage.key}",
        model=model,
        capabilities=[InMemorySandboxCapability()],
    )
    result = await Runner.run(
        agent,
        stage.prompt,
        max_turns=stage.max_turns,
        run_config=RunConfig(
            tracing_disabled=True,
            sandbox=SandboxRunConfig(session=session),
        ),
    )
    if not isinstance(result.final_output, str):
        raise ScenarioVerificationError(f"scenario stage {stage.key!r} final output was not text")
    return result.final_output


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
) -> None:
    cleanup_errors: list[tuple[str, BaseException]] = []
    for session in reversed(sessions):
        try:
            await session.aclose()
        except BaseException as error:
            cleanup_errors.append(("SDK session close", error))
        try:
            await client.delete(session)
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
