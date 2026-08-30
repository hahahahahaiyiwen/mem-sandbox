from __future__ import annotations

from collections.abc import Sequence

import pytest

from mem_sandbox.core.errors import (
    ConflictError,
    ErrorCategory,
    InternalSandboxError,
    InvalidRequestError,
    NotFoundError,
    OperationCancelledError,
    OperationTimeoutError,
    PolicyDeniedError,
    QuotaExceededError,
    SandboxError,
    UnsupportedOperationError,
)

ERROR_CASES: Sequence[tuple[type[SandboxError], ErrorCategory, str]] = (
    (InvalidRequestError, ErrorCategory.INVALID_REQUEST, "invalid_request"),
    (NotFoundError, ErrorCategory.NOT_FOUND, "not_found"),
    (ConflictError, ErrorCategory.CONFLICT, "conflict"),
    (QuotaExceededError, ErrorCategory.QUOTA_EXCEEDED, "quota_exceeded"),
    (PolicyDeniedError, ErrorCategory.POLICY_DENIED, "policy_denied"),
    (OperationTimeoutError, ErrorCategory.TIMEOUT, "operation_timeout"),
    (OperationCancelledError, ErrorCategory.CANCELLED, "operation_cancelled"),
    (UnsupportedOperationError, ErrorCategory.UNSUPPORTED, "unsupported_operation"),
    (InternalSandboxError, ErrorCategory.INTERNAL, "internal_error"),
)


@pytest.mark.parametrize(("error_type", "category", "code"), ERROR_CASES)
def test_error_categories_and_codes_are_stable(
    error_type: type[SandboxError],
    category: ErrorCategory,
    code: str,
) -> None:
    error = error_type("descriptive message")

    assert error.category is category
    assert error.code == code
    assert str(error) == "descriptive message"


def test_error_requires_a_non_empty_message() -> None:
    with pytest.raises(ValueError, match="message"):
        InvalidRequestError("")
