"""Live Azure OpenAI entry point for shared MemSandbox scenarios."""

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
from samples.openai_agents_sdk.providers.azure_openai.config import AzureOpenAISettings
from samples.openai_agents_sdk.providers.azure_openai.model import create_azure_model


def build_parser() -> ArgumentParser:
    """Create the Azure OpenAI sample command-line interface."""
    return build_sample_parser(description="Run an Azure OpenAI agent task inside MemSandbox.")


async def main(argv: Sequence[str] | None = None) -> None:
    """Run one configured scenario and release all application-owned resources."""

    def create_model() -> tuple[AsyncModelClient, Model]:
        return create_azure_model(AzureOpenAISettings.from_environment())

    await run_provider_sample(
        argv=argv,
        parser=build_parser(),
        model_factory=create_model,
        client_name="Azure client",
    )


if __name__ == "__main__":
    asyncio.run(main())
