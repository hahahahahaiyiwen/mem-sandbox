"""Stable domain error categories shared by core boundaries."""

from enum import StrEnum
from typing import ClassVar


class ErrorCategory(StrEnum):
    """Framework-neutral categories used when adapters translate failures."""

    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    QUOTA_EXCEEDED = "quota_exceeded"
    POLICY_DENIED = "policy_denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    INTERNAL = "internal"


class SandboxError(Exception):
    """Base class for expected MemSandbox domain failures."""

    category: ClassVar[ErrorCategory]
    code: ClassVar[str]

    def __init__(self, message: str) -> None:
        super().__init__(_require_message(message))


def _require_message(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("error message must be a string")
    if not value.strip():
        raise ValueError("error message must not be empty")
    return value


class InvalidRequestError(SandboxError):
    """The caller supplied an invalid request."""

    category = ErrorCategory.INVALID_REQUEST
    code = "invalid_request"


class NotFoundError(SandboxError):
    """A requested domain resource does not exist."""

    category = ErrorCategory.NOT_FOUND
    code = "not_found"


class ConflictError(SandboxError):
    """The request conflicts with current sandbox state."""

    category = ErrorCategory.CONFLICT
    code = "conflict"


class QuotaExceededError(SandboxError):
    """A bounded resource limit would be exceeded."""

    category = ErrorCategory.QUOTA_EXCEEDED
    code = "quota_exceeded"


class PolicyDeniedError(SandboxError):
    """Policy explicitly denied the requested operation."""

    category = ErrorCategory.POLICY_DENIED
    code = "policy_denied"


class OperationTimeoutError(SandboxError):
    """The operation exceeded its effective timeout."""

    category = ErrorCategory.TIMEOUT
    code = "operation_timeout"


class OperationCancelledError(SandboxError):
    """The operation was cancelled before completion."""

    category = ErrorCategory.CANCELLED
    code = "operation_cancelled"


class UnsupportedOperationError(SandboxError):
    """The requested behavior is not implemented by this backend."""

    category = ErrorCategory.UNSUPPORTED
    code = "unsupported_operation"


class InternalSandboxError(SandboxError):
    """An unexpected internal failure prevented completion."""

    category = ErrorCategory.INTERNAL
    code = "internal_error"
