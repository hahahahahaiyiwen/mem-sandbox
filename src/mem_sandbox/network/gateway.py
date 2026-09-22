"""Destination-safe bounded outbound HTTP gateway orchestration."""

from __future__ import annotations

import asyncio
import zlib
from dataclasses import replace
from typing import cast
from urllib.parse import urljoin

from mem_sandbox.network.destination import (
    HttpRequestClass,
    IpAddressClass,
    NetworkPolicyDecision,
    NetworkPolicyOutcome,
    NetworkPolicyPhase,
    NetworkPolicyRequest,
    NetworkResolution,
    NormalizedHttpUrl,
    ResolvedHttpAddress,
    normalize_http_url,
)
from mem_sandbox.network.errors import (
    OutboundHttpCancelled,
    OutboundHttpDenied,
    OutboundHttpLimitExceeded,
    OutboundHttpResolutionFailed,
    OutboundHttpResponseInvalid,
    OutboundHttpTimeout,
    OutboundHttpTransportFailed,
)
from mem_sandbox.network.models import (
    HttpHeader,
    HttpMethod,
    HttpTransferUsage,
    NetworkOperationContext,
    OutboundHttpRequest,
    OutboundHttpResponse,
)
from mem_sandbox.network.ports import HttpTransport, NetworkPolicyEngine, NetworkResolver
from mem_sandbox.network.transport import (
    AdmittedHttpDestination,
    HttpTransportRequest,
    HttpTransportResponse,
)

_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
_CONTROLLED_REQUEST_HEADERS = frozenset(
    (
        "accept-encoding",
        "authorization",
        "connection",
        "content-length",
        "cookie",
        "expect",
        "host",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    )
)
_FRAMING_RESPONSE_HEADERS = frozenset(
    ("connection", "content-encoding", "content-length", "transfer-encoding")
)
_SENSITIVE_RESPONSE_HEADERS = frozenset(
    (
        "authentication-info",
        "proxy-authenticate",
        "proxy-authentication-info",
        "set-cookie",
        "set-cookie2",
        "www-authenticate",
    )
)


