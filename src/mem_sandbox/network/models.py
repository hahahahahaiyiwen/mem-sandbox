"""Immutable framework-neutral controlled-HTTP values."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from mem_sandbox.core import OperationId, SessionId
from mem_sandbox.network.errors import (
    OutboundHttpDenied,
    OutboundHttpLimitExceeded,
    OutboundHttpRequestInvalid,
    OutboundHttpResponseInvalid,
)

if TYPE_CHECKING:
    from mem_sandbox.network.ports import (
        NetworkCancellationSignal,
        OutboundHttpGateway,
    )

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_MAX_URL_BYTES = 8192
_MAX_HEADER_VALUE_BYTES = 64 * 1024


class HttpMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"


class HttpScheme(StrEnum):
    HTTP = "http"
    HTTPS = "https"


class HttpEventDelivery(StrEnum):
    BEST_EFFORT = "best_effort"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class NetworkPolicyId:
    value: str

    def __post_init__(self) -> None:
        _require_identifier("network policy identifier", self.value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CredentialRouteId:
    value: str

    def __post_init__(self) -> None:
        _require_identifier("credential route identifier", self.value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, repr=False)
class HttpHeader:
    name: str
    value: str

    def __post_init__(self) -> None:
        name = cast(object, self.name)
        value = cast(object, self.value)
        if not isinstance(name, str):
            raise OutboundHttpRequestInvalid("HTTP header name is invalid")
        if not isinstance(value, str):
            raise TypeError("HTTP header value must be a string")
        name = str.__str__(name)
        value = str.__str__(value)
        if _HEADER_NAME.fullmatch(name) is None:
            raise OutboundHttpRequestInvalid("HTTP header name is invalid")
        if any(
            (ord(character) < 32 and character != "\t") or ord(character) == 127
            for character in value
        ):
            raise OutboundHttpRequestInvalid("HTTP header value contains a control character")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            size = None
        if size is None:
            raise OutboundHttpRequestInvalid("HTTP header value must be valid UTF-8")
        if size > _MAX_HEADER_VALUE_BYTES:
            raise OutboundHttpLimitExceeded("HTTP header value exceeds its byte limit")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "value", value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, value=<redacted>)"


@dataclass(frozen=True, slots=True, kw_only=True)
class HttpTransferLimits:
    timeout_seconds: float = 10.0
    max_request_header_bytes: int = 16 * 1024
    max_request_body_bytes: int = 0
    max_response_header_bytes: int = 64 * 1024
    max_response_body_bytes: int = 512 * 1024
    max_decompressed_response_bytes: int = 512 * 1024
    max_redirects: int = 3
    max_requests: int = 4
    max_transferred_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        _require_positive_finite("timeout_seconds", self.timeout_seconds)
        for name in (
            "max_request_header_bytes",
            "max_request_body_bytes",
            "max_response_header_bytes",
            "max_response_body_bytes",
            "max_decompressed_response_bytes",
            "max_redirects",
            "max_transferred_bytes",
        ):
            _require_non_negative_integer(name, getattr(self, name))
        _require_positive_integer("max_requests", self.max_requests)
        if self.max_decompressed_response_bytes < self.max_response_body_bytes:
            raise ValueError(
                "max_decompressed_response_bytes must not be less than max_response_body_bytes"
            )

    def is_within(self, ceiling: HttpTransferLimits) -> bool:
        if not isinstance(cast(object, ceiling), HttpTransferLimits):
            raise TypeError("ceiling must be HttpTransferLimits")
        return all(
            getattr(self, name) <= getattr(ceiling, name)
            for name in (
                "timeout_seconds",
                "max_request_header_bytes",
                "max_request_body_bytes",
                "max_response_header_bytes",
                "max_response_body_bytes",
                "max_decompressed_response_bytes",
                "max_redirects",
                "max_requests",
                "max_transferred_bytes",
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboundHttpGrant:
    policy_id: NetworkPolicyId
    methods: tuple[HttpMethod, ...] = (HttpMethod.GET, HttpMethod.HEAD)
    schemes: tuple[HttpScheme, ...] = (HttpScheme.HTTPS,)
    limits: HttpTransferLimits = field(default_factory=HttpTransferLimits)
    credential_routes: tuple[CredentialRouteId, ...] = ()
    event_delivery: HttpEventDelivery = HttpEventDelivery.REQUIRED

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.policy_id), NetworkPolicyId):
            raise TypeError("policy_id must be NetworkPolicyId")
        object.__setattr__(
            self,
            "methods",
            _ordered_enum_tuple("methods", self.methods, HttpMethod),
        )
        object.__setattr__(
            self,
            "schemes",
            _ordered_enum_tuple("schemes", self.schemes, HttpScheme),
        )
        if not self.methods:
            raise ValueError("methods must not be empty")
        if not self.schemes:
            raise ValueError("schemes must not be empty")
        if not isinstance(cast(object, self.limits), HttpTransferLimits):
            raise TypeError("limits must be HttpTransferLimits")
        routes = cast(object, self.credential_routes)
        if not isinstance(routes, tuple):
            raise TypeError("credential_routes must be a tuple")
        route_values = cast(tuple[object, ...], routes)
        if any(not isinstance(route, CredentialRouteId) for route in route_values):
            raise TypeError("credential_routes must contain CredentialRouteId values")
        ordered_routes = tuple(
            sorted(cast(tuple[CredentialRouteId, ...], routes), key=lambda item: item.value)
        )
        if len(set(ordered_routes)) != len(ordered_routes):
            raise ValueError("credential_routes must not contain duplicates")
        object.__setattr__(self, "credential_routes", ordered_routes)
        if not isinstance(cast(object, self.event_delivery), HttpEventDelivery):
            raise TypeError("event_delivery must be HttpEventDelivery")

    def is_within(self, ceiling: OutboundHttpGrant) -> bool:
        if not isinstance(cast(object, ceiling), OutboundHttpGrant):
            raise TypeError("ceiling must be OutboundHttpGrant")
        event_is_within = (
            self.event_delivery is HttpEventDelivery.REQUIRED
            or ceiling.event_delivery is HttpEventDelivery.BEST_EFFORT
        )
        return (
            self.policy_id == ceiling.policy_id
            and set(self.methods).issubset(ceiling.methods)
            and set(self.schemes).issubset(ceiling.schemes)
            and self.limits.is_within(ceiling.limits)
            and set(self.credential_routes).issubset(ceiling.credential_routes)
            and event_is_within
        )

    def require_request(self, request: OutboundHttpRequest) -> None:
        if not isinstance(cast(object, request), OutboundHttpRequest):
            raise TypeError("request must be OutboundHttpRequest")
        if request.method not in self.methods:
            raise OutboundHttpDenied("HTTP method is outside the effective grant")
        if request.scheme not in self.schemes:
            raise OutboundHttpDenied("HTTP scheme is outside the effective grant")
        if (
            request.credential_route is not None
            and request.credential_route not in self.credential_routes
        ):
            raise OutboundHttpDenied("credential route is outside the effective grant")
        if not request.limits.is_within(self.limits):
            raise OutboundHttpLimitExceeded("requested HTTP limits exceed the effective grant")

    def require_response(
        self,
        request: OutboundHttpRequest,
        response: OutboundHttpResponse,
    ) -> None:
        self.require_request(request)
        if not isinstance(cast(object, response), OutboundHttpResponse):
            raise TypeError("response must be OutboundHttpResponse")
        if response.status_code < 200:
            raise OutboundHttpResponseInvalid("outbound HTTP response must be a final response")
        limits = request.limits
        if _headers_size(response.headers) > limits.max_response_header_bytes:
            raise OutboundHttpLimitExceeded("HTTP response headers exceed the requested limit")
        if len(response.body) > limits.max_decompressed_response_bytes:
            raise OutboundHttpLimitExceeded(
                "HTTP decompressed response body exceeds the requested limit"
            )
        usage = response.usage
        if usage.request_count > limits.max_requests:
            raise OutboundHttpLimitExceeded("HTTP request count exceeds the requested limit")
        if usage.response_bytes > limits.max_response_body_bytes:
            raise OutboundHttpLimitExceeded("HTTP response usage exceeds the requested limit")
        if usage.decompressed_response_bytes > limits.max_decompressed_response_bytes:
            raise OutboundHttpLimitExceeded("HTTP decompressed usage exceeds the requested limit")
        if usage.decompressed_response_bytes != len(response.body):
            raise OutboundHttpLimitExceeded(
                "HTTP decompressed usage does not match the published response body"
            )
        if usage.redirect_count > limits.max_redirects:
            raise OutboundHttpLimitExceeded("HTTP redirect count exceeds the requested limit")
        if usage.request_bytes + usage.response_wire_bytes > limits.max_transferred_bytes:
            raise OutboundHttpLimitExceeded("HTTP transferred bytes exceed the requested limit")
        if usage.duration_ms > limits.timeout_seconds * 1000:
            raise OutboundHttpLimitExceeded("HTTP duration exceeds the requested limit")
        if request.method is HttpMethod.HEAD and response.body:
            raise OutboundHttpLimitExceeded("HEAD response contains a body")


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class OutboundHttpRequest:
    method: HttpMethod
    url: str
    limits: HttpTransferLimits
    headers: tuple[HttpHeader, ...] = ()
    body: bytes = b""
    credential_route: CredentialRouteId | None = None
    _scheme: HttpScheme = field(
        init=False,
        default=HttpScheme.HTTPS,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.method), HttpMethod):
            raise TypeError("method must be HttpMethod")
        url = cast(object, self.url)
        if not isinstance(url, str):
            raise TypeError("url must be a string")
        try:
            url_size = len(url.encode("utf-8"))
        except UnicodeEncodeError:
            url_size = None
        if url_size is None:
            raise OutboundHttpRequestInvalid("URL must be valid UTF-8")
        if not url or url_size > _MAX_URL_BYTES or any(ord(char) < 32 for char in url):
            raise OutboundHttpRequestInvalid("URL is empty, contains control data, or is too long")
        split = None
        scheme = None
        try:
            split = urlsplit(url)
            scheme = HttpScheme(split.scheme.lower())
        except (TypeError, ValueError):
            pass
        if split is None or scheme is None:
            raise OutboundHttpRequestInvalid("URL must use an HTTP or HTTPS scheme")
        if not split.netloc:
            raise OutboundHttpRequestInvalid("URL must contain an authority")
        object.__setattr__(self, "_scheme", scheme)
        if not isinstance(cast(object, self.limits), HttpTransferLimits):
            raise TypeError("limits must be HttpTransferLimits")
        headers = cast(object, self.headers)
        if not isinstance(headers, tuple):
            raise TypeError("headers must be a tuple")
        if any(not isinstance(header, HttpHeader) for header in cast(tuple[object, ...], headers)):
            raise TypeError("headers must contain HttpHeader values")
        if (
            _headers_size(cast(tuple[HttpHeader, ...], headers))
            > self.limits.max_request_header_bytes
        ):
            raise OutboundHttpLimitExceeded("HTTP request headers exceed the requested limit")
        body = cast(object, self.body)
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        if len(body) > self.limits.max_request_body_bytes:
            raise OutboundHttpLimitExceeded("HTTP request body exceeds the requested limit")
        if self.method in (HttpMethod.GET, HttpMethod.HEAD) and body:
            raise OutboundHttpRequestInvalid(
                f"{self.method.value} requests must not contain a body"
            )
        route = cast(object, self.credential_route)
        if route is not None and not isinstance(route, CredentialRouteId):
            raise TypeError("credential_route must be CredentialRouteId or None")

    @property
    def scheme(self) -> HttpScheme:
        return self._scheme

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(method={self.method.value!r}, "
            "url=<redacted>, headers=<redacted>, body=<redacted>, "
            f"limits={self.limits!r}, credential_route=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class HttpTransferUsage:
    request_count: int
    request_bytes: int
    response_bytes: int
    response_wire_bytes: int
    decompressed_response_bytes: int
    redirect_count: int
    duration_ms: float

    def __post_init__(self) -> None:
        for name in (
            "request_count",
            "request_bytes",
            "response_bytes",
            "response_wire_bytes",
            "decompressed_response_bytes",
            "redirect_count",
        ):
            _require_non_negative_integer(name, getattr(self, name))
        if self.request_count == 0:
            raise ValueError("request_count must be positive")
        if self.response_wire_bytes < self.response_bytes:
            raise ValueError("response_wire_bytes must include response_bytes")
        _require_non_negative_finite("duration_ms", self.duration_ms)


@dataclass(frozen=True, slots=True, repr=False)
class OutboundHttpResponse:
    status_code: int
    headers: tuple[HttpHeader, ...]
    body: bytes
    usage: HttpTransferUsage

    def __post_init__(self) -> None:
        status = cast(object, self.status_code)
        if isinstance(status, bool) or not isinstance(status, int):
            raise TypeError("status_code must be an integer")
        if status < 200 or status > 599:
            raise ValueError("status_code must be between 200 and 599")
        headers = cast(object, self.headers)
        if not isinstance(headers, tuple):
            raise TypeError("headers must be a tuple")
        if any(not isinstance(header, HttpHeader) for header in cast(tuple[object, ...], headers)):
            raise TypeError("headers must contain HttpHeader values")
        if not isinstance(cast(object, self.body), bytes):
            raise TypeError("body must be bytes")
        if not isinstance(cast(object, self.usage), HttpTransferUsage):
            raise TypeError("usage must be HttpTransferUsage")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status_code={self.status_code}, "
            "headers=<redacted>, body=<redacted>, "
            f"usage={self.usage!r})"
        )


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class NetworkOperationContext:
    session_id: SessionId
    operation_id: OperationId
    grant: OutboundHttpGrant
    deadline_monotonic: float
    cancellation: NetworkCancellationSignal | None = None

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.session_id), SessionId):
            raise TypeError("session_id must be SessionId")
        if not isinstance(cast(object, self.operation_id), OperationId):
            raise TypeError("operation_id must be OperationId")
        if not isinstance(cast(object, self.grant), OutboundHttpGrant):
            raise TypeError("grant must be OutboundHttpGrant")
        _require_positive_finite("deadline_monotonic", self.deadline_monotonic)
        cancellation = cast(object, self.cancellation)
        if cancellation is not None and not callable(getattr(cancellation, "is_set", None)):
            raise TypeError("cancellation must provide is_set() or be None")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(session_id={self.session_id!r}, "
            f"operation_id={self.operation_id!r}, grant=<redacted>, "
            f"deadline_monotonic={self.deadline_monotonic!r}, "
            f"cancellation={'set' if self.cancellation is not None else 'none'})"
        )


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class OutboundHttpBinding:
    gateway: OutboundHttpGateway
    grant: OutboundHttpGrant

    def __post_init__(self) -> None:
        if not callable(getattr(cast(object, self.gateway), "send", None)):
            raise TypeError("gateway must provide async send()")
        if not isinstance(cast(object, self.grant), OutboundHttpGrant):
            raise TypeError("grant must be OutboundHttpGrant")

    def with_grant(self, grant: OutboundHttpGrant) -> OutboundHttpBinding:
        if not isinstance(cast(object, grant), OutboundHttpGrant):
            raise TypeError("grant must be OutboundHttpGrant")
        if not grant.is_within(self.grant):
            raise OutboundHttpDenied("requested outbound HTTP grant exceeds the host grant ceiling")
        return OutboundHttpBinding(gateway=self.gateway, grant=grant)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(gateway=<redacted>, grant=<redacted>)"


def _ordered_enum_tuple[EnumT: StrEnum](
    name: str,
    value: object,
    enum_type: type[EnumT],
) -> tuple[EnumT, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    raw = cast(tuple[object, ...], value)
    if any(not isinstance(item, enum_type) for item in raw):
        raise TypeError(f"{name} must contain {enum_type.__name__} values")
    ordered = tuple(sorted(cast(tuple[EnumT, ...], value), key=lambda item: item.value))
    if len(set(ordered)) != len(ordered):
        raise ValueError(f"{name} must not contain duplicates")
    return ordered


def _headers_size(headers: tuple[HttpHeader, ...]) -> int:
    return sum(
        len(header.name.encode("ascii")) + 2 + len(header.value.encode("utf-8")) + 2
        for header in headers
    )


def _require_identifier(name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")


def _require_non_negative_integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _require_positive_integer(name: str, value: object) -> None:
    _require_non_negative_integer(name, value)
    if value == 0:
        raise ValueError(f"{name} must be positive")


def _require_positive_finite(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


def _require_non_negative_finite(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be non-negative and finite")
