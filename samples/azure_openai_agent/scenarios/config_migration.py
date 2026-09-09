"""Atomic multi-file configuration migration scenario."""

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from samples.azure_openai_agent.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    StagedScenario,
)

API_PATH = "/workspace/services/api.conf"
WORKER_PATH = "/workspace/services/worker.conf"
REPORT_PATH = "/workspace/reports/migration.txt"
MIGRATED_CONFIG = b"timeout_seconds=30\ncache=false\n"
REPORT_CONTENT = b"migrated=api,worker\nold_timeout=15\nnew_timeout=30\n"


def _manifest() -> Manifest:
    original = b"timeout_seconds=15\ncache=false\n"
    return Manifest(
        entries={
            "services": Dir(
                children={
                    "api.conf": File(content=original),
                    "worker.conf": File(content=original),
                }
            )
        }
    )


SCENARIO = StagedScenario(
    name="config-migration",
    description="Hash-guard and atomically migrate two configuration files.",
    manifest_factory=_manifest,
    stages=(
        AgentStage(
            key="config-migration",
            name="Configuration migrator",
            prompt=f"""SCENARIO config-migration
Read {API_PATH} and {WORKER_PATH}, retaining both content hashes. Apply one atomic unified
diff that changes timeout_seconds from 15 to 30 in both files and supplies both hashes in
expected_hashes. Then create {REPORT_PATH} with exactly:
migrated=api,worker
old_timeout=15
new_timeout=30
Include one final newline, verify the files, and return a concise completion sentence.
""",
        ),
    ),
    expected_artifacts=(
        ArtifactExpectation(API_PATH, MIGRATED_CONFIG),
        ArtifactExpectation(WORKER_PATH, MIGRATED_CONFIG),
        ArtifactExpectation(REPORT_PATH, REPORT_CONTENT),
    ),
)