class BoundedOutboundHttpGateway:
    """Apply destination admission and limits around one-attempt transports."""

    def __init__(
        self,
        *,
        policy: NetworkPolicyEngine,
        resolver: NetworkResolver,
        transport: HttpTransport,
    ) -> None:
        if not callable(getattr(cast(object, policy), "evaluate", None)):
            raise TypeError("policy must provide async evaluate()")
        if not callable(getattr(cast(object, resolver), "resolve", None)):
            raise TypeError("resolver must provide async resolve()")
        if not callable(getattr(cast(object, transport), "send", None)):
            raise TypeError("transport must provide async send()")
        self._policy = policy
        self._resolver = resolver
        self._transport = transport

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        if not isinstance(cast(object, request), OutboundHttpRequest):
            raise TypeError("request must be OutboundHttpRequest")
        if not isinstance(cast(object, context), NetworkOperationContext):
            raise TypeError("context must be NetworkOperationContext")
        context.grant.require_request(request)
        _require_supported_request(request)
        _require_not_cancelled(context)
        loop = asyncio.get_running_loop()
        deadline = min(
            context.deadline_monotonic,
            loop.time() + request.limits.timeout_seconds,
        )
        effective_context = replace(context, deadline_monotonic=deadline)
        started = loop.time()
        timed_out = False
        response: OutboundHttpResponse | None = None
        try:
            response = await asyncio.wait_for(
                self._send_bounded(request, effective_context, started),
                timeout=max(0.0, deadline - loop.time()),
            )
        except TimeoutError:
            timed_out = True
        if timed_out:
            raise OutboundHttpTimeout("outbound HTTP operation exceeded its deadline")
        assert response is not None
        return response

    async def _send_bounded(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
        started: float,
    ) -> OutboundHttpResponse:
        limits = request.limits
        current = normalize_http_url(request.url)
        method = request.method
        request_count = 0
        redirect_count = 0
        transferred_response_bytes = 0
        while True:
            _require_not_cancelled(context)
            if request_count >= limits.max_requests:
                raise OutboundHttpLimitExceeded("HTTP request count exceeds the requested limit")
            if current.scheme not in context.grant.schemes:
                raise OutboundHttpDenied("redirect scheme is outside the effective grant")
            await self._require_policy(
                current,
                method,
                redirect_count,
                (),
                None,
                NetworkPolicyPhase.PRE_RESOLUTION,
                context,
            )
            resolution = await self._resolve(current, context)
            if not resolution.addresses:
                raise OutboundHttpResolutionFailed("controlled resolution returned no addresses")
            if any(
                address.classification is not IpAddressClass.GLOBAL
                for address in resolution.addresses
            ):
                raise OutboundHttpDenied("resolved destination contains a prohibited address")
            await self._require_policy(
                current,
                method,
                redirect_count,
                resolution.addresses,
                resolution.canonical_hostname,
                NetworkPolicyPhase.POST_RESOLUTION,
                context,
            )
            _require_not_cancelled(context)
            remaining_transfer = limits.max_transferred_bytes - transferred_response_bytes
            if remaining_transfer < 0:
                raise OutboundHttpLimitExceeded("HTTP transferred bytes exceed the requested limit")
            remaining_response = limits.max_response_body_bytes - transferred_response_bytes
            if remaining_response < 0:
                raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
            headers = _transport_headers(current, request.headers)
            if _headers_size(headers) > limits.max_request_header_bytes:
                raise OutboundHttpLimitExceeded("HTTP request headers exceed the requested limit")
            attempt = HttpTransportRequest(
                method=method,
                destination=AdmittedHttpDestination(
                    url=current,
                    address=resolution.addresses[0],
                ),
                headers=headers,
                max_response_header_bytes=limits.max_response_header_bytes,
                max_response_body_bytes=min(
                    remaining_response,
                    remaining_transfer,
                ),
            )
            raw_response = await self._send_attempt(attempt, context)
            request_count += 1
            _require_transport_response(raw_response, attempt)
            transferred_response_bytes += len(raw_response.body)
            if transferred_response_bytes > limits.max_transferred_bytes:
                raise OutboundHttpLimitExceeded("HTTP transferred bytes exceed the requested limit")
            location = _redirect_location(raw_response)
            if raw_response.status_code in _REDIRECT_STATUSES and location is not None:
                if redirect_count >= limits.max_redirects:
                    raise OutboundHttpLimitExceeded(
                        "HTTP redirect count exceeds the requested limit"
                    )
                redirect_count += 1
                current = normalize_http_url(urljoin(current.canonical_url, location))
                continue
            if method is HttpMethod.HEAD and raw_response.body:
                raise OutboundHttpResponseInvalid("HEAD transport response contains a body")
            body, headers = _decode_response(raw_response, limits.max_decompressed_response_bytes)
            duration_ms = (asyncio.get_running_loop().time() - started) * 1000
            response = OutboundHttpResponse(
                status_code=raw_response.status_code,
                headers=headers,
                body=body,
                usage=HttpTransferUsage(
                    request_count=request_count,
                    request_bytes=len(request.body),
                    response_bytes=transferred_response_bytes,
                    decompressed_response_bytes=len(body),
                    redirect_count=redirect_count,
                    duration_ms=duration_ms,
                ),
            )
            context.grant.require_response(request, response)
            return response

    async def _require_policy(
        self,
        url: NormalizedHttpUrl,
        method: HttpMethod,
        redirect_depth: int,
        addresses: tuple[ResolvedHttpAddress, ...],
        canonical_hostname: str | None,
        phase: NetworkPolicyPhase,
        context: NetworkOperationContext,
    ) -> None:
        facts = NetworkPolicyRequest(
            policy_id=context.grant.policy_id,
            session_id=context.session_id,
            operation_id=context.operation_id,
            phase=phase,
            method=method,
            request_class=HttpRequestClass.BASELINE,
            scheme=url.scheme,
            hostname=url.hostname,
            port=url.port,
            redirect_depth=redirect_depth,
            addresses=addresses,
            canonical_hostname=canonical_hostname,
        )
        failed = False
        decision: object = None
        try:
            decision = await self._policy.evaluate(facts)
        except asyncio.CancelledError:
            if _caller_is_cancelling():
                raise
            failed = True
        except Exception:
            failed = True
        if failed or not isinstance(decision, NetworkPolicyDecision):
            raise OutboundHttpDenied("network policy evaluation failed closed")
        if decision.outcome is not NetworkPolicyOutcome.ALLOW:
            raise OutboundHttpDenied("network policy denied the destination")
        _require_not_cancelled(context)

    async def _resolve(
        self,
        url: NormalizedHttpUrl,
        context: NetworkOperationContext,
    ) -> NetworkResolution:
        literal = url.ip_literal
        if literal is not None:
            return NetworkResolution(hostname=url.hostname, addresses=(literal,))
        failed = False
        stable_failure: type[Exception] | None = None
        resolution: object = None
        try:
            resolution = await self._resolver.resolve(url.hostname, url.port, context)
        except asyncio.CancelledError:
            if _caller_is_cancelling():
                raise
            failed = True
        except OutboundHttpCancelled:
            stable_failure = OutboundHttpCancelled
        except OutboundHttpTimeout:
            stable_failure = OutboundHttpTimeout
        except OutboundHttpResolutionFailed:
            stable_failure = OutboundHttpResolutionFailed
        except Exception:
            failed = True
        if stable_failure is OutboundHttpCancelled:
            raise OutboundHttpCancelled("controlled destination resolution was cancelled")
        if stable_failure is OutboundHttpTimeout:
            raise OutboundHttpTimeout("controlled destination resolution timed out")
        if stable_failure is OutboundHttpResolutionFailed:
            raise OutboundHttpResolutionFailed("controlled destination resolution failed")
        if (
            failed
            or not isinstance(resolution, NetworkResolution)
            or resolution.hostname != url.hostname
        ):
            raise OutboundHttpResolutionFailed("controlled destination resolution failed")
        _require_not_cancelled(context)
        return resolution

    async def _send_attempt(
        self,
        request: HttpTransportRequest,
        context: NetworkOperationContext,
    ) -> HttpTransportResponse:
        failed = False
        stable_failure: type[Exception] | None = None
        response: object = None
        try:
            response = await self._transport.send(request, context)
        except asyncio.CancelledError:
            if _caller_is_cancelling():
                raise
            failed = True
        except OutboundHttpCancelled:
            stable_failure = OutboundHttpCancelled
        except OutboundHttpTimeout:
            stable_failure = OutboundHttpTimeout
        except OutboundHttpLimitExceeded:
            stable_failure = OutboundHttpLimitExceeded
        except OutboundHttpResponseInvalid:
            stable_failure = OutboundHttpResponseInvalid
        except Exception:
            failed = True
        if stable_failure is OutboundHttpCancelled:
            raise OutboundHttpCancelled("outbound HTTP transport was cancelled")
        if stable_failure is OutboundHttpTimeout:
            raise OutboundHttpTimeout("outbound HTTP transport timed out")
        if stable_failure is OutboundHttpLimitExceeded:
            raise OutboundHttpLimitExceeded("outbound HTTP transport exceeded a limit")
        if stable_failure is OutboundHttpResponseInvalid:
            raise OutboundHttpResponseInvalid("outbound HTTP response is invalid")
        if failed:
            raise OutboundHttpTransportFailed("outbound HTTP transport attempt failed")
        if not isinstance(response, HttpTransportResponse):
            raise OutboundHttpResponseInvalid(
                "outbound HTTP transport returned an invalid response"
            )
        _require_not_cancelled(context)
        return response


