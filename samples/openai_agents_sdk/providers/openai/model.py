"""Official OpenAI Responses model construction for the provider-specific sample."""

from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

from samples.openai_agents_sdk.providers.openai.config import OpenAISettings

_OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"


def create_openai_model(
    settings: OpenAISettings,
) -> tuple[AsyncOpenAI, OpenAIResponsesModel]:
    """Construct the official client and Agents SDK Responses model."""
    client = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=_OFFICIAL_OPENAI_BASE_URL,
    )
    model = OpenAIResponsesModel(
        model=settings.model,
        openai_client=client,
    )
    return client, model
