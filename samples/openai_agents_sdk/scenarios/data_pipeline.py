"""Constrained command pipeline scenario."""

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from samples.openai_agents_sdk.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    StagedScenario,
)

OUTPUT_PATH = "/workspace/output/counts.txt"
OUTPUT_CONTENT = b"      1 error\n      2 login\n      2 warning\n"


def _manifest() -> Manifest:
    return Manifest(
        entries={
            "data": Dir(
                children={"events.txt": File(content=b"warning\nlogin\nlogin\nerror\nwarning\n")}
            )
        }
    )


SCENARIO = StagedScenario(
    name="data-pipeline",
    description="Generate a deterministic derived artifact with a virtual pipeline.",
    manifest_factory=_manifest,
    stages=(
        AgentStage(
            key="data-pipeline",
            name="Data pipeline operator",
            prompt=f"""SCENARIO data-pipeline
Use the execute tool to create /workspace/output and run a constrained pipeline equivalent
to `sort /workspace/data/events.txt | uniq -c > {OUTPUT_PATH}`. Read the result, confirm it
contains counts 1 error, 2 login, and 2 warning in sorted order, then return a concise
completion sentence. Do not use write_file to create the counts artifact.
""",
        ),
    ),
    expected_artifacts=(ArtifactExpectation(OUTPUT_PATH, OUTPUT_CONTENT),),
)
