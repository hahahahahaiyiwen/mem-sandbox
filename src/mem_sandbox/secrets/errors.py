"""Stable secret-boundary failures."""

from mem_sandbox.core import (
    ConflictError,
    InternalSandboxError,
    InvalidRequestError,
    NotFoundError,
    OperationTimeoutError,
    PolicyDeniedError,
    QuotaExceededError,
)


class SecretReferenceInvalid(InvalidRequestError):
    """A secret reference is not a valid bounded application name."""

    code = "secret_reference_invalid"


class SecretDenied(PolicyDeniedError):
    """Secret access is unsupported or explicitly denied."""

    code = "secret_denied"


class SecretNotFound(NotFoundError):
    """The approved source does not contain the requested reference."""

    code = "secret_not_found"


class SecretExpired(OperationTimeoutError):
    """A lease can no longer expose its protected value."""

    code = "secret_expired"


class SecretLeaseLimitExceeded(QuotaExceededError):
    """The broker has no active-lease capacity."""

    code = "secret_lease_limit_exceeded"


class SecretSourceUnavailable(InternalSandboxError):
    """The configured source could not resolve a reference."""

    code = "secret_source_unavailable"


class SecretSourceValueInvalid(InternalSandboxError):
    """The source returned material outside the broker value bounds."""

    code = "secret_source_value_invalid"


class SecretLeaseClosed(ConflictError):
    """A closed lease cannot expose its protected value."""

    code = "secret_lease_closed"


class SecretLeaseCleanupFailed(InternalSandboxError):
    """A lease could not complete its cleanup contract."""

    code = "secret_lease_cleanup_failed"


SecretReferenceInvalidError = SecretReferenceInvalid
SecretDeniedError = SecretDenied
SecretNotFoundError = SecretNotFound
SecretExpiredError = SecretExpired
SecretLeaseLimitExceededError = SecretLeaseLimitExceeded
SecretSourceUnavailableError = SecretSourceUnavailable
SecretSourceValueInvalidError = SecretSourceValueInvalid
SecretLeaseClosedError = SecretLeaseClosed
SecretLeaseCleanupFailedError = SecretLeaseCleanupFailed
