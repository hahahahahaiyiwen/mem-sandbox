"""Environment-backed configuration for the official OpenAI provider sample."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_API_KEY_ENV = "OPENAI_API_KEY"
_MODEL_ENV = "OPENAI_MODEL"


@dataclass(frozen=True, slots=True)
class OpenAISettings:
    """Validated official OpenAI settings with a non-revealing credential field."""

    api_key: str
    model: str

    def __repr__(self) -> str:
        return f"OpenAISettings(api_key=<hidden>, model={self.model!r})"

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> OpenAISettings:
        """Load required settings from an injected or process environment."""
        selected = os.environ if environment is None else environment
        return cls(
            api_key=_required(selected, _API_KEY_ENV),
            model=_required(selected, _MODEL_ENV),
        )


def _required(environment: Mapping[str, str], variable: str) -> str:
    value = environment.get(variable)
    if value is None or not value.strip():
        raise ValueError(f"{variable} must be set to a non-empty value")
    return value.strip()
