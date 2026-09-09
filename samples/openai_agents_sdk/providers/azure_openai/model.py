"""Azure OpenAI model construction for the provider-specific sample."""

from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncAzureOpenAI

from samples.openai_agents_sdk.providers.azure_openai.config import AzureOpenAISettings


def create_azure_model(
    settings: AzureOpenAISettings,
) -> tuple[AsyncAzureOpenAI, OpenAIChatCompletionsModel]:
    """Construct the live Azure client and Agents SDK Chat Completions model."""
    client = AsyncAzureOpenAI(
        azure_endpoint=settings.endpoint,
        api_version=settings.api_version,
        api_key=settings.api_key,
    )
    model = OpenAIChatCompletionsModel(
        model=settings.deployment,
        openai_client=client,
        strict_feature_validation=True,
    )
    return client, model
