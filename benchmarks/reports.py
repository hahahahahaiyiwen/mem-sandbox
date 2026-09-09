"""Human-readable reports generated from benchmark artifacts."""

from __future__ import annotations

from benchmarks.schema import BenchmarkRunArtifact


def render_markdown(artifact: BenchmarkRunArtifact) -> str:
    lines = [
        "# MemSandbox Benchmark Report",
        "",
        f"- Schema: `{artifact.schema_version}`",
        f"- Tier: `{artifact.metadata.tier}`",
        f"- Commit: `{artifact.metadata.source_commit or 'unknown'}`",
        f"- Source tree: `{artifact.metadata.source_tree_hash}`",
        f"- Working tree dirty: `{str(artifact.metadata.working_tree_dirty).lower()}`",
        f"- Started: `{artifact.metadata.started_at}`",
        f"- Python: `{artifact.environment.python_version}`",
        f"- Platform: `{artifact.environment.operating_system}`",
        f"- CPU: `{artifact.environment.cpu_model}`",
        f"- Logical CPUs: `{artifact.environment.logical_cpu_count or 'unknown'}`",
        f"- Runner: `{artifact.environment.runner_identity or 'unidentified'}`",
        f"- Power: `{artifact.environment.power_configuration or 'unidentified'}`",
        (
            f"- Sampling: `{artifact.metadata.warmups}` warm-ups, "
            f"`{artifact.metadata.samples}` measured samples"
        ),
        "",
        (
            "| Case | Driver | Profile | Operation files | Operation KiB | "
            "Snapshot KiB | Max retained KiB | Max peak KiB | Samples | Median ms | Failures |"
        ),
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in artifact.cases:
        median = case.statistics.median_ns
        median_ms = "" if median is None else f"{median / 1_000_000:.3f}"
        lines.append(
            f"| {case.case} | {case.driver} | {case.profile} | "
            f"{case.dimensions.operation_file_count} | "
            f"{_kib(case.dimensions.operation_bytes)} | "
            f"{_optional_kib(case.dimensions.snapshot_bytes)} | "
            f"{_optional_kib(case.memory.python_retained_bytes)} | "
            f"{_optional_kib(case.memory.python_peak_bytes)} | "
            f"{case.statistics.count} | {median_ms} | {len(case.failures)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _kib(value: int) -> str:
    return f"{value / 1024:.1f}"


def _optional_kib(value: int | None) -> str:
    return "" if value is None else _kib(value)
