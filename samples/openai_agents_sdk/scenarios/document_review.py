"""Guarded document revision followed by independent review."""

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from mem_sandbox.core import OperationKind
from mem_sandbox.policy import PolicyDecision, PolicyRequest
from mem_sandbox.session import SessionPolicyEngine
from samples.openai_agents_sdk.scenarios.types import (
    AgentStage,
    ArtifactExpectation,
    StagedScenario,
)

BRIEF_PATH = "/workspace/source/release-brief.txt"
DRAFT_PATH = "/workspace/drafts/release-notes.md"
INSTRUCTIONS_PATH = "/workspace/review/instructions.md"
REVIEW_PATH = "/workspace/review/findings.md"

BRIEF_CONTENT = b"""product=Orion Workspace
version=2.4.0
release_date=2026-09-18
minimum_python=3.12
"""
DRAFT_CONTENT = b"""# Orion Workspace 2.3.0

Orion Workspace 2.3.0 will be released on September 12, 2026.

## Compatibility

Python 3.11 or newer is required.

## Upgrade notes

Back up the workspace before upgrading.
"""
EXPECTED_DRAFT_CONTENT = b"""# Orion Workspace 2.4.0

Orion Workspace 2.4.0 will be released on September 18, 2026.

## Compatibility

Python 3.12 or newer is required.

## Upgrade notes

Back up the workspace before upgrading.
"""
INSTRUCTIONS_CONTENT = b"""# Review instructions

Allowed edits:
- Release title and summary sentence.
- Python requirement in Compatibility.

Protected content:
- The Upgrade notes heading and paragraph.
- This instruction file and the source brief.

Required review output:
- Markdown with a status and evidence table.
- Each finding must cite both the draft and source brief paths.
"""
REVIEW_CONTENT = b"""# Review

Status: approved

| Field | Draft evidence | Source evidence |
| --- | --- | --- |
| Version | `/workspace/drafts/release-notes.md`: `2.4.0` | `/workspace/source/release-brief.txt`: `version=2.4.0` |
| Release date | `/workspace/drafts/release-notes.md`: `September 18, 2026` | `/workspace/source/release-brief.txt`: `release_date=2026-09-18` |
| Python | `/workspace/drafts/release-notes.md`: `3.12` | `/workspace/source/release-brief.txt`: `minimum_python=3.12` |
| Protected content | `/workspace/drafts/release-notes.md`: `Upgrade notes` preserved | `/workspace/review/instructions.md`: protected section requirement |
"""


class DocumentReviewPolicyEngine:
    """Allow review-file creation while protecting seeded files from direct writes."""

    async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        if request.operation_kind is OperationKind.EXECUTE:
            return PolicyDecision(
                allowed=False,
                reason_code="document_review_commands_disabled",
                effective_limits=request.requested_limits,
            )
        if request.operation_kind is OperationKind.WRITE_FILE and (
            request.path is None or request.path.value != REVIEW_PATH
        ):
            return PolicyDecision(
                allowed=False,
                reason_code="document_review_write_scope",
                effective_limits=request.requested_limits,
            )
        return PolicyDecision(
            allowed=True,
            reason_code="document_review_operation_allowed",
            effective_limits=request.requested_limits,
        )


def _policy() -> SessionPolicyEngine:
    return DocumentReviewPolicyEngine()


def _manifest() -> Manifest:
    return Manifest(
        entries={
            "source": Dir(children={"release-brief.txt": File(content=BRIEF_CONTENT)}),
            "drafts": Dir(children={"release-notes.md": File(content=DRAFT_CONTENT)}),
            "review": Dir(children={"instructions.md": File(content=INSTRUCTIONS_CONTENT)}),
        }
    )


SCENARIO = StagedScenario(
    name="document-review",
    description="Revise a stale document with a guarded patch, then independently review it.",
    manifest_factory=_manifest,
    policy_engine_factory=_policy,
    stages=(
        AgentStage(
            key="document-review-editor",
            name="Document editor",
            prompt=f"""SCENARIO document-review STAGE editor
Read {BRIEF_PATH}, {DRAFT_PATH}, and {INSTRUCTIONS_PATH}. Discover every discrepancy
between the source brief and draft. Follow the allowed-edit and protected-content rules.
Read the draft immediately before editing, retain its content hash, and apply one unified
diff limited to the permitted text with that hash in expected_hashes. Read the result and
return a concise completion sentence. Do not modify the source brief or review instructions.
""",
        ),
        AgentStage(
            key="document-review-reviewer",
            name="Document reviewer",
            prompt=f"""SCENARIO document-review STAGE reviewer
Independently read {BRIEF_PATH}, {DRAFT_PATH}, and {INSTRUCTIONS_PATH}. Compare the draft
with the source brief and confirm that protected content remains unchanged. Do not modify
any existing file. Create {REVIEW_PATH} as Markdown with a status and an evidence table.
Give each reviewed field its own row, cite the draft and source brief paths with their exact
values, and include one protected-content row citing the review instructions. Read the
artifact back and return a concise review sentence.
""",
        ),
    ),
    expected_artifacts=(
        ArtifactExpectation(BRIEF_PATH, BRIEF_CONTENT),
        ArtifactExpectation(INSTRUCTIONS_PATH, INSTRUCTIONS_CONTENT),
        ArtifactExpectation(DRAFT_PATH, EXPECTED_DRAFT_CONTENT),
        ArtifactExpectation(REVIEW_PATH, REVIEW_CONTENT),
    ),
)
