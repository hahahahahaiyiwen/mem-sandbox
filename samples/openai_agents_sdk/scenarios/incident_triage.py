"""Deterministic multi-log incident investigation scenario."""

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from samples.openai_agents_sdk.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    StagedScenario,
)

REPORT_PATH = "/workspace/reports/incident.txt"
REPORT_CONTENT = b"primary_failure=timeout\noccurrences=3\naffected_services=api,worker\n"


def _manifest() -> Manifest:
    return Manifest(
        entries={
            "logs": Dir(
                children={
                    "api.log": File(
                        content=(
                            b"INFO request complete\n"
                            b"ERROR timeout contacting cache\n"
                            b"ERROR timeout contacting cache\n"
                        )
                    ),
                    "worker.log": File(
                        content=(
                            b"ERROR disk pressure\n"
                            b"ERROR timeout contacting cache\n"
                            b"INFO retry succeeded\n"
                        )
                    ),
                }
            )
        }
    )


SCENARIO = StagedScenario(
    name="incident-triage",
    description="Search seeded service logs and write an exact incident summary.",
    manifest_factory=_manifest,
    stages=(
        AgentStage(
            key="incident-triage",
            name="Incident investigator",
            prompt=f"""SCENARIO incident-triage
Investigate all files under /workspace/logs with the constrained command tools. Use search
and counting commands to identify the failure appearing across both services. Then create
{REPORT_PATH} with exactly:
primary_failure=timeout
occurrences=3
affected_services=api,worker
Include one final newline and return a concise completion sentence.
""",
        ),
    ),
    expected_artifacts=(ArtifactExpectation(REPORT_PATH, REPORT_CONTENT),),
)
