"""Workspace quota recovery scenario."""

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from mem_sandbox.integrations.openai_agents import InMemorySandboxClientOptions
from mem_sandbox.workspace import WorkspaceLimits
from samples.azure_openai_agent.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    StagedScenario,
)

OUTPUT_PATH = "/workspace/output/status.txt"
OUTPUT_CONTENT = b"status=complete\nstrategy=compact\n"


def _manifest() -> Manifest:
    return Manifest(
        entries={
            "input": Dir(children={"requirements.txt": File(content=b"produce a compact status\n")})
        }
    )


def _options() -> InMemorySandboxClientOptions:
    return InMemorySandboxClientOptions(
        workspace_limits=WorkspaceLimits(
            max_file_bytes=64,
            max_total_bytes=1024,
            max_nodes=32,
        )
    )


SCENARIO = StagedScenario(
    name="quota-recovery",
    description="Recover from a real per-file quota failure with compact output.",
    manifest_factory=_manifest,
    options_factory=_options,
    stages=(
        AgentStage(
            key="quota-recovery",
            name="Quota-aware writer",
            prompt=f"""SCENARIO quota-recovery
First attempt to create {OUTPUT_PATH} with more than 64 UTF-8 bytes so the configured file
quota rejects it. Do not increase or bypass the quota. Recover by creating the same path
with exactly:
status=complete
strategy=compact
Include one final newline, read it, and return a concise completion sentence.
""",
        ),
    ),
    expected_artifacts=(ArtifactExpectation(OUTPUT_PATH, OUTPUT_CONTENT),),
)
