"""Concrete no-secret broker."""

from mem_sandbox.secrets.errors import SecretDenied
from mem_sandbox.secrets.models import SecretAccessRequest, SecretLease


class NoSecretBroker:
    """Reject every lease request explicitly."""

    __slots__ = ()

    async def lease(self, request: SecretAccessRequest) -> SecretLease:
        raise SecretDenied(f"secret access is unsupported for reference {request.secret_ref.name}")
