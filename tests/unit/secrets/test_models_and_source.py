from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest

from mem_sandbox.core import ErrorCategory, OperationId, SessionId
from mem_sandbox.secrets import (
    MappingSecretSource,
    SecretAccessRequest,
    SecretBrokerLimits,
    SecretNotFound,
    SecretRef,
    SecretReferenceInvalid,
    SecretValue,
)

SESSION_ID = SessionId(UUID("12345678-1234-5678-1234-567812345678"))
OPERATION_ID = OperationId(UUID("87654321-4321-8765-4321-876543218765"))
CANARY = "secret-\N{LOCK}-canary"


def test_secret_reference_is_bounded_and_uses_a_stable_invalid_request_error() -> None:
    assert SecretRef("a" * 128).name == "a" * 128

    for value in ("", "-leading", "contains space", "a" * 129, 123):
        with pytest.raises(SecretReferenceInvalid) as raised:
            SecretRef(value)  # type: ignore[arg-type]
        assert raised.value.category is ErrorCategory.INVALID_REQUEST
        assert CANARY not in str(raised.value)


def test_secret_value_has_explicit_exact_reveals_and_non_revealing_representations() -> None:
    value = SecretValue(CANARY)

    assert value.reveal_text() == CANARY
    assert value.reveal_bytes() == CANARY.encode("utf-8")
    assert CANARY not in repr(value)
    assert CANARY not in str(value)
    with pytest.raises(FrozenInstanceError):
        value._value = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError, match="string"):
        SecretValue(123)  # type: ignore[arg-type]


def test_secret_access_request_and_broker_limits_validate_exact_types_and_bounds() -> None:
    request = SecretAccessRequest(
        session_id=SESSION_ID,
        operation_id=OPERATION_ID,
        secret_ref=SecretRef("service-token"),
        command_name=None,
        max_lease_seconds=1.0,
    )

    assert replace(request, max_lease_seconds=2).max_lease_seconds == 2
    for value in (True, 0, -1, float("inf"), float("nan"), "1"):
        with pytest.raises((TypeError, ValueError)):
            replace(request, max_lease_seconds=value)  # type: ignore[arg-type]

    assert SecretBrokerLimits(
        max_active_leases=1,
        max_lease_seconds=1,
        max_value_bytes=1,
    )
    for field in ("max_active_leases", "max_value_bytes"):
        with pytest.raises((TypeError, ValueError)):
            replace(
                SecretBrokerLimits(
                    max_active_leases=1,
                    max_lease_seconds=1,
                    max_value_bytes=1,
                ),
                **{field: 0},
            )
    with pytest.raises(ValueError, match="positive"):
        SecretBrokerLimits(
            max_active_leases=1,
            max_lease_seconds=0,
            max_value_bytes=1,
        )


@pytest.mark.asyncio
async def test_mapping_source_returns_independent_typed_values_and_reports_missing_refs() -> None:
    secret_ref = SecretRef("service-token")
    value = SecretValue(CANARY)
    source_values = {secret_ref: value}
    source = MappingSecretSource(source_values)
    source_values.clear()

    resolved = await source.resolve(secret_ref)

    assert resolved is value
    assert CANARY not in repr(source)
    with pytest.raises(SecretNotFound) as raised:
        await source.resolve(SecretRef("missing-token"))
    assert raised.value.category is ErrorCategory.NOT_FOUND
    assert "missing-token" in str(raised.value)
    assert CANARY not in str(raised.value)
