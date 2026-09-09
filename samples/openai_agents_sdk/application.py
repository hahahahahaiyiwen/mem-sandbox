"""Shared application lifecycle for provider-backed OpenAI Agents samples."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Protocol

from agents.models.interface import Model

from mem_sandbox.session import SandboxSession
from samples.openai_agents_sdk.runner import InspectionContext, ScenarioResult, run_scenario
from samples.openai_agents_sdk.scenarios import get_scenario, list_scenarios
from samples.shared.cli import run_inspection_cli
from samples.shared.service import create_sample_service_bundle


class AsyncModelClient(Protocol):
    """Minimum provider-client lifetime required by the sample application."""

    async def close(self) -> None:
        """Release provider client resources."""
        ...


type ModelFactory = Callable[[], tuple[AsyncModelClient, Model]]


def build_sample_parser(*, description: str) -> argparse.ArgumentParser:
    """Create the common command-line interface for one provider entry point."""
    parser = argparse.ArgumentParser(description=description)
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
        help="list registered scenarios without requiring provider configuration",
    )
    return parser


async def run_provider_sample(
    *,
    argv: Sequence[str] | None,
    parser: argparse.ArgumentParser,
    model_factory: ModelFactory,
    client_name: str,
) -> None:
    """Run one provider model through the shared scenario and application lifecycle."""
    args = parser.parse_args(argv)
    if args.list_scenarios:
        for scenario in list_scenarios():
            print(f"{scenario.name}: {scenario.description}")
        return

    scenario = get_scenario(args.scenario)
    model_client: AsyncModelClient | None = None
    service = None
    try:
        model_client, model = model_factory()
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
        if model_client is not None:
            try:
                await model_client.close()
            except BaseException as error:
                cleanup_errors.append((f"{client_name} close", error))
        _surface_cleanup_errors(primary, cleanup_errors)


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


def _surface_cleanup_errors(
    primary: BaseException | None,
    cleanup_errors: list[tuple[str, BaseException]],
) -> None:
    if not cleanup_errors:
        return
    if primary is not None:
        for operation, error in cleanup_errors:
            primary.add_note(f"secondary {operation} failure: {error}")
        return

    operation, failure = cleanup_errors[0]
    failure.add_note(f"{operation} failed during sample cleanup")
    for secondary_operation, secondary in cleanup_errors[1:]:
        failure.add_note(f"secondary {secondary_operation} failure: {secondary}")
    raise failure
