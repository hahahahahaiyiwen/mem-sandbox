"""Stateful create/read/guarded-patch scenario."""

from agents.sandbox import Manifest

from samples.openai_agents_sdk.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    StagedScenario,
)

SAMPLE_PATH = "/workspace/demo/report.txt"
EXPECTED_CONTENT = b"status=complete\n"


def _manifest() -> Manifest:
    return Manifest()


SCENARIO = StagedScenario(
    name="workspace-edit",
    description="Create, read, hash-guard patch, and verify one workspace file.",
    manifest_factory=_manifest,
    stages=(
        AgentStage(
            key="workspace-edit",
            name="Workspace editor",
            prompt=f"""SCENARIO workspace-edit
Complete this exact task using only the provided MemSandbox tools:
1. Create {SAMPLE_PATH} with exactly `status=pending` followed by one newline. Create parent
   directories and require that the path does not already exist.
2. Read the file and retain the returned content_hash.
3. Apply a unified diff changing only `status=pending` to `status=complete`. Supply the
   retained hash in expected_hashes so the patch is concurrency guarded.
4. Read the file again and confirm the exact final content.
5. Return one concise completion sentence.
Do not claim success unless every tool operation succeeds.
""",
        ),
    ),
    expected_artifacts=(ArtifactExpectation(SAMPLE_PATH, EXPECTED_CONTENT),),
)
