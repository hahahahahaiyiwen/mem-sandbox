from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import cast

import pytest
from evaluations.workspace_showcase.reports import render_markdown
from evaluations.workspace_showcase.schema import (
    SCHEMA_VERSION,
    ArtifactVerification,
    EvaluationLimitations,
    FailureAttribution,
    FailureCategory,
    MetadataSnapshot,
    Outcome,
    StageOutcome,
    TimingCategory,
    TimingSample,
    TokenCompleteness,
    ToolInteraction,
    UsageSummary,
    VerificationCheckKind,
    WorkspaceShowcaseRecord,
    build_run_metadata,
    load_record_json,
    read_record,
    write_record,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _metadata():
    return build_run_metadata(
        provider="openai",
        model_or_deployment="gpt-test",
        provider_api_version=None,
        approval_reference="https://github.com/example/project/issues/76#issuecomment-1",
        scenario="document-review",
        fixture_version=3,
        snapshot=MetadataSnapshot(
            started_at_utc="2026-09-11T20:00:00+00:00",
            source_commit="c" * 40,
            source_dirty=True,
            mem_sandbox_version="0.2.0",
            mem_sandbox_openai_agents_version="0.2.0",
            openai_agents_version="0.22.0",
            openai_version="2.44.0",
            python_implementation="CPython",
            python_version="3.12.10",
            operating_system="Windows",
            architecture="AMD64",
        ),
    )


def _record() -> WorkspaceShowcaseRecord:
    return WorkspaceShowcaseRecord(
        schema_version=SCHEMA_VERSION,
        metadata=_metadata(),
        outcome=Outcome.SUCCEEDED,
        stages=(
            StageOutcome(sequence=1, stage="editor", outcome=Outcome.SUCCEEDED),
            StageOutcome(sequence=2, stage="reviewer", outcome=Outcome.SUCCEEDED),
        ),
        artifacts=(
            ArtifactVerification(
                path="/workspace/review.md",
                check_kinds=(
                    VerificationCheckKind.OUTPUT_CORRECTNESS,
                    VerificationCheckKind.EVIDENCE_ACCURACY,
                ),
                expected_sha256=_HASH_A,
                actual_sha256=_HASH_A,
                matched=True,
            ),
            ArtifactVerification(
                path="/workspace/source.md",
                check_kinds=(VerificationCheckKind.UNRELATED_CONTENT_PRESERVATION,),
                expected_sha256=_HASH_B,
                actual_sha256=_HASH_B,
                matched=True,
            ),
        ),
        tool_interactions=(
            ToolInteraction(
                sequence=1,
                stage="editor",
                tool_name="execute",
                duration_ns=11,
                outcome=Outcome.FAILED,
                error_category="command",
                error_code="command_not_found",
                unsupported=True,
            ),
            ToolInteraction(
                sequence=2,
                stage="editor",
                tool_name="execute",
                duration_ns=7,
                outcome=Outcome.SUCCEEDED,
                repair_of_sequence=1,
            ),
        ),
        timings=(
            TimingSample(
                category=TimingCategory.WORKSPACE_EXECUTE,
                operation="execute",
                stage="editor",
                outcome=Outcome.FAILED,
                duration_ns=11,
            ),
            TimingSample(
                category=TimingCategory.PROVIDER_MODEL,
                operation="get_response",
                stage="reviewer",
                outcome=Outcome.SUCCEEDED,
                duration_ns=23,
            ),
        ),
        usage=UsageSummary(
            model_call_count=2,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            token_completeness=TokenCompleteness.COMPLETE,
            provider_cost="0.125",
            cost_completeness=TokenCompleteness.COMPLETE,
        ),
        failure=None,
    )


def _object(text: str) -> dict[str, object]:
    value = cast(object, json.loads(text))
    assert isinstance(value, dict)
    untyped = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in untyped)
    return cast(dict[str, object], untyped)


def _set_unknown_schema(payload: dict[str, object]) -> None:
    payload["schema_version"] = SCHEMA_VERSION + 1


