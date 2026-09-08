"""Command-line benchmark runner for smoke and controlled execution tiers."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path

from benchmarks.cases import (
    DIRECT_DRIVER,
    OPENAI_CAPABILITY_DRIVER,
    OPENAI_SANDBOX_DRIVER,
    CaseConfig,
    DriverFactory,
    measure_adapter_overhead,
    measure_burst_create,
    measure_cold_process_ready,
    measure_create_first_operation,
    measure_create_ready,
    measure_memory,
    measure_resume,
    measure_snapshot_create,
    measure_stateful_scenario,
)
from benchmarks.profiles import load_profile
from benchmarks.reports import render_markdown
from benchmarks.schema import (
    SCHEMA_VERSION,
    BenchmarkCaseResult,
    BenchmarkRunArtifact,
    build_environment_fingerprint,
    build_run_metadata,
    write_artifact,
)


async def run_suite(config: CaseConfig, *, tier: str) -> BenchmarkRunArtifact:
    if tier not in {"smoke", "reference"}:
        raise ValueError(
            "controlled benchmark execution is deferred until fresh-process runner "
            "integration is implemented"
        )
    empty = load_profile("empty")
    non_empty_profiles = (load_profile("small_project"),)
    factories: tuple[DriverFactory, ...] = (
        DIRECT_DRIVER,
        OPENAI_SANDBOX_DRIVER,
        OPENAI_CAPABILITY_DRIVER,
    )
    cases: list[BenchmarkCaseResult] = []
    for factory in factories:
        driver_name = factory.name
        create_case = "core_create_ready" if driver_name == "direct" else "adapter_create_ready"
        cases.append(await measure_create_ready(factory, empty, config, case=create_case))
        cases.append(await measure_create_first_operation(factory, empty, config))
        cases.append(await measure_memory(factory, empty, config))
        for profile in non_empty_profiles:
            cases.append(
                await measure_create_ready(
                    factory,
                    profile,
                    config,
                    case="seeded_create_ready",
                )
            )
            cases.append(await measure_create_first_operation(factory, profile, config))
            cases.append(await measure_snapshot_create(factory, profile, config))
            cases.append(await measure_resume(factory, profile, config, first_operation=False))
            cases.append(await measure_resume(factory, profile, config, first_operation=True))
            cases.append(await measure_memory(factory, profile, config))
        cases.append(await measure_burst_create(factory, empty, config))
        cases.append(await measure_stateful_scenario(factory, empty, config))
        cold_config = (
            replace(config, warmups=0, samples=min(2, config.samples))
            if tier == "reference"
            else config
        )
        cases.append(await measure_cold_process_ready(driver_name, empty, cold_config))
    cases.append(
        await measure_adapter_overhead(
            OPENAI_SANDBOX_DRIVER,
            empty,
            config,
            case="backend_adapter_overhead",
        )
    )
    cases.append(
        await measure_adapter_overhead(
            OPENAI_CAPABILITY_DRIVER,
            empty,
            config,
            case="capability_overhead",
            baseline_factory=OPENAI_SANDBOX_DRIVER,
        )
    )
    return BenchmarkRunArtifact(
        schema_version=SCHEMA_VERSION,
        metadata=build_run_metadata(
            tier=tier,
            warmups=config.warmups,
            samples=config.samples,
            minimum_case_duration_seconds=config.minimum_duration_seconds,
            concurrency=config.burst_concurrency,
        ),
        environment=build_environment_fingerprint(),
        cases=tuple(cases),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tier",
        choices=("smoke", "reference"),
        default="smoke",
    )
    parser.add_argument("--warmups", type=int)
    parser.add_argument("--samples", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.tier == "smoke":
        config = CaseConfig(
            warmups=0 if args.warmups is None else args.warmups,
            samples=1 if args.samples is None else args.samples,
            burst_concurrency=4,
            minimum_duration_seconds=0.0,
        )
    elif args.tier == "reference":
        config = CaseConfig(
            warmups=2 if args.warmups is None else args.warmups,
            samples=5 if args.samples is None else args.samples,
            burst_concurrency=4,
            minimum_duration_seconds=0.0,
        )
    else:
        raise ValueError(f"unsupported benchmark tier: {args.tier}")
    artifact = asyncio.run(run_suite(config, tier=args.tier))
    write_artifact(args.output, artifact)
    args.output.with_suffix(".md").write_text(
        render_markdown(artifact),
        encoding="utf-8",
        newline="\n",
    )
    if any(case.failures for case in artifact.cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
