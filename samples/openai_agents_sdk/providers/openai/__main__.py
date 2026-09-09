"""Live official OpenAI entry point for shared MemSandbox scenarios."""

from __future__ import annotations

import asyncio
from argparse import ArgumentParser
from collections.abc import Sequence

from agents.models.interface import Model

from samples.openai_agents_sdk.application import (
    AsyncModelClient,
    build_sample_parser,
    run_provider_sample,
)
from samples.openai_agents_sdk.providers.openai.config import OpenAISettings
from samples.openai_agents_sdk.providers.openai.model import create_openai_model


def build_parser() -> ArgumentParser:
    """Create the official OpenAI sample command-line interface."""
    return build_sample_parser(
        description="Run an official OpenAI model agent task inside MemSandbox."
    )


async def main(argv: Sequence[str] | None = None) -> None:
    """Run one configured scenario and release all application-owned resources."""

    def create_model() -> tuple[AsyncModelClient, Model]:
        return create_openai_model(OpenAISettings.from_environment())

    await run_provider_sample(
        argv=argv,
        parser=build_parser(),
        model_factory=create_model,
        client_name="OpenAI client",
    )


if __name__ == "__main__":
    asyncio.run(main())
