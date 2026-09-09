"""Live Azure OpenAI entry point for registered MemSandbox scenarios."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from openai import AsyncAzureOpenAI

from mem_sandbox.session import SandboxSession
from samples.azure_openai_agent.app import (
    InspectionContext,
    ScenarioResult,
    create_azure_model,
    run_scenario,
)
from samples.azure_openai_agent.cli import run_inspection_cli
from samples.azure_openai_agent.config import AzureOpenAISettings
from samples.azure_openai_agent.scenarios import get_scenario, list_scenarios
from samples.azure_openai_agent.service import create_sample_service_bundle


def build_parser() -> argparse.ArgumentParser:
    """Create the stable sample command-line interface."""
    parser = argparse.ArgumentParser(
        description="Run an Azure OpenAI agent task inside MemSandbox."
    )
    parser.add_argument(
        "--scenario",
        default="workspace-edit",
        choices=[scenario.name for scenario in list_scenarios()],
        help="registered agent task to run (default: workspace-edit)",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="open the interactive MemSandbox CLI after successful verification",
    )
    parser.add_argument(
        "--inspect-on-failure",
        action="store_true",
        help="open the interactive MemSandbox CLI before cleaning up a failed scenario",
    )
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="list registered scenarios without requiring Azure configuration",
    )
    return parser


async def main(argv: Sequence[str] | None = None) -> None:
    """Run one configured scenario and release all application-owned resources."""
    args = build_parser().parse_args(argv)
    if args.list_scenarios:
        for scenario in list_scenarios():
            print(f"{scenario.name}: {scenario.description}")
        return

    scenario = get_scenario(args.scenario)
    settings = AzureOpenAISettings.from_environment()
    azure_client: AsyncAzureOpenAI | None = None
    service = None
    try:
        azure_client, model = create_azure_model(settings)
        bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
        service = bundle.service

        async def inspect(
            context: InspectionContext,
            session: SandboxSession,
        ) -> None:
            _print_inspection_context(context)
            await run_inspection_cli(session)

        result = await run_scenario(
            model=model,
            service=service,
            scenario=scenario,
            inspector=inspect if args.inspect or args.inspect_on_failure else None,
            inspect_success=args.inspect,
            inspect_on_failure=args.inspect_on_failure,
            snapshot_store=bundle.snapshot_store,
            clock=bundle.clock,
        )
        if not args.inspect:
            _print_result(result)
    finally:
        primary = sys.exception()
        cleanup_errors: list[tuple[str, BaseException]] = []
        if service is not None:
            try:
                await service.close()
            except BaseException as error:
                cleanup_errors.append(("MemSandbox service close", error))
        if azure_client is not None:
            try:
                await azure_client.close()
            except BaseException as error:
                cleanup_errors.append(("Azure client close", error))
        if primary is not None:
            for operation, error in cleanup_errors:
                primary.add_note(f"secondary {operation} failure: {error}")
        elif cleanup_errors:
            operation, failure = cleanup_errors[0]
            failure.add_note(f"{operation} failed during sample cleanup")
            for secondary_operation, secondary in cleanup_errors[1:]:
                failure.add_note(f"secondary {secondary_operation} failure: {secondary}")
            raise failure


def _print_inspection_context(context: InspectionContext) -> None:
    if context.result is not None:
        _print_result(context.result)
        return
    assert context.error is not None
    print(
        f"Scenario {context.scenario_name!r} failed: {context.error}",
        file=sys.stderr,
    )
    print("Inspecting the live sandbox before cleanup.", file=sys.stderr)


def _print_result(result: ScenarioResult) -> None:
    for index, output in enumerate(result.stage_outputs, start=1):
        print(f"Stage {index}: {output}")
    if result.selected_branch is not None:
        print(f"Selected branch: {result.selected_branch}")
    for artifact in result.artifacts:
        print(f"Verified {artifact.path}:")
        print(artifact.content.decode("utf-8"), end="")


if __name__ == "__main__":
    asyncio.run(main())
