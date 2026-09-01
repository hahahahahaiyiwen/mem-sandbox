"""Secret-reference contracts without secret material persistence."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from mem_sandbox.core import OperationId, SessionId

_SECRET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class SecretRef:
    name: str

    def __post_init__(self) -> None:
        name = cast(object, self.name)
        if not isinstance(name, str):
            raise TypeError("secret reference name must be a string")
        if _SECRET_NAME.fullmatch(name) is None:
            raise ValueError("secret reference name is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class SecretValue:
    _value: str

    def reveal(self) -> str:
        return self._value


@dataclass(frozen=True, slots=True)
class SecretAccessRequest:
    session_id: SessionId
    operation_id: OperationId
    secret_ref: SecretRef
    command_name: str | None
    max_lease_seconds: float

    def __post_init__(self) -> None:
        value = cast(object, self.max_lease_seconds)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("max_lease_seconds must be positive and finite")


class SecretLease(Protocol):
    @property
    def value(self) -> SecretValue: ...

    @property
    def expires_at(self) -> datetime: ...

    async def close(self) -> None: ...
