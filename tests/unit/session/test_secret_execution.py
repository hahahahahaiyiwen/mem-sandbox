from __future__ import annotations

from dataclasses import replace

import pytest

from mem_sandbox.command_executor import CommandLimits
from mem_sandbox.secrets import SecretRef
from mem_sandbox.session import SessionExecuteRequest, SessionSecretEnvironmentBinding


def test_secret_environment_bindings_are_validated_bounded_and_normalized() -> None:
    shared = SecretRef("shared-token")
    request = SessionExecuteRequest(
        command="env",
        command_limits=CommandLimits(max_secret_bindings=2),
        secret_environment=(
            SessionSecretEnvironmentBinding("TOKEN_B", shared),
            SessionSecretEnvironmentBinding("TOKEN_A", shared),
        ),
    )

    assert tuple(binding.name for binding in request.secret_environment) == (
        "TOKEN_A",
        "TOKEN_B",
    )
    with pytest.raises(ValueError, match="duplicate"):
        replace(
            request,
            secret_environment=(
                SessionSecretEnvironmentBinding("TOKEN_A", shared),
                SessionSecretEnvironmentBinding("TOKEN_A", SecretRef("other-token")),
            ),
        )
    with pytest.raises(ValueError, match="PWD"):
        SessionSecretEnvironmentBinding("PWD", shared)
    with pytest.raises(ValueError, match="max_secret_bindings"):
        replace(
            request,
            command_limits=CommandLimits(max_secret_bindings=1),
        )
    with pytest.raises(TypeError, match="tuple"):
        replace(request, secret_environment=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SessionSecretEnvironmentBinding"):
        replace(request, secret_environment=(object(),))  # type: ignore[arg-type]
