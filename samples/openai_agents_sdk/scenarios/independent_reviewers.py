"""Independent reviewers isolated in snapshot-backed workspace forks."""

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from mem_sandbox.core import OperationKind
from mem_sandbox.policy import PolicyDecision, PolicyRequest
from mem_sandbox.session import SessionPolicyEngine
from samples.openai_agents_sdk.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    SnapshotBranch,
    SnapshotBranchingScenario,
)

REVIEW_SOURCE_PATH = "/workspace/source/deployment-proposal.md"
REVIEW_CRITERIA_PATH = "/workspace/review/criteria.md"
REVIEW_STATUS_PATH = "/workspace/review/status.txt"
REVIEW_FINDINGS_PATH = "/workspace/review/findings.md"

REVIEW_SOURCE_CONTENT = b"""# Deployment proposal

Enable response caching for repeated read requests.

## Required safeguards

- Write requests must bypass the cache.
- Request audit records must remain complete.
- Operators need a documented rollback signal.
"""
REVIEW_CRITERIA_CONTENT = b"""# Review criteria

Review the proposal independently from the assigned perspective.

Required output:
- Update the review status with a hash-guarded patch.
- Create a Markdown findings artifact citing the source proposal.

Protected content:
- The source proposal and this criteria file must remain unchanged.
"""
BASELINE_REVIEW_STATUS = b"review=unreviewed\n"
RISK_REVIEW_STATUS = b"review=risk-needs-safeguards\n"
CLARITY_REVIEW_STATUS = b"review=clarity-revision-requested\n"
RISK_REVIEW_CONTENT = b"""# Risk review

Status: needs safeguards

- `/workspace/source/deployment-proposal.md`: caching is limited to repeated read requests.
- Write requests must bypass the cache and request audit records must remain complete.
- A rollback signal is required before approval.
"""
CLARITY_REVIEW_CONTENT = b"""# Clarity review

Status: revision requested

- `/workspace/source/deployment-proposal.md`: the proposal does not explain how operators enable caching.
- Add observable success and rollback steps while preserving the write bypass and audit requirements.
"""


class IndependentReviewPolicyEngine:
    """Protect shared inputs while allowing branch-local status and findings."""

    async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        if request.operation_kind is OperationKind.EXECUTE:
            return PolicyDecision(
                allowed=False,
                reason_code="independent_review_commands_disabled",
                effective_limits=request.requested_limits,
            )
        if request.operation_kind is OperationKind.WRITE_FILE and (
            request.path is None or request.path.value != REVIEW_FINDINGS_PATH
        ):
            return PolicyDecision(
                allowed=False,
                reason_code="independent_review_write_scope",
                effective_limits=request.requested_limits,
            )
        return PolicyDecision(
            allowed=True,
            reason_code="independent_review_operation_allowed",
            effective_limits=request.requested_limits,
        )


def _policy() -> SessionPolicyEngine:
    return IndependentReviewPolicyEngine()


def _manifest() -> Manifest:
    return Manifest(
        entries={
            "source": Dir(
                children={
                    "deployment-proposal.md": File(content=REVIEW_SOURCE_CONTENT),
                }
            ),
            "review": Dir(
                children={
                    "criteria.md": File(content=REVIEW_CRITERIA_CONTENT),
                    "status.txt": File(content=BASELINE_REVIEW_STATUS),
                }
            ),
        }
    )


SCENARIO = SnapshotBranchingScenario(
    name="independent-reviewers",
    description="Run independent reviewers in isolated forks and select one host-side.",
    manifest_factory=_manifest,
    policy_engine_factory=_policy,
    baseline_stage=AgentStage(
        key="independent-reviewers-baseline",
        name="Review coordinator",
        prompt=f"""SCENARIO independent-reviewers STAGE baseline
Read {REVIEW_SOURCE_PATH}, {REVIEW_CRITERIA_PATH}, and {REVIEW_STATUS_PATH}. Confirm the
proposal is ready for independent review without modifying any file. Return a concise
completion sentence.
""",
    ),
    checkpoint_artifacts=(),
    branches=(
        SnapshotBranch(
            name="risk",
            stage=AgentStage(
                key="independent-reviewer-risk",
                name="Risk reviewer",
                prompt=f"""SCENARIO independent-reviewers BRANCH risk
Independently read {REVIEW_SOURCE_PATH}, {REVIEW_CRITERIA_PATH}, and
{REVIEW_STATUS_PATH}. Review safety, audit, and rollback concerns. Read the status
immediately before editing, then apply one hash-guarded patch that records your review
perspective. Create {REVIEW_FINDINGS_PATH} as Markdown with a status and source-linked
findings. Read the artifact back and return a concise completion sentence. Do not modify
the source proposal or review criteria.
""",
            ),
            expected_artifacts=(
                ArtifactExpectation(REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
                ArtifactExpectation(REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
                ArtifactExpectation(REVIEW_STATUS_PATH, RISK_REVIEW_STATUS),
                ArtifactExpectation(REVIEW_FINDINGS_PATH, RISK_REVIEW_CONTENT),
            ),
        ),
        SnapshotBranch(
            name="clarity",
            stage=AgentStage(
                key="independent-reviewer-clarity",
                name="Clarity reviewer",
                prompt=f"""SCENARIO independent-reviewers BRANCH clarity
Independently read {REVIEW_SOURCE_PATH}, {REVIEW_CRITERIA_PATH}, and
{REVIEW_STATUS_PATH}. Review operator-facing clarity and actionable guidance. Read the
status immediately before editing, then apply one hash-guarded patch that records your
review perspective. Create {REVIEW_FINDINGS_PATH} as Markdown with a status and
source-linked findings. Read the artifact back and return a concise completion sentence.
Do not modify the source proposal or review criteria.
""",
            ),
            expected_artifacts=(
                ArtifactExpectation(REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
                ArtifactExpectation(REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
                ArtifactExpectation(REVIEW_STATUS_PATH, CLARITY_REVIEW_STATUS),
                ArtifactExpectation(REVIEW_FINDINGS_PATH, CLARITY_REVIEW_CONTENT),
            ),
        ),
    ),
    selected_branch="risk",
    baseline_expected_artifacts=(
        ArtifactExpectation(REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
        ArtifactExpectation(REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
        ArtifactExpectation(REVIEW_STATUS_PATH, BASELINE_REVIEW_STATUS),
    ),
)
