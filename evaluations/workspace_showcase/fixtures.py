"""Versioned, provider-independent fixtures for workspace-showcase evaluations."""

from __future__ import annotations

from dataclasses import dataclass

from samples.openai_agents_sdk.scenarios import ScenarioDefinition, StagedScenario

from evaluations.workspace_showcase.schema import VerificationCheckKind


class FixtureDriftError(RuntimeError):
    """The registered sample scenario no longer matches its evaluation fixture."""


@dataclass(frozen=True, slots=True)
class EvaluationArtifactFixture:
    """One exact expected artifact and its evaluation check classifications."""

    path: str
    expected_content: bytes
    check_kinds: tuple[VerificationCheckKind, ...]

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("evaluation artifact path must not be empty")
        if not self.check_kinds:
            raise ValueError("evaluation artifact check kinds must not be empty")


@dataclass(frozen=True, slots=True)
class WorkspaceShowcaseFixture:
    """Independent versioned contract for one registered sample scenario."""

    scenario: str
    version: int
    stage_keys: tuple[str, ...]
    artifacts: tuple[EvaluationArtifactFixture, ...]

    def __post_init__(self) -> None:
        if not self.scenario:
            raise ValueError("evaluation fixture scenario must not be empty")
        if self.version < 1:
            raise ValueError("evaluation fixture version must be positive")
        if not self.stage_keys or len(set(self.stage_keys)) != len(self.stage_keys):
            raise ValueError("evaluation fixture stage keys must be non-empty and unique")
        paths = tuple(artifact.path for artifact in self.artifacts)
        if not paths or len(set(paths)) != len(paths):
            raise ValueError("evaluation fixture artifact paths must be non-empty and unique")


_DOCUMENT_REVIEW = WorkspaceShowcaseFixture(
    scenario="document-review",
    version=1,
    stage_keys=(
        "document-review-editor",
        "document-review-reviewer",
    ),
    artifacts=(
        EvaluationArtifactFixture(
            path="/workspace/source/release-brief.txt",
            expected_content=b"""product=Orion Workspace
version=2.4.0
release_date=2026-09-18
minimum_python=3.12
""",
            check_kinds=(VerificationCheckKind.UNRELATED_CONTENT_PRESERVATION,),
        ),
        EvaluationArtifactFixture(
            path="/workspace/review/instructions.md",
            expected_content=b"""# Review instructions

Allowed edits:
- Release title and summary sentence.
- Python requirement in Compatibility.

Protected content:
- The Upgrade notes heading and paragraph.
- This instruction file and the source brief.

Required review output:
- Markdown with a status and evidence table.
- Each finding must cite both the draft and source brief paths.
""",
            check_kinds=(VerificationCheckKind.UNRELATED_CONTENT_PRESERVATION,),
        ),
        EvaluationArtifactFixture(
            path="/workspace/drafts/release-notes.md",
            expected_content=b"""# Orion Workspace 2.4.0

Orion Workspace 2.4.0 will be released on September 18, 2026.

## Compatibility

Python 3.12 or newer is required.

## Upgrade notes

Back up the workspace before upgrading.
""",
            check_kinds=(
                VerificationCheckKind.OUTPUT_CORRECTNESS,
                VerificationCheckKind.UNRELATED_CONTENT_PRESERVATION,
            ),
        ),
        EvaluationArtifactFixture(
            path="/workspace/review/findings.md",
            expected_content=b"""# Review

Status: approved

| Field | Draft evidence | Source evidence |
| --- | --- | --- |
| Version | `/workspace/drafts/release-notes.md`: `2.4.0` | `/workspace/source/release-brief.txt`: `version=2.4.0` |
| Release date | `/workspace/drafts/release-notes.md`: `September 18, 2026` | `/workspace/source/release-brief.txt`: `release_date=2026-09-18` |
| Python | `/workspace/drafts/release-notes.md`: `3.12` | `/workspace/source/release-brief.txt`: `minimum_python=3.12` |
| Protected content | `/workspace/drafts/release-notes.md`: `Upgrade notes` preserved | `/workspace/review/instructions.md`: protected section requirement |
""",
            check_kinds=(VerificationCheckKind.EVIDENCE_ACCURACY,),
        ),
    ),
)

_MULTI_AGENT_HANDOFF = WorkspaceShowcaseFixture(
    scenario="multi-agent-handoff",
    version=1,
    stage_keys=(
        "multi-agent-plan",
        "multi-agent-implement",
        "multi-agent-review",
    ),
    artifacts=(
        EvaluationArtifactFixture(
            path="/workspace/app.conf",
            expected_content=b"cache=true\n",
            check_kinds=(VerificationCheckKind.OUTPUT_CORRECTNESS,),
        ),
        EvaluationArtifactFixture(
            path="/workspace/handoff/plan.txt",
            expected_content=b"change=enable-cache\n",
            check_kinds=(VerificationCheckKind.OUTPUT_CORRECTNESS,),
        ),
        EvaluationArtifactFixture(
            path="/workspace/handoff/review.txt",
            expected_content=b"approved=true\n",
            check_kinds=(VerificationCheckKind.EVIDENCE_ACCURACY,),
        ),
    ),
)

_FIXTURES = (_DOCUMENT_REVIEW, _MULTI_AGENT_HANDOFF)
_FIXTURES_BY_SCENARIO = {fixture.scenario: fixture for fixture in _FIXTURES}


def list_evaluation_fixtures() -> tuple[WorkspaceShowcaseFixture, ...]:
    """Return the supported evaluation fixtures in stable CLI order."""
    return _FIXTURES


def get_evaluation_fixture(scenario: str) -> WorkspaceShowcaseFixture:
    """Resolve one supported evaluation fixture."""
    try:
        return _FIXTURES_BY_SCENARIO[scenario]
    except KeyError as error:
        raise ValueError("unsupported workspace-showcase scenario") from error


def validate_fixture_against_scenario(
    fixture: WorkspaceShowcaseFixture,
    scenario: ScenarioDefinition,
) -> StagedScenario:
    """Fail explicitly when a registered scenario drifts from its pinned fixture."""
    if not isinstance(scenario, StagedScenario):
        raise FixtureDriftError("workspace-showcase fixture requires a staged scenario")
    if scenario.name != fixture.scenario:
        raise FixtureDriftError("registered scenario name does not match evaluation fixture")

    registered_stage_keys = tuple(stage.key for stage in scenario.stages)
    if registered_stage_keys != fixture.stage_keys:
        raise FixtureDriftError("registered scenario stage order drifted from evaluation fixture")

    registered_artifacts = scenario.expected_artifacts
    if len(registered_artifacts) != len(fixture.artifacts):
        raise FixtureDriftError(
            "registered scenario artifact count drifted from evaluation fixture"
        )
    for index, (registered, expected) in enumerate(
        zip(registered_artifacts, fixture.artifacts, strict=True),
        start=1,
    ):
        if registered.path != expected.path:
            raise FixtureDriftError(
                f"registered scenario artifact path drifted at position {index}"
            )
        if registered.content != expected.expected_content:
            raise FixtureDriftError(
                f"registered scenario artifact content drifted at position {index}"
            )
    return scenario
