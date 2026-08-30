"""Immutable identifiers and UUID generation boundary."""

from dataclasses import dataclass
from typing import Protocol, Self
from uuid import UUID, uuid4


class UuidGenerator(Protocol):
    """Generates UUID values for a consuming domain module."""

    def new_uuid(self) -> UUID:
        """Return a new non-nil UUID."""
        ...


class SystemUuidGenerator:
    """Production UUID generator."""

    __slots__ = ()

    def new_uuid(self) -> UUID:
        """Return a random UUID."""
        return uuid4()


@dataclass(frozen=True, slots=True)
class _UuidIdentifier:
    value: UUID

    def __post_init__(self) -> None:
        if _require_uuid(self.value).int == 0:
            raise ValueError("identifier value must not be the nil UUID")

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse a canonical or non-canonical UUID string."""
        try:
            parsed = UUID(_require_text(value))
        except ValueError as error:
            raise ValueError("identifier text must be a valid UUID") from error
        return cls(parsed)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class SessionId(_UuidIdentifier):
    """Identifies one sandbox session."""


@dataclass(frozen=True, slots=True)
class OperationId(_UuidIdentifier):
    """Identifies one requested sandbox operation."""


@dataclass(frozen=True, slots=True)
class SnapshotId(_UuidIdentifier):
    """Identifies one persisted sandbox snapshot."""


@dataclass(frozen=True, slots=True, order=True)
class Revision:
    """Monotonic workspace revision."""

    value: int

    def __post_init__(self) -> None:
        if _require_integer(self.value) < 0:
            raise ValueError("revision value must be non-negative")

    @classmethod
    def initial(cls) -> Self:
        """Return the initial workspace revision."""
        return cls(0)

    def next(self) -> Self:
        """Return the following revision without mutating this value."""
        return type(self)(self.value + 1)

    def __str__(self) -> str:
        return str(self.value)


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError("identifier value must be a UUID")
    return value


def _require_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("identifier text must be a string")
    return value


def _require_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("revision value must be an integer")
    return value
