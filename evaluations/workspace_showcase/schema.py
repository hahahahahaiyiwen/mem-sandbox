"""Immutable, secret-safe records for workspace-showcase live evaluations."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import cast

SCHEMA_VERSION = 1

_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,127}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!()-]{0,127}$")
_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~:/?#@!$&'()*+,;=%-]{0,511}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EXCEPTION_TYPE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_UNSUPPORTED_CODES = frozenset(
    {
        "command_not_found",
        "unsupported_command",
        "unsupported_syntax",
    }
)


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INCOMPLETE = "incomplete"


class VerificationCheckKind(StrEnum):
    OUTPUT_CORRECTNESS = "output_correctness"
    UNRELATED_CONTENT_PRESERVATION = "unrelated_content_preservation"
    EVIDENCE_ACCURACY = "evidence_accuracy"


class TimingCategory(StrEnum):
    WORKSPACE_SEED = "workspace_seed"
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_MUTATION = "workspace_mutation"
    WORKSPACE_EXECUTE = "workspace_execute"
    SNAPSHOT_PERSIST = "snapshot_persist"
    SNAPSHOT_RESTORE = "snapshot_restore"
    HOST_VERIFICATION = "host_verification"
    CLEANUP = "cleanup"
    PROVIDER_MODEL = "provider_model"
    END_TO_END = "end_to_end"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


TokenCompleteness = Completeness
CostCompleteness = Completeness
TerminalOutcome = Outcome


class FailureCategory(StrEnum):
    MODEL_BEHAVIOR = "model_behavior"
    PROVIDER_INTEGRATION = "provider_integration"
    SDK_BEHAVIOR = "sdk_behavior"
    MEM_SANDBOX = "mem_sandbox"
    HOST_VERIFICATION = "host_verification"
    CLEANUP = "cleanup"
    CANCELLATION = "cancellation"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EvaluationLimitations:
    non_deterministic: bool = True
    gating: bool = False
    provider_and_machine_specific: bool = True
    comparable_across_uncontrolled_runs: bool = False

    def __post_init__(self) -> None:
        _require_bool("non_deterministic", self.non_deterministic)
        _require_bool("gating", self.gating)
        _require_bool(
            "provider_and_machine_specific",
            self.provider_and_machine_specific,
        )
        _require_bool(
            "comparable_across_uncontrolled_runs",
            self.comparable_across_uncontrolled_runs,
        )
        if not self.non_deterministic:
            raise ValueError("workspace-showcase records must remain non-deterministic")
        if self.gating:
            raise ValueError("workspace-showcase records must remain non-gating")
        if not self.provider_and_machine_specific:
            raise ValueError("workspace-showcase records are provider- and machine-specific")
        if self.comparable_across_uncontrolled_runs:
            raise ValueError(
                "workspace-showcase records are not directly comparable across uncontrolled runs"
            )


@dataclass(frozen=True, slots=True)
class MetadataSnapshot:
    started_at_utc: str
    source_commit: str | None
    source_dirty: bool
    mem_sandbox_version: str
    mem_sandbox_openai_agents_version: str
    openai_agents_version: str
    openai_version: str
    python_implementation: str
    python_version: str
    operating_system: str
    architecture: str

    def __post_init__(self) -> None:
        _require_utc_timestamp(self.started_at_utc)
        _require_commit(self.source_commit)
        _require_bool("source_dirty", self.source_dirty)
        _require_version("mem_sandbox_version", self.mem_sandbox_version)
        _require_version(
            "mem_sandbox_openai_agents_version",
            self.mem_sandbox_openai_agents_version,
        )
        _require_version("openai_agents_version", self.openai_agents_version)
        _require_version("openai_version", self.openai_version)
        _require_label("python_implementation", self.python_implementation)
        _require_version("python_version", self.python_version)
        _require_label("operating_system", self.operating_system)
        _require_label("architecture", self.architecture)


@dataclass(frozen=True, slots=True)
class RunMetadata:
    started_at_utc: str
    source_commit: str | None
    source_dirty: bool
    mem_sandbox_version: str
    mem_sandbox_openai_agents_version: str
    openai_agents_version: str
    openai_version: str
    python_implementation: str
    python_version: str
    operating_system: str
    architecture: str
    provider: str
    model_or_deployment: str
    provider_api_version: str | None
    approval_reference: str
    scenario: str
    fixture_version: int
    non_deterministic: bool = True
    gating: bool = False

    def __post_init__(self) -> None:
        MetadataSnapshot(
            started_at_utc=self.started_at_utc,
            source_commit=self.source_commit,
            source_dirty=self.source_dirty,
            mem_sandbox_version=self.mem_sandbox_version,
            mem_sandbox_openai_agents_version=self.mem_sandbox_openai_agents_version,
            openai_agents_version=self.openai_agents_version,
            openai_version=self.openai_version,
            python_implementation=self.python_implementation,
            python_version=self.python_version,
            operating_system=self.operating_system,
            architecture=self.architecture,
        )
        _require_label("provider", self.provider)
        _require_label("model_or_deployment", self.model_or_deployment)
        if self.provider_api_version is not None:
            _require_label("provider_api_version", self.provider_api_version)
        _require_reference("approval_reference", self.approval_reference)
        _require_label("scenario", self.scenario)
        _require_positive("fixture_version", self.fixture_version)
        _require_bool("non_deterministic", self.non_deterministic)
        _require_bool("gating", self.gating)
        if not self.non_deterministic:
            raise ValueError("run metadata must identify the evaluation as non-deterministic")
        if self.gating:
            raise ValueError("run metadata must identify the evaluation as non-gating")


@dataclass(frozen=True, slots=True)
class StageOutcome:
    sequence: int
    stage: str
    outcome: Outcome
    error_code: str | None = None

    def __post_init__(self) -> None:
        _require_positive("stage sequence", self.sequence)
        _require_label("stage", self.stage)
        _require_enum("stage outcome", self.outcome, Outcome)
        _require_optional_code("stage error code", self.error_code)
        if self.outcome is Outcome.SUCCEEDED and self.error_code is not None:
            raise ValueError("a succeeded stage cannot have an error code")


@dataclass(frozen=True, slots=True)
class ArtifactVerification:
    path: str
    check_kinds: tuple[VerificationCheckKind, ...]
    expected_sha256: str
    actual_sha256: str | None
    matched: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        _require_path(self.path)
        _require_tuple("artifact check_kinds", self.check_kinds)
        if not self.check_kinds:
            raise ValueError("artifact check_kinds must not be empty")
        if len(set(self.check_kinds)) != len(self.check_kinds):
            raise ValueError("artifact check_kinds must not contain duplicates")
        for check_kind in self.check_kinds:
            _require_enum("artifact check kind", check_kind, VerificationCheckKind)
        expected = _require_sha256("expected_sha256", self.expected_sha256)
        actual = (
            None
            if self.actual_sha256 is None
            else _require_sha256("actual_sha256", self.actual_sha256)
        )
        object.__setattr__(self, "expected_sha256", expected)
        object.__setattr__(self, "actual_sha256", actual)
        _require_bool("matched", self.matched)
        _require_optional_code("artifact error code", self.error_code)
        if self.matched:
            if actual is None or actual != expected:
                raise ValueError(
                    "matched artifacts require equal expected and actual SHA-256 values"
                )
            if self.error_code is not None:
                raise ValueError("matched artifacts cannot have an error code")
        elif actual == expected:
            raise ValueError("equal expected and actual SHA-256 values must be marked matched")


@dataclass(frozen=True, slots=True)
class ToolInteraction:
    sequence: int
    stage: str
    tool_name: str
    duration_ns: int
    outcome: Outcome
    error_category: str | None = None
    error_code: str | None = None
    unsupported: bool = False
    repair_of_sequence: int | None = None

    def __post_init__(self) -> None:
        _require_positive("tool interaction sequence", self.sequence)
        _require_label("tool interaction stage", self.stage)
        _require_label("tool name", self.tool_name)
        _require_nonnegative("tool duration_ns", self.duration_ns)
        _require_enum("tool outcome", self.outcome, Outcome)
        _require_optional_code("tool error category", self.error_category)
        _require_optional_code("tool error code", self.error_code)
        _require_bool("unsupported", self.unsupported)
        if self.outcome is Outcome.SUCCEEDED:
            if self.error_category is not None or self.error_code is not None:
                raise ValueError("a succeeded tool interaction cannot have error details")
            if self.unsupported:
                raise ValueError("a succeeded tool interaction cannot be unsupported")
        if self.repair_of_sequence is not None:
            _require_positive("repair_of_sequence", self.repair_of_sequence)
            if self.repair_of_sequence >= self.sequence:
                raise ValueError("repair links must reference a lower sequence number")
            if self.outcome is not Outcome.SUCCEEDED:
                raise ValueError("only a succeeded interaction can be recorded as a repair")
        implies_unsupported = (
            self.error_category == "unsupported" or self.error_code in _UNSUPPORTED_CODES
        )
        if self.unsupported != implies_unsupported:
            raise ValueError("unsupported must agree with the stable error category or code")


@dataclass(frozen=True, slots=True)
class TimingSample:
    category: TimingCategory
    operation: str
    stage: str | None
    outcome: Outcome
    duration_ns: int

    def __post_init__(self) -> None:
        _require_enum("timing category", self.category, TimingCategory)
        _require_label("timing operation", self.operation)
        if self.stage is not None:
            _require_label("timing stage", self.stage)
        _require_enum("timing outcome", self.outcome, Outcome)
        _require_nonnegative("timing duration_ns", self.duration_ns)


@dataclass(frozen=True, slots=True)
class UsageSummary:
    model_call_count: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    token_completeness: Completeness
    provider_cost: str | None
    cost_completeness: Completeness

    def __post_init__(self) -> None:
        _require_nonnegative("model_call_count", self.model_call_count)
        token_values = (self.input_tokens, self.output_tokens, self.total_tokens)
        for name, value in zip(
            ("input_tokens", "output_tokens", "total_tokens"),
            token_values,
            strict=True,
        ):
            if value is not None:
                _require_nonnegative(name, value)
        _require_enum("token_completeness", self.token_completeness, Completeness)
        _require_completeness(
            "token",
            self.token_completeness,
            token_values,
            self.model_call_count,
        )
        _require_enum("cost_completeness", self.cost_completeness, Completeness)
        if self.provider_cost is not None:
            canonical_cost = _require_decimal_cost(self.provider_cost)
            object.__setattr__(self, "provider_cost", canonical_cost)
        _require_completeness(
            "cost",
            self.cost_completeness,
            (self.provider_cost,),
            self.model_call_count,
        )


@dataclass(frozen=True, slots=True)
class FailureAttribution:
    category: FailureCategory
    exception_type: str
    stage: str | None = None
    stable_code: str | None = None

    def __post_init__(self) -> None:
        _require_enum("failure category", self.category, FailureCategory)
        _require_exception_type(self.exception_type)
        if self.stage is not None:
            _require_label("failure stage", self.stage)
        _require_optional_code("failure stable code", self.stable_code)

    @classmethod
    def from_exception(
        cls,
        category: FailureCategory,
        error: BaseException,
        *,
        stage: str | None = None,
        stable_code: str | None = None,
    ) -> FailureAttribution:
        return cls(
            category=category,
            exception_type=type(error).__name__,
            stage=stage,
            stable_code=stable_code,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceShowcaseRecord:
    schema_version: int
    metadata: RunMetadata
    outcome: Outcome
    stages: tuple[StageOutcome, ...]
    artifacts: tuple[ArtifactVerification, ...]
    tool_interactions: tuple[ToolInteraction, ...]
    timings: tuple[TimingSample, ...]
    usage: UsageSummary
    failure: FailureAttribution | None
    limitations: EvaluationLimitations = field(default_factory=EvaluationLimitations)

    def __post_init__(self) -> None:
        _validate_record(self)

    def to_json(self) -> str:
        _validate_record(self)
        return json.dumps(
            _record_to_object(self),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )


def build_run_metadata(
    *,
    provider: str,
    model_or_deployment: str,
    provider_api_version: str | None,
    approval_reference: str,
    scenario: str,
    fixture_version: int,
    snapshot: MetadataSnapshot | None = None,
) -> RunMetadata:
    selected = snapshot or capture_metadata_snapshot()
    return RunMetadata(
        started_at_utc=selected.started_at_utc,
        source_commit=selected.source_commit,
        source_dirty=selected.source_dirty,
        mem_sandbox_version=selected.mem_sandbox_version,
        mem_sandbox_openai_agents_version=selected.mem_sandbox_openai_agents_version,
        openai_agents_version=selected.openai_agents_version,
        openai_version=selected.openai_version,
        python_implementation=selected.python_implementation,
        python_version=selected.python_version,
        operating_system=selected.operating_system,
        architecture=selected.architecture,
        provider=provider,
        model_or_deployment=model_or_deployment,
        provider_api_version=provider_api_version,
        approval_reference=approval_reference,
        scenario=scenario,
        fixture_version=fixture_version,
    )


def capture_metadata_snapshot() -> MetadataSnapshot:
    return MetadataSnapshot(
        started_at_utc=datetime.now(UTC).isoformat(),
        source_commit=_source_commit(),
        source_dirty=_source_dirty(),
        mem_sandbox_version=_distribution_version("mem-sandbox"),
        mem_sandbox_openai_agents_version=_distribution_version("mem-sandbox-openai-agents"),
        openai_agents_version=_distribution_version("openai-agents"),
        openai_version=_distribution_version("openai"),
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        operating_system=platform.platform(),
        architecture=platform.machine(),
    )


def load_record_json(text: str) -> WorkspaceShowcaseRecord:
    try:
        parsed = cast(object, json.loads(text))
    except json.JSONDecodeError as error:
        raise ValueError("record is not valid JSON") from error
    return _parse_record(_expect_object("record", parsed))


def write_record(path: Path, record: WorkspaceShowcaseRecord) -> None:
    serialized = record.to_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized + "\n", encoding="utf-8", newline="\n")


def read_record(path: Path) -> WorkspaceShowcaseRecord:
    return load_record_json(path.read_text(encoding="utf-8"))


def _validate_record(record: WorkspaceShowcaseRecord) -> None:
    if type(record.schema_version) is not int or record.schema_version != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be exactly {SCHEMA_VERSION}")
    metadata = cast(object, record.metadata)
    if not isinstance(metadata, RunMetadata):
        raise TypeError("metadata must be RunMetadata")
    metadata.__post_init__()
    _require_enum("record outcome", record.outcome, Outcome)
    _require_tuple("stages", record.stages)
    _require_tuple("artifacts", record.artifacts)
    _require_tuple("tool_interactions", record.tool_interactions)
    _require_tuple("timings", record.timings)
    usage = cast(object, record.usage)
    if not isinstance(usage, UsageSummary):
        raise TypeError("usage must be UsageSummary")
    usage.__post_init__()
    failure = cast(object, record.failure)
    if failure is not None and not isinstance(failure, FailureAttribution):
        raise TypeError("failure must be FailureAttribution or None")
    if failure is not None:
        failure.__post_init__()
    limitations = cast(object, record.limitations)
    if not isinstance(limitations, EvaluationLimitations):
        raise TypeError("limitations must be EvaluationLimitations")
    limitations.__post_init__()
    if (
        record.metadata.non_deterministic != record.limitations.non_deterministic
        or record.metadata.gating != record.limitations.gating
    ):
        raise ValueError("metadata and structured limitations must agree")
    _validate_ordered_stages(record.stages)
    if record.outcome is Outcome.SUCCEEDED and any(
        stage.outcome is not Outcome.SUCCEEDED for stage in record.stages
    ):
        raise ValueError("a succeeded record requires every stage to have succeeded")
    stage_names = {stage.stage for stage in record.stages}
    _validate_artifacts(record.artifacts)
    _validate_tool_interactions(record.tool_interactions)
    for interaction in record.tool_interactions:
        if interaction.stage not in stage_names:
            raise ValueError("tool interactions must reference a known stage")
    for timing in cast(tuple[object, ...], record.timings):
        if not isinstance(timing, TimingSample):
            raise TypeError("timings must contain TimingSample values")
        timing.__post_init__()
        if timing.stage is not None and timing.stage not in stage_names:
            raise ValueError("timing samples must reference a known stage")
    if record.failure is not None:
        if record.failure.stage is not None and record.failure.stage not in stage_names:
            raise ValueError("failure attribution must reference a known stage")
    if record.outcome is Outcome.SUCCEEDED and record.failure is not None:
        raise ValueError("a succeeded record cannot have failure attribution")
    if record.outcome is not Outcome.SUCCEEDED and record.failure is None:
        raise ValueError("a non-succeeded record requires failure attribution")


def _validate_ordered_stages(stages: tuple[StageOutcome, ...]) -> None:
    names: set[str] = set()
    for expected_sequence, stage_value in enumerate(
        cast(tuple[object, ...], stages),
        start=1,
    ):
        stage = stage_value
        if not isinstance(stage, StageOutcome):
            raise TypeError("stages must contain StageOutcome values")
        stage.__post_init__()
        if stage.sequence != expected_sequence:
            raise ValueError("stage sequence numbers must be contiguous and ordered from one")
        if stage.stage in names:
            raise ValueError("stage names must be unique")
        names.add(stage.stage)


def _validate_artifacts(artifacts: tuple[ArtifactVerification, ...]) -> None:
    paths: set[str] = set()
    for artifact_value in cast(tuple[object, ...], artifacts):
        artifact = artifact_value
        if not isinstance(artifact, ArtifactVerification):
            raise TypeError("artifacts must contain ArtifactVerification values")
        artifact.__post_init__()
        if artifact.path in paths:
            raise ValueError("artifact paths must be unique")
        paths.add(artifact.path)


def _validate_tool_interactions(interactions: tuple[ToolInteraction, ...]) -> None:
    by_sequence: dict[int, ToolInteraction] = {}
    for expected_sequence, interaction_value in enumerate(
        cast(tuple[object, ...], interactions),
        start=1,
    ):
        interaction = interaction_value
        if not isinstance(interaction, ToolInteraction):
            raise TypeError("tool_interactions must contain ToolInteraction values")
        interaction.__post_init__()
        if interaction.sequence != expected_sequence:
            raise ValueError(
                "tool interaction sequence numbers must be contiguous and ordered from one"
            )
        by_sequence[interaction.sequence] = interaction
        if interaction.repair_of_sequence is None:
            continue
        repaired = by_sequence.get(interaction.repair_of_sequence)
        if (
            repaired is None
            or repaired.outcome is not Outcome.FAILED
            or repaired.sequence != interaction.sequence - 1
            or repaired.stage != interaction.stage
            or repaired.tool_name != interaction.tool_name
        ):
            raise ValueError(
                "repair links must target the immediately preceding failed "
                "interaction from the same stage and tool"
            )


def _record_to_object(record: WorkspaceShowcaseRecord) -> dict[str, object]:
    metadata = record.metadata
    limitations = record.limitations
    return {
        "schema_version": record.schema_version,
        "metadata": {
            "started_at_utc": metadata.started_at_utc,
            "source_commit": metadata.source_commit,
            "source_dirty": metadata.source_dirty,
            "mem_sandbox_version": metadata.mem_sandbox_version,
            "mem_sandbox_openai_agents_version": metadata.mem_sandbox_openai_agents_version,
            "openai_agents_version": metadata.openai_agents_version,
            "openai_version": metadata.openai_version,
            "python_implementation": metadata.python_implementation,
            "python_version": metadata.python_version,
            "operating_system": metadata.operating_system,
            "architecture": metadata.architecture,
            "provider": metadata.provider,
            "model_or_deployment": metadata.model_or_deployment,
            "provider_api_version": metadata.provider_api_version,
            "approval_reference": metadata.approval_reference,
            "scenario": metadata.scenario,
            "fixture_version": metadata.fixture_version,
            "non_deterministic": metadata.non_deterministic,
            "gating": metadata.gating,
        },
        "outcome": record.outcome.value,
        "stages": [
            {
                "sequence": stage.sequence,
                "stage": stage.stage,
                "outcome": stage.outcome.value,
                "error_code": stage.error_code,
            }
            for stage in record.stages
        ],
        "artifacts": [
            {
                "path": artifact.path,
                "check_kinds": [kind.value for kind in artifact.check_kinds],
                "expected_sha256": artifact.expected_sha256,
                "actual_sha256": artifact.actual_sha256,
                "matched": artifact.matched,
                "error_code": artifact.error_code,
            }
            for artifact in record.artifacts
        ],
        "tool_interactions": [
            {
                "sequence": interaction.sequence,
                "stage": interaction.stage,
                "tool_name": interaction.tool_name,
                "duration_ns": interaction.duration_ns,
                "outcome": interaction.outcome.value,
                "error_category": interaction.error_category,
                "error_code": interaction.error_code,
                "unsupported": interaction.unsupported,
                "repair_of_sequence": interaction.repair_of_sequence,
            }
            for interaction in record.tool_interactions
        ],
        "timings": [
            {
                "category": timing.category.value,
                "operation": timing.operation,
                "stage": timing.stage,
                "outcome": timing.outcome.value,
                "duration_ns": timing.duration_ns,
            }
            for timing in record.timings
        ],
        "usage": {
            "model_call_count": record.usage.model_call_count,
            "input_tokens": record.usage.input_tokens,
            "output_tokens": record.usage.output_tokens,
            "total_tokens": record.usage.total_tokens,
            "token_completeness": record.usage.token_completeness.value,
            "provider_cost": record.usage.provider_cost,
            "cost_completeness": record.usage.cost_completeness.value,
        },
        "failure": (
            None
            if record.failure is None
            else {
                "category": record.failure.category.value,
                "exception_type": record.failure.exception_type,
                "stage": record.failure.stage,
                "stable_code": record.failure.stable_code,
            }
        ),
        "limitations": {
            "non_deterministic": limitations.non_deterministic,
            "gating": limitations.gating,
            "provider_and_machine_specific": limitations.provider_and_machine_specific,
            "comparable_across_uncontrolled_runs": (
                limitations.comparable_across_uncontrolled_runs
            ),
        },
    }


def _parse_record(value: dict[str, object]) -> WorkspaceShowcaseRecord:
    _require_keys(
        "record",
        value,
        {
            "schema_version",
            "metadata",
            "outcome",
            "stages",
            "artifacts",
            "tool_interactions",
            "timings",
            "usage",
            "failure",
            "limitations",
        },
    )
    schema_version = _int_field(value, "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {schema_version}")
    failure_value = value["failure"]
    return WorkspaceShowcaseRecord(
        schema_version=schema_version,
        metadata=_parse_metadata(_object_field(value, "metadata")),
        outcome=_enum_field(value, "outcome", Outcome),
        stages=tuple(_parse_stage(item) for item in _array_field(value, "stages")),
        artifacts=tuple(_parse_artifact(item) for item in _array_field(value, "artifacts")),
        tool_interactions=tuple(
            _parse_tool_interaction(item) for item in _array_field(value, "tool_interactions")
        ),
        timings=tuple(_parse_timing(item) for item in _array_field(value, "timings")),
        usage=_parse_usage(_object_field(value, "usage")),
        failure=(
            None
            if failure_value is None
            else _parse_failure(_expect_object("failure", failure_value))
        ),
        limitations=_parse_limitations(_object_field(value, "limitations")),
    )


def _parse_metadata(value: dict[str, object]) -> RunMetadata:
    _require_keys(
        "metadata",
        value,
        {
            "started_at_utc",
            "source_commit",
            "source_dirty",
            "mem_sandbox_version",
            "mem_sandbox_openai_agents_version",
            "openai_agents_version",
            "openai_version",
            "python_implementation",
            "python_version",
            "operating_system",
            "architecture",
            "provider",
            "model_or_deployment",
            "provider_api_version",
            "approval_reference",
            "scenario",
            "fixture_version",
            "non_deterministic",
            "gating",
        },
    )
    return RunMetadata(
        started_at_utc=_str_field(value, "started_at_utc"),
        source_commit=_optional_str_field(value, "source_commit"),
        source_dirty=_bool_field(value, "source_dirty"),
        mem_sandbox_version=_str_field(value, "mem_sandbox_version"),
        mem_sandbox_openai_agents_version=_str_field(value, "mem_sandbox_openai_agents_version"),
        openai_agents_version=_str_field(value, "openai_agents_version"),
        openai_version=_str_field(value, "openai_version"),
        python_implementation=_str_field(value, "python_implementation"),
        python_version=_str_field(value, "python_version"),
        operating_system=_str_field(value, "operating_system"),
        architecture=_str_field(value, "architecture"),
        provider=_str_field(value, "provider"),
        model_or_deployment=_str_field(value, "model_or_deployment"),
        provider_api_version=_optional_str_field(value, "provider_api_version"),
        approval_reference=_str_field(value, "approval_reference"),
        scenario=_str_field(value, "scenario"),
        fixture_version=_int_field(value, "fixture_version"),
        non_deterministic=_bool_field(value, "non_deterministic"),
        gating=_bool_field(value, "gating"),
    )


def _parse_stage(value: object) -> StageOutcome:
    item = _expect_object("stage", value)
    _require_keys("stage", item, {"sequence", "stage", "outcome", "error_code"})
    return StageOutcome(
        sequence=_int_field(item, "sequence"),
        stage=_str_field(item, "stage"),
        outcome=_enum_field(item, "outcome", Outcome),
        error_code=_optional_str_field(item, "error_code"),
    )


def _parse_artifact(value: object) -> ArtifactVerification:
    item = _expect_object("artifact", value)
    _require_keys(
        "artifact",
        item,
        {
            "path",
            "check_kinds",
            "expected_sha256",
            "actual_sha256",
            "matched",
            "error_code",
        },
    )
    return ArtifactVerification(
        path=_str_field(item, "path"),
        check_kinds=tuple(
            _enum_value("artifact check kind", check, VerificationCheckKind)
            for check in _array_field(item, "check_kinds")
        ),
        expected_sha256=_str_field(item, "expected_sha256"),
        actual_sha256=_optional_str_field(item, "actual_sha256"),
        matched=_bool_field(item, "matched"),
        error_code=_optional_str_field(item, "error_code"),
    )


def _parse_tool_interaction(value: object) -> ToolInteraction:
    item = _expect_object("tool interaction", value)
    _require_keys(
        "tool interaction",
        item,
        {
            "sequence",
            "stage",
            "tool_name",
            "duration_ns",
            "outcome",
            "error_category",
            "error_code",
            "unsupported",
            "repair_of_sequence",
        },
    )
    return ToolInteraction(
        sequence=_int_field(item, "sequence"),
        stage=_str_field(item, "stage"),
        tool_name=_str_field(item, "tool_name"),
        duration_ns=_int_field(item, "duration_ns"),
        outcome=_enum_field(item, "outcome", Outcome),
        error_category=_optional_str_field(item, "error_category"),
        error_code=_optional_str_field(item, "error_code"),
        unsupported=_bool_field(item, "unsupported"),
        repair_of_sequence=_optional_int_field(item, "repair_of_sequence"),
    )


def _parse_timing(value: object) -> TimingSample:
    item = _expect_object("timing", value)
    _require_keys(
        "timing",
        item,
        {"category", "operation", "stage", "outcome", "duration_ns"},
    )
    return TimingSample(
        category=_enum_field(item, "category", TimingCategory),
        operation=_str_field(item, "operation"),
        stage=_optional_str_field(item, "stage"),
        outcome=_enum_field(item, "outcome", Outcome),
        duration_ns=_int_field(item, "duration_ns"),
    )


def _parse_usage(value: dict[str, object]) -> UsageSummary:
    _require_keys(
        "usage",
        value,
        {
            "model_call_count",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "token_completeness",
            "provider_cost",
            "cost_completeness",
        },
    )
    return UsageSummary(
        model_call_count=_int_field(value, "model_call_count"),
        input_tokens=_optional_int_field(value, "input_tokens"),
        output_tokens=_optional_int_field(value, "output_tokens"),
        total_tokens=_optional_int_field(value, "total_tokens"),
        token_completeness=_enum_field(value, "token_completeness", Completeness),
        provider_cost=_optional_str_field(value, "provider_cost"),
        cost_completeness=_enum_field(value, "cost_completeness", Completeness),
    )


def _parse_failure(value: dict[str, object]) -> FailureAttribution:
    _require_keys(
        "failure",
        value,
        {"category", "exception_type", "stage", "stable_code"},
    )
    return FailureAttribution(
        category=_enum_field(value, "category", FailureCategory),
        exception_type=_str_field(value, "exception_type"),
        stage=_optional_str_field(value, "stage"),
        stable_code=_optional_str_field(value, "stable_code"),
    )


def _parse_limitations(value: dict[str, object]) -> EvaluationLimitations:
    _require_keys(
        "limitations",
        value,
        {
            "non_deterministic",
            "gating",
            "provider_and_machine_specific",
            "comparable_across_uncontrolled_runs",
        },
    )
    return EvaluationLimitations(
        non_deterministic=_bool_field(value, "non_deterministic"),
        gating=_bool_field(value, "gating"),
        provider_and_machine_specific=_bool_field(value, "provider_and_machine_specific"),
        comparable_across_uncontrolled_runs=_bool_field(
            value, "comparable_across_uncontrolled_runs"
        ),
    )


def _expect_object(name: str, value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    untyped = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in untyped):
        raise ValueError(f"{name} must be a JSON object")
    return cast(dict[str, object], untyped)


def _require_keys(name: str, value: dict[str, object], expected: set[str]) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing fields: {', '.join(missing)}")
    if unexpected:
        details.append(f"unexpected fields: {', '.join(unexpected)}")
    raise ValueError(f"{name} has {'; '.join(details)}")


def _array_field(value: dict[str, object], name: str) -> list[object]:
    selected = value[name]
    if not isinstance(selected, list):
        raise ValueError(f"{name} must be a JSON array")
    return cast(list[object], selected)


def _object_field(value: dict[str, object], name: str) -> dict[str, object]:
    return _expect_object(name, value[name])


def _str_field(value: dict[str, object], name: str) -> str:
    selected = value[name]
    if not isinstance(selected, str):
        raise ValueError(f"{name} must be a string")
    return selected


def _optional_str_field(value: dict[str, object], name: str) -> str | None:
    selected = value[name]
    if selected is None:
        return None
    if not isinstance(selected, str):
        raise ValueError(f"{name} must be a string or null")
    return selected


def _int_field(value: dict[str, object], name: str) -> int:
    selected = value[name]
    if type(selected) is not int:
        raise ValueError(f"{name} must be an integer")
    return selected


def _optional_int_field(value: dict[str, object], name: str) -> int | None:
    selected = value[name]
    if selected is None:
        return None
    if type(selected) is not int:
        raise ValueError(f"{name} must be an integer or null")
    return selected


def _bool_field(value: dict[str, object], name: str) -> bool:
    selected = value[name]
    if type(selected) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return selected


def _enum_field[EnumType: StrEnum](
    value: dict[str, object],
    name: str,
    enum_type: type[EnumType],
) -> EnumType:
    return _enum_value(name, value[name], enum_type)


def _enum_value[EnumType: StrEnum](
    name: str,
    value: object,
    enum_type: type[EnumType],
) -> EnumType:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{name} is not supported") from error


def _require_utc_timestamp(value: str) -> None:
    _require_single_line("started_at_utc", value, 40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("started_at_utc must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("started_at_utc must use UTC")


def _require_commit(value: str | None) -> None:
    if value is None:
        return
    if _COMMIT_PATTERN.fullmatch(value) is None:
        raise ValueError("source_commit must be a 7-64 character lowercase hexadecimal hash")


def _require_label(name: str, value: str) -> None:
    _require_single_line(name, value, 128)
    if _LABEL_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} contains characters outside the label allowlist")


def _require_version(name: str, value: str) -> None:
    _require_single_line(name, value, 128)
    if _VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} contains characters outside the version allowlist")


def _require_reference(name: str, value: str) -> None:
    _require_single_line(name, value, 512)
    if _REFERENCE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} contains characters outside the reference allowlist")


def _require_path(value: str) -> None:
    _require_single_line("artifact path", value, 512)
    normalized = value.replace("\\", "/")
    if "//" in normalized:
        raise ValueError("artifact path must not contain empty segments")
    segments = normalized.removeprefix("/").split("/")
    if not segments or any(
        segment in {"", ".", ".."} or _PATH_SEGMENT_PATTERN.fullmatch(segment) is None
        for segment in segments
    ):
        raise ValueError("artifact path contains characters or segments outside the path allowlist")


def _require_sha256(name: str, value: str) -> str:
    normalized = value.lower()
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{name} must be a valid SHA-256 hexadecimal digest")
    return normalized


def _require_optional_code(name: str, value: str | None) -> None:
    if value is None:
        return
    _require_single_line(name, value, 64)
    if _CODE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} contains characters outside the code allowlist")


def _require_exception_type(value: str) -> None:
    _require_single_line("exception_type", value, 128)
    if _EXCEPTION_TYPE_PATTERN.fullmatch(value) is None:
        raise ValueError("exception_type contains characters outside the type allowlist")


def _require_single_line(name: str, value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > maximum or "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be a bounded, non-empty, single-line string")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must contain ASCII characters only") from error
    return value


def _require_nonnegative(name: str, value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _require_positive(name: str, value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _require_bool(name: str, value: bool) -> None:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a boolean")


def _require_tuple(name: str, value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")


def _require_enum[EnumType: StrEnum](
    name: str,
    value: object,
    enum_type: type[EnumType],
) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{name} must be {enum_type.__name__}")


def _require_completeness(
    name: str,
    completeness: Completeness,
    values: tuple[int | str | None, ...],
    model_call_count: int,
) -> None:
    supplied = sum(value is not None for value in values)
    if model_call_count == 0:
        if completeness is not Completeness.UNAVAILABLE or supplied:
            raise ValueError(f"{name} data must be unavailable when no model calls were made")
        return
    if completeness is Completeness.UNAVAILABLE and supplied:
        raise ValueError(f"{name} data marked unavailable must contain only null values")
    if completeness is Completeness.COMPLETE and supplied != len(values):
        raise ValueError(f"complete {name} data requires every value")
    if completeness is Completeness.PARTIAL and supplied == 0:
        raise ValueError(f"partial {name} data requires at least one supplied value")


def _require_decimal_cost(value: str) -> str:
    _require_single_line("provider_cost", value, 128)
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("provider_cost must be a decimal string") from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("provider_cost must be a finite nonnegative decimal string")
    return format(parsed, "f")


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _source_commit() -> str | None:
    completed = _run_git("rev-parse", "HEAD")
    if completed is None or completed.returncode != 0:
        return None
    candidate = completed.stdout.strip().lower()
    return candidate or None


def _source_dirty() -> bool:
    completed = _run_git("status", "--porcelain")
    return completed is None or completed.returncode != 0 or bool(completed.stdout.strip())


def _run_git(*arguments: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=Path(__file__).resolve().parents[2],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
