"""Command-line entry point for approved workspace-showcase live evaluations."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from samples.openai_agents_sdk.scenarios import get_scenario
from samples.shared.service import create_sample_service_bundle

from evaluations.workspace_showcase.instrumentation import (
    PerformanceClock,
    ProviderCostSource,
)
from evaluations.workspace_showcase.runner import (
    ProviderKind,
    ProviderLoader,
    ScenarioExecutor,
    ScenarioResolver,
    ServiceBundleFactory,
    execute_scenario,
    load_live_provider,
    run_workspace_showcase,
)
from evaluations.workspace_showcase.schema import MetadataSnapshot, WorkspaceShowcaseRecord


class _Arguments(argparse.Namespace):
    provider: str
    scenario: str
    output: Path
    allow_billable_provider_run: bool
    approval_reference: str


def build_parser() -> argparse.ArgumentParser:
    """Build the narrow, explicitly approved live-evaluation CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "Run a non-gating workspace-showcase evaluation. Provider calls may be billable."
        ),
        epilog=("Results are non-deterministic, provider- and machine-specific, and non-gating."),
    )
    parser.add_argument(
        "--provider",
        choices=[provider.value for provider in ProviderKind],
        required=True,
        help="live provider to call; provider calls may be billable",
    )
    parser.add_argument(
        "--scenario",
        choices=["document-review", "multi-agent-handoff"],
        required=True,
        help="versioned non-gating evaluation scenario",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new directory for record.json and REPORT.md",
    )
    parser.add_argument(
        "--allow-billable-provider-run",
        action="store_true",
        help="required acknowledgement that live provider calls may be billable",
    )
    parser.add_argument(
        "--approval-reference",
        required=True,
        help="non-empty issue or pull-request approval reference",
    )
    return parser


async def async_main(
    argv: Sequence[str] | None = None,
    *,
    provider_loader: ProviderLoader = load_live_provider,
    scenario_resolver: ScenarioResolver = get_scenario,
    service_bundle_factory: ServiceBundleFactory = create_sample_service_bundle,
    scenario_executor: ScenarioExecutor = execute_scenario,
    performance_clock: PerformanceClock | None = None,
    cost_source: ProviderCostSource | None = None,
    metadata_snapshot: MetadataSnapshot | None = None,
) -> WorkspaceShowcaseRecord:
    """Parse CLI arguments and execute through the same approval-gated API."""
    args = build_parser().parse_args(argv, namespace=_Arguments())
    return await run_workspace_showcase(
        provider=args.provider,
        scenario=args.scenario,
        output=args.output,
        allow_billable_provider_run=args.allow_billable_provider_run,
        approval_reference=args.approval_reference,
        provider_loader=provider_loader,
        scenario_resolver=scenario_resolver,
        service_bundle_factory=service_bundle_factory,
        scenario_executor=scenario_executor,
        performance_clock=performance_clock,
        cost_source=cost_source,
        metadata_snapshot=metadata_snapshot,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command-line evaluation."""
    asyncio.run(async_main(argv))


if __name__ == "__main__":
    main()
