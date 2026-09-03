"""Deterministic application-supplied secret sources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from mem_sandbox.secrets.errors import SecretNotFound
from mem_sandbox.secrets.models import SecretRef, SecretValue


class MappingSecretSource:
    """Resolve references from an immutable copy of an application mapping."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[SecretRef, SecretValue]) -> None:
        values_object = cast(object, values)
        if not isinstance(values_object, Mapping):
            raise TypeError("values must be a mapping")
        copied: dict[SecretRef, SecretValue] = {}
        for secret_ref, value in values.items():
            if not isinstance(cast(object, secret_ref), SecretRef):
                raise TypeError("secret source keys must be SecretRef values")
            if not isinstance(cast(object, value), SecretValue):
                raise TypeError("secret source values must be SecretValue values")
            copied[secret_ref] = value
        self._values = copied

    async def resolve(self, secret_ref: SecretRef) -> SecretValue:
        if not isinstance(cast(object, secret_ref), SecretRef):
            raise TypeError("secret_ref must be a SecretRef")
        value = self._values.get(secret_ref)
        if value is None:
            raise SecretNotFound(f"secret reference {secret_ref.name} does not exist")
        return value
