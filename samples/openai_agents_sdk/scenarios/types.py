"""OpenAI Agents SDK-shaped contracts for runnable scenarios."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agents.sandbox import Manifest

from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.session import SessionPolicyEngine
from mem_sandbox_openai_agents import InMemorySandboxClientOptions


def _default_options() -> InMemorySandboxClientOptions:
    return InMemorySandboxClientOptions()


def _allow_all_policy() -> SessionPolicyEngine:
    return AllowAllPolicyEngine()


@dataclass(frozen=True, slots=True)
class ArtifactExpectation:
    """One exact host-verified file produced or preserved by a scenario."""

    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class AgentStage:
    """One bounded agent invocation against the scenario session."""

    key: str
    name: str
    prompt: str
    max_turns: int = 8
    instructions: str = (
        "Follow the task exactly. Use only the provided MemSandbox tools for workspace "
        "operations and surface any tool failure."
    )


@dataclass(frozen=True, slots=True)
class StagedScenario:
    """One or more sequential agents sharing a single sandbox session."""

    name: str
    description: str
    manifest_factory: Callable[[], Manifest]
    stages: tuple[AgentStage, ...]
    expected_artifacts: tuple[ArtifactExpectation, ...]
    options_factory: Callable[[], InMemorySandboxClientOptions] = _default_options
    policy_engine_factory: Callable[[], SessionPolicyEngine] = _allow_all_policy


@dataclass(frozen=True, slots=True)
class PauseContinueScenario:
    """Two fresh agent runs connected only by persisted workspace state."""

    name: str
    description: str
    manifest_factory: Callable[[], Manifest]
    initial_stage: AgentStage
    continuation_stage: AgentStage
    checkpoint_artifacts: tuple[ArtifactExpectation, ...]
    expected_artifacts: tuple[ArtifactExpectation, ...]
    options_factory: Callable[[], InMemorySandboxClientOptions] = _default_options
    policy_engine_factory: Callable[[], SessionPolicyEngine] = _allow_all_policy


@dataclass(frozen=True, slots=True)
class SnapshotBranch:
    """One isolated agent alternative resumed from a shared persisted state."""

    name: str
    stage: AgentStage
    expected_artifacts: tuple[ArtifactExpectation, ...]


@dataclass(frozen=True, slots=True)
class SnapshotBranchingScenario:
    """A baseline agent followed by isolated alternatives from one snapshot."""

    name: str
    description: str
    manifest_factory: Callable[[], Manifest]
    baseline_stage: AgentStage
    checkpoint_artifacts: tuple[ArtifactExpectation, ...]
    branches: tuple[SnapshotBranch, ...]
    selected_branch: str
    baseline_expected_artifacts: tuple[ArtifactExpectation, ...] = ()
    options_factory: Callable[[], InMemorySandboxClientOptions] = _default_options
    policy_engine_factory: Callable[[], SessionPolicyEngine] = _allow_all_policy


type ScenarioDefinition = StagedScenario | PauseContinueScenario | SnapshotBranchingScenario
