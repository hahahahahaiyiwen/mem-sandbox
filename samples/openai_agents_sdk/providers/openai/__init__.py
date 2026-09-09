"""Official OpenAI provider adapter for OpenAI Agents SDK scenarios."""

from samples.openai_agents_sdk.providers.openai.config import OpenAISettings
from samples.openai_agents_sdk.providers.openai.model import create_openai_model

__all__ = [
    "OpenAISettings",
    "create_openai_model",
]