def _require_supported_request(request: OutboundHttpRequest) -> None:
    if request.credential_route is not None:
        raise OutboundHttpDenied("credential routing is not available in this transport")
    seen: set[str] = set()
    for header in request.headers:
        name = header.name.lower()
        if name in seen:
            raise OutboundHttpDenied("duplicate HTTP request headers are not supported")
        seen.add(name)
        if name in _CONTROLLED_REQUEST_HEADERS or name.startswith("proxy-"):
            raise OutboundHttpDenied("HTTP request header is controlled by the gateway")


def _transport_headers(
    url: NormalizedHttpUrl,
    request_headers: tuple[HttpHeader, ...],
) -> tuple[HttpHeader, ...]:
    return (
        HttpHeader("Host", url.authority),
        HttpHeader("Connection", "close"),
        HttpHeader("Accept-Encoding", "gzip, deflate"),
        *request_headers,
    )


def _require_transport_response(
    response: HttpTransportResponse,
    request: HttpTransportRequest,
) -> None:
    if _headers_size(response.headers) > request.max_response_header_bytes:
        raise OutboundHttpLimitExceeded("HTTP response headers exceed the requested limit")
    if len(response.body) > request.max_response_body_bytes:
        raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
    content_lengths = [
        header.value.strip()
        for header in response.headers
        if header.name.lower() == "content-length"
    ]
    if len(content_lengths) > 1:
        raise OutboundHttpResponseInvalid("HTTP response has ambiguous content length")
    if content_lengths:
        try:
            declared = int(content_lengths[0], 10)
        except ValueError:
            raise OutboundHttpResponseInvalid("HTTP response content length is invalid") from None
        body_omitted = (
            request.method is HttpMethod.HEAD
            or response.status_code in (204, 205, 304)
            or (
                response.status_code in _REDIRECT_STATUSES
                and _redirect_location(response) is not None
            )
        )
        if declared < 0 or (not body_omitted and declared != len(response.body)):
            raise OutboundHttpResponseInvalid(
                "HTTP response content length does not match its body"
            )


