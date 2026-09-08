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
        f"- Python: `{artifact.environment.python_version}`",
        f"- Platform: `{artifact.environment.operating_system}`",
        "",
        "| Case | Driver | Profile | Samples | Median ms | Failures |",
        "|---|---|---|---:|---:|---:|",
    ]
    for case in artifact.cases:
        median = case.statistics.median_ns
        median_ms = "" if median is None else f"{median / 1_000_000:.3f}"
        lines.append(
            f"| {case.case} | {case.driver} | {case.profile} | "
            f"{case.statistics.count} | {median_ms} | {len(case.failures)} |"
        )
    lines.append("")
    return "\n".join(lines)
