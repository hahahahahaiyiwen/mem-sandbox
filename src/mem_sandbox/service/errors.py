"""Stable sandbox service lifecycle failures."""

from mem_sandbox.core import (
    ConflictError,
    InternalSandboxError,
    InvalidRequestError,
    NotFoundError,
)


class InvalidSandboxRequest(InvalidRequestError):
    code = "invalid_sandbox_request"


class SandboxNotFound(NotFoundError):
    code = "sandbox_not_found"


class SandboxIdentifierConflict(ConflictError):
    code = "sandbox_identifier_conflict"


class SandboxServiceClosed(ConflictError):
    code = "sandbox_service_closed"


class SandboxStartupFailed(InternalSandboxError):
    code = "sandbox_startup_failed"


class SandboxResumeFailed(InternalSandboxError):
    code = "sandbox_resume_failed"


class SandboxDeleteFailed(InternalSandboxError):
    code = "sandbox_delete_failed"


class SessionFactoryFailed(InternalSandboxError):
    code = "session_factory_failed"


class SessionFactoryCleanupFailed(InternalSandboxError):
    code = "session_factory_cleanup_failed"
