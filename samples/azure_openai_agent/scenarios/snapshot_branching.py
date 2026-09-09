"""Host-owned snapshot branching scenario."""

from agents.sandbox import Manifest
from agents.sandbox.entries import File

from samples.azure_openai_agent.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    SnapshotBranch,
    SnapshotBranchingScenario,
)

CHOICE_PATH = "/workspace/choice.txt"
PLAN_PATH = "/workspace/branch-plan.txt"
PLAN_CONTENT = b"evaluate=conservative,aggressive\n"
CHECKPOINT_PATH = "/workspace/baseline-confirmed.txt"
CHECKPOINT_CONTENT = b"baseline=confirmed\n"
CONSERVATIVE_CONTENT = b"choice=conservative\n"
AGGRESSIVE_CONTENT = b"choice=aggressive\n"


def _manifest() -> Manifest:
    return Manifest(
        entries={
            "choice.txt": File(content=b"choice=baseline\n"),
            "branch-plan.txt": File(content=PLAN_CONTENT),
        }
    )


SCENARIO = SnapshotBranchingScenario(
    name="snapshot-branching",
    description="Run two isolated alternatives from one host-owned snapshot.",
    manifest_factory=_manifest,
    baseline_stage=AgentStage(
        key="snapshot-baseline",
        name="Branch planner",
        prompt=f"""SCENARIO snapshot-branching STAGE baseline
Read {CHOICE_PATH} and {PLAN_PATH}. Confirm that the baseline choice and both planned
alternatives are present. Do not modify either file.
""",
    ),
    checkpoint_artifacts=(ArtifactExpectation(CHECKPOINT_PATH, CHECKPOINT_CONTENT),),
    branches=(
        SnapshotBranch(
            name="conservative",
            stage=AgentStage(
                key="snapshot-conservative",
                name="Conservative alternative",
                prompt=f"""SCENARIO snapshot-branching BRANCH conservative
Read {PLAN_PATH} and apply a hash-guarded patch changing {CHOICE_PATH} from
choice=baseline to choice=conservative. Return a concise completion sentence.
""",
            ),
            expected_artifacts=(
                ArtifactExpectation(CHOICE_PATH, CONSERVATIVE_CONTENT),
                ArtifactExpectation(PLAN_PATH, PLAN_CONTENT),
                ArtifactExpectation(CHECKPOINT_PATH, CHECKPOINT_CONTENT),
            ),
        ),
        SnapshotBranch(
            name="aggressive",
            stage=AgentStage(
                key="snapshot-aggressive",
                name="Aggressive alternative",
                prompt=f"""SCENARIO snapshot-branching BRANCH aggressive
Read {PLAN_PATH} and apply a hash-guarded patch changing {CHOICE_PATH} from
choice=baseline to choice=aggressive. Return a concise completion sentence.
""",
            ),
            expected_artifacts=(
                ArtifactExpectation(CHOICE_PATH, AGGRESSIVE_CONTENT),
                ArtifactExpectation(PLAN_PATH, PLAN_CONTENT),
                ArtifactExpectation(CHECKPOINT_PATH, CHECKPOINT_CONTENT),
            ),
        ),
    ),
    selected_branch="aggressive",
)
