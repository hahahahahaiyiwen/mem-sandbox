from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from mem_sandbox.core.identifiers import (
    OperationId,
    Revision,
    SessionId,
    SnapshotId,
    SystemUuidGenerator,
    UuidGenerator,
)

UUID_TEXT = "12345678-1234-5678-1234-567812345678"
UUID_VALUE = UUID(UUID_TEXT)


class FixedUuidGenerator:
    def __init__(self, value: UUID) -> None:
        self._value = value

    def new_uuid(self) -> UUID:
        return self._value


def _new_session_id(generator: UuidGenerator) -> SessionId:
    return SessionId(generator.new_uuid())


@pytest.mark.parametrize("identifier_type", (SessionId, OperationId, SnapshotId))
def test_uuid_identifiers_parse_and_format_canonically(
    identifier_type: type[SessionId] | type[OperationId] | type[SnapshotId],
) -> None:
    identifier = identifier_type.parse(UUID_TEXT.upper())

    assert str(identifier) == UUID_TEXT
    assert identifier.value == UUID_VALUE


def test_identifier_types_are_not_interchangeable() -> None:
    assert SessionId(UUID_VALUE) != OperationId(UUID_VALUE)


def test_uuid_identifiers_reject_nil_uuid() -> None:
    with pytest.raises(ValueError, match="nil"):
        SessionId(UUID(int=0))


def test_uuid_identifiers_are_immutable() -> None:
    identifier = SessionId(UUID_VALUE)

    with pytest.raises(FrozenInstanceError):
        setattr(identifier, "value", UUID(int=1))  # noqa: B010


def test_uuid_generator_is_constructor_substitutable() -> None:
    assert _new_session_id(FixedUuidGenerator(UUID_VALUE)) == SessionId(UUID_VALUE)


def test_system_uuid_generator_returns_unique_non_nil_values() -> None:
    generator = SystemUuidGenerator()

    first = generator.new_uuid()
    second = generator.new_uuid()

    assert first.int != 0
    assert second.int != 0
    assert first != second


def test_revision_starts_at_zero_and_advances_immutably() -> None:
    initial = Revision.initial()

    assert initial == Revision(0)
    assert initial.next() == Revision(1)
    assert initial == Revision(0)


def test_revision_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Revision(-1)