def _redirect_location(response: HttpTransportResponse) -> str | None:
    values = [header.value for header in response.headers if header.name.lower() == "location"]
    if len(values) > 1:
        raise OutboundHttpResponseInvalid("HTTP redirect has multiple locations")
    if not values:
        return None
    if not values[0]:
        raise OutboundHttpResponseInvalid("HTTP redirect location is empty")
    return values[0]


def _decode_response(
    response: HttpTransportResponse,
    maximum: int,
) -> tuple[bytes, tuple[HttpHeader, ...]]:
    encodings = [
        header.value.strip().lower()
        for header in response.headers
        if header.name.lower() == "content-encoding"
    ]
    if len(encodings) > 1:
        raise OutboundHttpResponseInvalid("HTTP response has multiple content encodings")
    encoding = encodings[0] if encodings else "identity"
    if "," in encoding:
        raise OutboundHttpResponseInvalid("HTTP response content encoding is unsupported")
    if encoding in ("", "identity"):
        body = response.body
    elif encoding == "gzip":
        body = _decompress(response.body, maximum, (zlib.MAX_WBITS | 16,))
    elif encoding == "deflate":
        body = _decompress(response.body, maximum, (zlib.MAX_WBITS, -zlib.MAX_WBITS))
    else:
        raise OutboundHttpResponseInvalid("HTTP response content encoding is unsupported")
    if len(body) > maximum:
        raise OutboundHttpLimitExceeded(
            "HTTP decompressed response body exceeds the requested limit"
        )
    headers = tuple(
        header
        for header in response.headers
        if header.name.lower() not in _FRAMING_RESPONSE_HEADERS | _SENSITIVE_RESPONSE_HEADERS
    )
    return body, headers


def _decompress(data: bytes, maximum: int, window_bits: tuple[int, ...]) -> bytes:
    for bits in window_bits:
        invalid = False
        exceeded = False
        output = b""
        try:
            decompressor = zlib.decompressobj(bits)
            output = decompressor.decompress(data, maximum + 1)
            if len(output) > maximum or decompressor.unconsumed_tail:
                exceeded = True
            else:
                output += decompressor.flush(maximum - len(output) + 1)
                if len(output) > maximum:
                    exceeded = True
                elif not decompressor.eof or decompressor.unused_data:
                    invalid = True
        except zlib.error:
            invalid = True
        if exceeded:
            raise OutboundHttpLimitExceeded(
                "HTTP decompressed response body exceeds the requested limit"
            )
        if not invalid:
            return output
    raise OutboundHttpResponseInvalid("HTTP response content encoding is malformed")


def _headers_size(headers: tuple[HttpHeader, ...]) -> int:
    return sum(
        len(header.name.encode("ascii")) + 2 + len(header.value.encode("utf-8")) + 2
        for header in headers
    )


def _require_not_cancelled(context: NetworkOperationContext) -> None:
    cancellation = context.cancellation
    if cancellation is not None and cancellation.is_set():
        raise OutboundHttpCancelled("outbound HTTP operation was cancelled")


def _caller_is_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
