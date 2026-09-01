"""Stable SandboxSession failures."""

from __future__ import annotations

from mem_sandbox.core import (
    ConflictError,
    InternalSandboxError,
    InvalidRequestError,
    OperationCancelledError,
    OperationId,
    OperationTimeoutError,
    PolicyDeniedError,
)


class _OperationIdentity:
    operation_id: OperationId | None

    def __init__(self, message: str, *, operation_id: OperationId | None = None) -> None:
        super().__init__(message)  # type: ignore[misc]
        self.operation_id = operation_id


class SessionRequestInvalid(_OperationIdentity, InvalidRequestError):
    code = "session_request_invalid"


class SessionStartInvalid(ConflictError):
    code = "session_start_invalid"


class SessionNotRunning(ConflictError):
    code = "session_not_running"


class SessionClosing(ConflictError):
    code = "session_closing"


class SessionClosed(ConflictError):
    code = "session_closed"


class SessionFailed(_OperationIdentity, InternalSandboxError):
    code = "session_failed"


class SessionOperationTimeout(_OperationIdentity, OperationTimeoutError):
    code = "session_operation_timeout"


class SessionOperationCancelled(_OperationIdentity, OperationCancelledError):
    code = "session_operation_cancelled"


class SessionCloseTimeout(OperationTimeoutError):
    code = "session_close_timeout"


class SessionPolicyDenied(_OperationIdentity, PolicyDeniedError):
    code = "session_policy_denied"

    def __init__(self, message: str, *, reason_code: str, operation_id: OperationId) -> None:
        super().__init__(message, operation_id=operation_id)
        self.reason_code = reason_code


class SessionEventDeliveryFailed(_OperationIdentity, InternalSandboxError):
    code = "session_event_delivery_failed"


class SessionSnapshotRestoreFailed(_OperationIdentity, InternalSandboxError):
    code = "session_snapshot_restore_failed"


class SessionCleanupFailed(InternalSandboxError):
    code = "session_cleanup_failed"


SessionRequestInvalidError = SessionRequestInvalid
SessionStartInvalidError = SessionStartInvalid
SessionNotRunningError = SessionNotRunning
SessionClosingError = SessionClosing
SessionClosedError = SessionClosed
SessionFailedError = SessionFailed
SessionOperationTimeoutError = SessionOperationTimeout
SessionOperationCancelledError = SessionOperationCancelled
SessionCloseTimeoutError = SessionCloseTimeout
SessionPolicyDeniedError = SessionPolicyDenied
SessionEventDeliveryFailedError = SessionEventDeliveryFailed
SessionSnapshotRestoreFailedError = SessionSnapshotRestoreFailed
SessionCleanupFailedError = SessionCleanupFailed
