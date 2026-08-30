"""Shared domain foundations for MemSandbox core modules."""

from mem_sandbox.core.clock import Clock, SystemClock
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
from mem_sandbox.core.identifiers import (
    OperationId,
    Revision,
    SessionId,
    SnapshotId,
    SystemUuidGenerator,
    UuidGenerator,
)
from mem_sandbox.core.models import OperationRequestMetadata, OperationResultMetadata

__all__ = [
    "Clock",
    "ConflictError",
    "ErrorCategory",
    "InternalSandboxError",
    "InvalidRequestError",
    "NotFoundError",
    "OperationCancelledError",
    "OperationId",
    "OperationRequestMetadata",
    "OperationResultMetadata",
    "OperationTimeoutError",
    "PolicyDeniedError",
    "QuotaExceededError",
    "Revision",
    "SandboxError",
    "SessionId",
    "SnapshotId",
    "SystemClock",
    "SystemUuidGenerator",
    "UnsupportedOperationError",
    "UuidGenerator",
]