def _remove_usage(payload: dict[str, object]) -> None:
    del payload["usage"]


def _add_unexpected(payload: dict[str, object]) -> None:
    payload["unexpected"] = True


def _set_negative_timing(payload: dict[str, object]) -> None:
    payload["timings"] = [
        {
            "category": "cleanup",
            "duration_ns": -1,
            "operation": "service.close",
            "outcome": "succeeded",
            "stage": None,
        }
    ]


def test_record_types_are_immutable_and_round_trip_through_strict_json() -> None:
    record = _record()

    with pytest.raises(FrozenInstanceError):
        record.outcome = Outcome.FAILED  # pyright: ignore[reportAttributeAccessIssue]

    serialized = record.to_json()
    serialized.encode("ascii")

    assert serialized == record.to_json()
    assert load_record_json(serialized) == record
    assert '"schema_version": 1' in serialized
    assert serialized.index('"artifacts"') < serialized.index('"metadata"')


def test_write_record_uses_utf8_lf_and_one_trailing_newline() -> None:
    path = Path(__file__).with_name(".workspace-showcase-record.json")
    try:
        write_record(path, _record())

        payload = path.read_bytes()
        assert payload.endswith(b"\n")
        assert not payload.endswith(b"\n\n")
        payload.decode("ascii")
        assert read_record(path) == _record()
    finally:
        path.unlink(missing_ok=True)


def test_serialization_revalidates_nested_runtime_values() -> None:
    record = _record()
    object.__setattr__(record.timings[0], "duration_ns", -1)

    with pytest.raises(ValueError, match="nonnegative"):
        record.to_json()


