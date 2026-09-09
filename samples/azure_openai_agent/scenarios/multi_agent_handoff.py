"""Sequential planner, implementer, and reviewer scenario."""

from agents.sandbox import Manifest
from agents.sandbox.entries import File

from samples.azure_openai_agent.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    StagedScenario,
)

CONFIG_PATH = "/workspace/app.conf"
PLAN_PATH = "/workspace/handoff/plan.txt"
REVIEW_PATH = "/workspace/handoff/review.txt"
CONFIG_CONTENT = b"cache=true\n"
PLAN_CONTENT = b"change=enable-cache\n"
REVIEW_CONTENT = b"approved=true\n"


def _manifest() -> Manifest:
    return Manifest(entries={"app.conf": File(content=b"cache=false\n")})


SCENARIO = StagedScenario(
    name="multi-agent-handoff",
    description="Run planner, implementer, and reviewer agents in one session.",
    manifest_factory=_manifest,
    stages=(
        AgentStage(
            key="multi-agent-plan",
            name="Planner",
            prompt=f"""SCENARIO multi-agent-handoff STAGE planner
Read {CONFIG_PATH}. Create {PLAN_PATH} with exactly `change=enable-cache` followed by one
newline. Do not modify the configuration. Return a concise handoff sentence.
""",
        ),
        AgentStage(
            key="multi-agent-implement",
            name="Implementer",
            prompt=f"""SCENARIO multi-agent-handoff STAGE implementer
Read {PLAN_PATH} and {CONFIG_PATH}. Apply a hash-guarded patch changing cache=false to
cache=true. Return a concise completion sentence.
""",
        ),
        AgentStage(
            key="multi-agent-review",
            name="Reviewer",
            prompt=f"""SCENARIO multi-agent-handoff STAGE reviewer
Read {PLAN_PATH} and {CONFIG_PATH}. If the requested change is present, create
{REVIEW_PATH} with exactly `approved=true` followed by one newline. Do not modify the
configuration. Return a concise review sentence.
""",
        ),
    ),
    expected_artifacts=(
        ArtifactExpectation(CONFIG_PATH, CONFIG_CONTENT),
        ArtifactExpectation(PLAN_PATH, PLAN_CONTENT),
        ArtifactExpectation(REVIEW_PATH, REVIEW_CONTENT),
    ),
)
