"""Azure OpenAI provider adapter for OpenAI Agents SDK scenarios."""

from samples.openai_agents_sdk.providers.azure_openai.config import AzureOpenAISettings
from samples.openai_agents_sdk.providers.azure_openai.model import create_azure_model

__all__ = [
    "AzureOpenAISettings",
    "create_azure_model",
]