@pytest.mark.parametrize(
    "mutate",
    [
        _set_unknown_schema,
        _remove_usage,
        _add_unexpected,
        _set_negative_timing,
    ],
)
def test_json_loader_rejects_unknown_missing_negative_and_unexpected_data(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    payload = _object(_record().to_json())
    mutate(payload)

    with pytest.raises(ValueError):
        load_record_json(json.dumps(payload))


def test_construction_validates_labels_paths_hashes_sequences_and_usage() -> None:
    with pytest.raises(ValueError, match="single-line"):
        StageOutcome(sequence=1, stage="editor\nsecret", outcome=Outcome.SUCCEEDED)
    with pytest.raises(ValueError, match="path"):
        ArtifactVerification(
            path="/workspace/../secret.txt",
            check_kinds=(VerificationCheckKind.OUTPUT_CORRECTNESS,),
            expected_sha256=_HASH_A,
            actual_sha256=_HASH_A,
            matched=True,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        ArtifactVerification(
            path="/workspace/result.txt",
            check_kinds=(VerificationCheckKind.OUTPUT_CORRECTNESS,),
            expected_sha256="bad",
            actual_sha256=_HASH_A,
            matched=False,
        )
    with pytest.raises(ValueError, match="lower sequence"):
        ToolInteraction(
            sequence=2,
            stage="editor",
            tool_name="write_file",
            duration_ns=1,
            outcome=Outcome.SUCCEEDED,
            repair_of_sequence=2,
        )
    with pytest.raises(ValueError, match="immediately preceding"):
        replace(
            _record(),
            tool_interactions=(
                ToolInteraction(
                    sequence=1,
                    stage="editor",
                    tool_name="execute",
                    duration_ns=1,
                    outcome=Outcome.FAILED,
                    error_category="command",
                    error_code="command_not_found",
                    unsupported=True,
                ),
                ToolInteraction(
                    sequence=2,
                    stage="editor",
                    tool_name="write_file",
                    duration_ns=1,
                    outcome=Outcome.SUCCEEDED,
                    repair_of_sequence=1,
                ),
            ),
        )
    with pytest.raises(ValueError, match="unavailable"):
        UsageSummary(
            model_call_count=1,
            input_tokens=0,
            output_tokens=None,
            total_tokens=None,
            token_completeness=TokenCompleteness.UNAVAILABLE,
            provider_cost=None,
            cost_completeness=TokenCompleteness.UNAVAILABLE,
        )
    with pytest.raises(ValueError, match="nonnegative"):
        UsageSummary(
            model_call_count=1,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            token_completeness=TokenCompleteness.COMPLETE,
            provider_cost="-0.01",
            cost_completeness=TokenCompleteness.COMPLETE,
        )


def test_record_rejects_repair_links_that_do_not_target_a_preceding_failure() -> None:
    record = _record()
    interactions = (
        replace(
            record.tool_interactions[0],
            outcome=Outcome.SUCCEEDED,
            error_category=None,
            error_code=None,
            unsupported=False,
        ),
        record.tool_interactions[1],
    )

    with pytest.raises(ValueError, match="failed interaction"):
        replace(record, tool_interactions=interactions)


def test_record_rejects_unknown_stage_references_and_failed_success_records() -> None:
    record = _record()

    with pytest.raises(ValueError, match="known stage"):
        replace(
            record,
            timings=(
                TimingSample(
                    category=TimingCategory.PROVIDER_MODEL,
                    operation="get_response",
                    stage="unknown-stage",
                    outcome=Outcome.SUCCEEDED,
                    duration_ns=1,
                ),
            ),
        )
    with pytest.raises(ValueError, match="succeeded record"):
        replace(
            record,
            stages=(StageOutcome(1, "editor", Outcome.FAILED, "stage_failed"),),
        )


def test_fixed_limitations_cannot_be_weakened() -> None:
    with pytest.raises(ValueError, match="non-deterministic"):
        EvaluationLimitations(non_deterministic=False)
    with pytest.raises(ValueError, match="non-gating"):
        EvaluationLimitations(gating=True)
    with pytest.raises(ValueError, match="not directly comparable"):
        EvaluationLimitations(comparable_across_uncontrolled_runs=True)


def test_failure_attribution_excludes_exception_messages() -> None:
    canary = "CANARY-secret-exception-message"
    failure = FailureAttribution.from_exception(
        FailureCategory.PROVIDER_INTEGRATION,
        RuntimeError(canary),
        stage="editor",
        stable_code="provider_request_failed",
    )
    record = replace(
        _record(),
        outcome=Outcome.FAILED,
        stages=(
            StageOutcome(1, "editor", Outcome.FAILED, "provider_request_failed"),
            StageOutcome(2, "reviewer", Outcome.INCOMPLETE),
        ),
        failure=failure,
    )

    serialized = record.to_json()
    assert failure.exception_type == "RuntimeError"
    assert canary not in serialized
    assert "message" not in serialized
    assert "traceback" not in serialized


def test_report_labels_evidence_usage_repairs_failure_and_limitations() -> None:
    record = replace(
        _record(),
        outcome=Outcome.FAILED,
        failure=FailureAttribution(
            category=FailureCategory.MODEL_BEHAVIOR,
            exception_type="ScenarioVerificationError",
            stage="reviewer",
            stable_code="artifact_mismatch",
        ),
    )

    report = render_markdown(record)

    assert "Schema: `1`" in report
    assert "Outcome: `failed`" in report
    assert "Provider: `openai`" in report
    assert "Model/deployment: `gpt-test`" in report
    assert "Scenario: `document-review`" in report
    assert "Verification: `2/2 matched`" in report
    assert "output_correctness" in report
    assert "unrelated_content_preservation" in report
    assert "evidence_accuracy" in report
    assert "mem-sandbox: `0.2.0`" in report
    assert "| 1 | editor | succeeded |" in report
    assert "| provider_model |" in report
    assert "Token completeness: `complete`" in report
    assert "Cost completeness: `complete`" in report
    assert "Unsupported interactions: `1`" in report
    assert "Repair interactions: `1`" in report
    assert "Failure attribution: `model_behavior`" in report
    assert "non-deterministic" in report
    assert "non-gating" in report
    assert "provider- and machine-specific" in report
    assert "not directly comparable" in report
    assert report.endswith("\n")
