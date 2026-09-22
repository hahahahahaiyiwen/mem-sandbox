"""Stable controlled-HTTP domain failures."""

from mem_sandbox.core import (
    InternalSandboxError,
    InvalidRequestError,
    OperationCancelledError,
    OperationTimeoutError,
    PolicyDeniedError,
    QuotaExceededError,
    UnsupportedOperationError,
)


class OutboundHttpRequestInvalid(InvalidRequestError):
    """The HTTP request contract is malformed."""

    code = "outbound_http_request_invalid"


class OutboundHttpUnavailable(UnsupportedOperationError):
    """The sandbox has no host-selected outbound HTTP capability."""

    code = "outbound_http_unavailable"


class OutboundHttpDenied(PolicyDeniedError):
    """The effective outbound HTTP grant denies the request."""

    code = "outbound_http_denied"


class OutboundHttpLimitExceeded(QuotaExceededError):
    """A request, response, or usage value exceeds its immutable limit."""

    code = "outbound_http_limit_exceeded"


class OutboundHttpTimeout(OperationTimeoutError):
    """The gateway exceeded its effective HTTP deadline."""

    code = "outbound_http_timeout"


class OutboundHttpCancelled(OperationCancelledError):
    """The HTTP operation was cancelled before completion."""

    code = "outbound_http_cancelled"


class OutboundHttpGatewayFailed(InternalSandboxError):
    """The gateway could not return a stable bounded outcome."""

    code = "outbound_http_gateway_failed"
