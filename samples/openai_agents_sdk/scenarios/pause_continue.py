"""Pause and continue work through JSON-safe snapshot-backed workspace state."""

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from mem_sandbox.core import OperationKind
from mem_sandbox.policy import PolicyDecision, PolicyRequest
from mem_sandbox.session import SessionPolicyEngine
from samples.openai_agents_sdk.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    PauseContinueScenario,
)

REQUEST_PATH = "/workspace/source/release-request.md"
STATUS_PATH = "/workspace/state/status.txt"
CHECKPOINT_PATH = "/workspace/handoff/checkpoint.md"
CONTINUATION_PATH = "/workspace/output/continuation.md"

REQUEST_CONTENT = b"""# Release request

- Service: orion-api
- Target: canary
- Window: 02:00 UTC
"""
READY_STATUS = b"state=ready\n"
PAUSED_STATUS = b"state=paused\n"
COMPLETED_STATUS = b"state=completed\n"
CHECKPOINT_CONTENT = b"""# Continuation checkpoint

- Service: orion-api
- Target: canary
- Window: 02:00 UTC
- Next action: publish a continuation result from this saved workspace.
"""
CONTINUATION_CONTENT = b"""# Continuation result

- Service: orion-api
- Target: canary
- Window: 02:00 UTC
- Status: resumed from `/workspace/handoff/checkpoint.md`
"""


class PauseContinuePolicyEngine:
    """Protect source and status files while allowing bounded workflow artifacts."""

    async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        if request.operation_kind is OperationKind.EXECUTE:
            return PolicyDecision(
                allowed=False,
                reason_code="pause_continue_commands_disabled",
                effective_limits=request.requested_limits,
            )
        allowed_writes = {CHECKPOINT_PATH, CONTINUATION_PATH}
        if request.operation_kind is OperationKind.WRITE_FILE and (
            request.path is None or request.path.value not in allowed_writes
        ):
            return PolicyDecision(
                allowed=False,
                reason_code="pause_continue_write_scope",
                effective_limits=request.requested_limits,
            )
        return PolicyDecision(
            allowed=True,
            reason_code="pause_continue_operation_allowed",
            effective_limits=request.requested_limits,
        )


def _policy() -> SessionPolicyEngine:
    return PauseContinuePolicyEngine()


def _manifest() -> Manifest:
    return Manifest(
        entries={
            "source": Dir(
                children={
                    "release-request.md": File(content=REQUEST_CONTENT),
                }
            ),
            "state": Dir(
                children={
                    "status.txt": File(content=READY_STATUS),
                }
            ),
            "handoff": Dir(children={}),
            "output": Dir(children={}),
        }
    )


SCENARIO = PauseContinueScenario(
    name="pause-continue",
    description="Persist workspace state and continue in a fresh agent run.",
    manifest_factory=_manifest,
    policy_engine_factory=_policy,
    initial_stage=AgentStage(
        key="pause-continue-initial",
        name="Checkpoint preparer",
        prompt=f"""SCENARIO pause-continue STAGE initial
Read {REQUEST_PATH} and {STATUS_PATH}. Discover the release details, patch the status
with its current content hash to show that work is paused, and write {CHECKPOINT_PATH}
with every fact a later run needs. Read the checkpoint back and return a concise
completion sentence. Do not modify the source request.
""",
    ),
    continuation_stage=AgentStage(
        key="pause-continue-resumed",
        name="Continuation worker",
        prompt=f"""SCENARIO pause-continue STAGE resumed
This is a fresh agent run with no prior conversation. Read {CHECKPOINT_PATH} and
{STATUS_PATH}; treat those workspace files as the only cross-run task context. Patch the
status with its current content hash to show completion, write {CONTINUATION_PATH}, read
the result back, and return a concise completion sentence. Do not modify the checkpoint.
""",
    ),
    checkpoint_artifacts=(
        ArtifactExpectation(REQUEST_PATH, REQUEST_CONTENT),
        ArtifactExpectation(STATUS_PATH, PAUSED_STATUS),
        ArtifactExpectation(CHECKPOINT_PATH, CHECKPOINT_CONTENT),
    ),
    expected_artifacts=(
        ArtifactExpectation(REQUEST_PATH, REQUEST_CONTENT),
        ArtifactExpectation(STATUS_PATH, COMPLETED_STATUS),
        ArtifactExpectation(CHECKPOINT_PATH, CHECKPOINT_CONTENT),
        ArtifactExpectation(CONTINUATION_PATH, CONTINUATION_CONTENT),
    ),
)
