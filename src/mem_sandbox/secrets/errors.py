"""Stable secret broker failures."""

from mem_sandbox.core import PolicyDeniedError


class SecretDenied(PolicyDeniedError):
    """Secret access is unsupported or explicitly denied."""

    code = "secret_denied"


SecretDeniedError = SecretDenied
