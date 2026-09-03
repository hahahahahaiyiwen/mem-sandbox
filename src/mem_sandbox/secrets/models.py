"""Secret-reference contracts without secret material persistence."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from mem_sandbox.core import OperationId, SessionId
from mem_sandbox.secrets.errors import SecretReferenceInvalid

_SECRET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class SecretRef:
    name: str

    def __post_init__(self) -> None:
        name = cast(object, self.name)
        if not isinstance(name, str):
            raise SecretReferenceInvalid("secret reference name must be a string")
        if _SECRET_NAME.fullmatch(name) is None:
            raise SecretReferenceInvalid("secret reference name is invalid")


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class SecretValue:
    _value: str

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self._value), str):
            raise TypeError("secret value must be a string")

    def reveal_text(self) -> str:
        return self._value

    def reveal_bytes(self) -> bytes:
        return self._value.encode("utf-8")


@dataclass(frozen=True, slots=True)
class SecretAccessRequest:
    session_id: SessionId
    operation_id: OperationId
    secret_ref: SecretRef
    command_name: str | None
    max_lease_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.session_id), SessionId):
            raise TypeError("session_id must be a SessionId")
        if not isinstance(cast(object, self.operation_id), OperationId):
            raise TypeError("operation_id must be an OperationId")
        if not isinstance(cast(object, self.secret_ref), SecretRef):
            raise TypeError("secret_ref must be a SecretRef")
        command_name = cast(object, self.command_name)
        if command_name is not None and not isinstance(command_name, str):
            raise TypeError("command_name must be a string or None")
        value = cast(object, self.max_lease_seconds)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError("max_lease_seconds must be numeric")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("max_lease_seconds must be positive and finite")


@dataclass(frozen=True, slots=True, kw_only=True)
class SecretBrokerLimits:
    max_active_leases: int
    max_lease_seconds: float
    max_value_bytes: int

    def __post_init__(self) -> None:
        for name in ("max_active_leases", "max_value_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        duration = cast(object, self.max_lease_seconds)
        if isinstance(duration, bool) or not isinstance(duration, int | float):
            raise TypeError("max_lease_seconds must be numeric")
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("max_lease_seconds must be positive and finite")


class SecretSource(Protocol):
    async def resolve(self, secret_ref: SecretRef) -> SecretValue: ...


class SecretLease(Protocol):
    @property
    def value(self) -> SecretValue: ...

    @property
    def expires_at(self) -> datetime: ...

    async def close(self) -> None: ...


class SecretBroker(Protocol):
    async def lease(self, request: SecretAccessRequest) -> SecretLease: ...
