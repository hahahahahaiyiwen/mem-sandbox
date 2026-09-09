"""Environment-backed configuration for the Azure OpenAI provider sample."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

_ENDPOINT_ENV = "AZURE_OPENAI_ENDPOINT"
_API_KEY_ENV = "AZURE_OPENAI_API_KEY"
_API_VERSION_ENV = "AZURE_OPENAI_API_VERSION"
_DEPLOYMENT_ENV = "AZURE_OPENAI_DEPLOYMENT"
_PUBLIC_AZURE_OPENAI_SUFFIXES = (
    ".openai.azure.com",
    ".cognitiveservices.azure.com",
)


@dataclass(frozen=True, slots=True)
class AzureOpenAISettings:
    """Validated live-provider settings with a non-revealing credential field."""

    endpoint: str
    api_key: str
    api_version: str
    deployment: str

    def __repr__(self) -> str:
        return (
            "AzureOpenAISettings("
            f"endpoint={self.endpoint!r}, "
            "api_key=<hidden>, "
            f"api_version={self.api_version!r}, "
            f"deployment={self.deployment!r})"
        )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> AzureOpenAISettings:
        """Load the four required settings from an injected or process environment."""
        selected = os.environ if environment is None else environment
        endpoint = _required(selected, _ENDPOINT_ENV).rstrip("/")
        _validate_endpoint(endpoint)
        return cls(
            endpoint=endpoint,
            api_key=_required(selected, _API_KEY_ENV),
            api_version=_required(selected, _API_VERSION_ENV),
            deployment=_required(selected, _DEPLOYMENT_ENV),
        )


def _required(environment: Mapping[str, str], variable: str) -> str:
    value = environment.get(variable)
    if value is None or not value.strip():
        raise ValueError(f"{variable} must be set to a non-empty value")
    return value.strip()


def _validate_endpoint(endpoint: str) -> None:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise ValueError(
            f"{_ENDPOINT_ENV} must be a public Azure OpenAI resource endpoint"
        ) from error

    hostname = (parsed.hostname or "").lower()
    suffix = next(
        (candidate for candidate in _PUBLIC_AZURE_OPENAI_SUFFIXES if hostname.endswith(candidate)),
        None,
    )
    resource_name = "" if suffix is None else hostname.removesuffix(suffix)
    if (
        parsed.scheme.lower() != "https"
        or suffix is None
        or not resource_name
        or "." in resource_name
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{_ENDPOINT_ENV} must be a public Azure OpenAI resource endpoint "
            "ending in .openai.azure.com or .cognitiveservices.azure.com"
        )
