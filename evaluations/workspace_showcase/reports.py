"""Markdown rendering for workspace-showcase live evaluation records."""

from __future__ import annotations

from evaluations.workspace_showcase.schema import (
    TimingCategory,
    VerificationCheckKind,
    WorkspaceShowcaseRecord,
)


def render_markdown(record: WorkspaceShowcaseRecord) -> str:
    matched = sum(artifact.matched for artifact in record.artifacts)
    unsupported = sum(interaction.unsupported for interaction in record.tool_interactions)
    repairs = sum(
        interaction.repair_of_sequence is not None for interaction in record.tool_interactions
    )
    lines = [
        "# Workspace Showcase Live Evaluation",
        "",
        f"- Schema: `{record.schema_version}`",
        f"- Outcome: `{record.outcome.value}`",
        f"- Provider: `{record.metadata.provider}`",
        f"- Model/deployment: `{record.metadata.model_or_deployment}`",
        f"- Scenario: `{record.metadata.scenario}`",
        f"- Fixture version: `{record.metadata.fixture_version}`",
        f"- Started (UTC): `{record.metadata.started_at_utc}`",
        f"- Approval reference: `{record.metadata.approval_reference}`",
        f"- Verification: `{matched}/{len(record.artifacts)} matched`",
        f"- Unsupported interactions: `{unsupported}`",
        f"- Repair interactions: `{repairs}`",
        "",
        "## Versions",
        "",
        f"- mem-sandbox: `{record.metadata.mem_sandbox_version}`",
        (f"- mem-sandbox-openai-agents: `{record.metadata.mem_sandbox_openai_agents_version}`"),
        f"- openai-agents: `{record.metadata.openai_agents_version}`",
        f"- openai: `{record.metadata.openai_version}`",
        f"- Python: `{record.metadata.python_version}`",
        f"- OS: `{record.metadata.operating_system}`",
        f"- Architecture: `{record.metadata.architecture}`",
        "",
        "## Stage outcomes",
        "",
        "| Sequence | Stage | Outcome |",
        "|---:|---|---|",
    ]
    for stage in record.stages:
        lines.append(f"| {stage.sequence} | {stage.stage} | {stage.outcome.value} |")

    lines.extend(
        [
            "",
            "## Verification by check",
            "",
            "| Check | Artifacts | Matched |",
            "|---|---:|---:|",
        ]
    )
    for check_kind in VerificationCheckKind:
        artifacts = [
            artifact for artifact in record.artifacts if check_kind in artifact.check_kinds
        ]
        lines.append(
            f"| {check_kind.value} | {len(artifacts)} | "
            f"{sum(artifact.matched for artifact in artifacts)} |"
        )

    lines.extend(
        [
            "",
            "## Artifact verification",
            "",
            "| Path | Check kinds | Match state | Stable code |",
            "|---|---|---|---|",
        ]
    )
    for artifact in record.artifacts:
        checks = ", ".join(check.value for check in artifact.check_kinds)
        state = "matched" if artifact.matched else "unmatched"
        lines.append(f"| {artifact.path} | {checks} | {state} | {artifact.error_code or 'none'} |")

    lines.extend(
        [
            "",
            "## Tool interactions",
            "",
            (
                "| Sequence | Stage | Tool | Outcome | Stable code | Unsupported | "
                "Repair of | Duration ns |"
            ),
            "|---:|---|---|---|---|---|---:|---:|",
        ]
    )
    for interaction in record.tool_interactions:
        stable_code = interaction.error_code or interaction.error_category or "none"
        repair = (
            "none"
            if interaction.repair_of_sequence is None
            else str(interaction.repair_of_sequence)
        )
        lines.append(
            f"| {interaction.sequence} | {interaction.stage} | "
            f"{interaction.tool_name} | {interaction.outcome.value} | "
            f"{stable_code} | {'yes' if interaction.unsupported else 'no'} | "
            f"{repair} | {interaction.duration_ns} |"
        )

    lines.extend(
        [
            "",
            "## Timing by category",
            "",
            "| Category | Samples | Total ns |",
            "|---|---:|---:|",
        ]
    )
    for category in TimingCategory:
        samples = [sample for sample in record.timings if sample.category is category]
        lines.append(
            f"| {category.value} | {len(samples)} | "
            f"{sum(sample.duration_ns for sample in samples)} |"
        )

    usage = record.usage
    lines.extend(
        [
            "",
            "## Usage",
            "",
            f"- Model calls: `{usage.model_call_count}`",
            f"- Token completeness: `{usage.token_completeness.value}`",
            f"- Input tokens: `{_optional(usage.input_tokens)}`",
            f"- Output tokens: `{_optional(usage.output_tokens)}`",
            f"- Total tokens: `{_optional(usage.total_tokens)}`",
            f"- Cost completeness: `{usage.cost_completeness.value}`",
            f"- Provider-reported cost: `{_optional(usage.provider_cost)}`",
            "",
            "## Failure attribution",
            "",
        ]
    )
    if record.failure is None:
        lines.append("- Failure attribution: `none`")
    else:
        lines.extend(
            [
                f"- Failure attribution: `{record.failure.category.value}`",
                f"- Exception type: `{record.failure.exception_type}`",
                f"- Stage: `{record.failure.stage or 'none'}`",
                f"- Stable code: `{record.failure.stable_code or 'none'}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This result is non-deterministic.",
            "- This result is non-gating.",
            "- This result is provider- and machine-specific.",
            "- This result is not directly comparable across uncontrolled runs.",
            "",
        ]
    )
    return "\n".join(lines)


def _optional(value: int | str | None) -> str:
    return "unavailable" if value is None else str(value)
