"""Destination-safe bounded outbound HTTP gateway orchestration."""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any, cast
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
    OutboundHttpRequestInvalid,
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
    parse_http_content_length,
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
        self._settling_tasks: set[asyncio.Task[object]] = set()

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
        _require_active(context)
        loop = asyncio.get_running_loop()
        deadline = min(
            context.deadline_monotonic,
            loop.time() + request.limits.timeout_seconds,
        )
        effective_context = replace(context, deadline_monotonic=deadline)
        _require_active(effective_context)
        started = loop.time()
        return await self._send_bounded(request, effective_context, started)

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
        encoded_response_bytes = 0
        response_wire_bytes = 0
        while True:
            _require_active(context)
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
            _require_active(context)
            resolution = await self._resolve(current, context)
            _require_active(context)
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
            _require_active(context)
            remaining_transfer = (
                limits.max_transferred_bytes - len(request.body) - response_wire_bytes
            )
            if remaining_transfer < 0:
                raise OutboundHttpLimitExceeded("HTTP transferred bytes exceed the requested limit")
            remaining_response = limits.max_response_body_bytes - encoded_response_bytes
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
                max_response_wire_bytes=remaining_transfer,
            )
            raw_response = await self._send_attempt(attempt, context)
            _require_active(context)
            request_count += 1
            raw_response = _require_transport_response(raw_response, attempt)
            encoded_response_bytes += len(raw_response.body)
            response_wire_bytes += raw_response.wire_bytes
            if len(request.body) + response_wire_bytes > limits.max_transferred_bytes:
                raise OutboundHttpLimitExceeded("HTTP transferred bytes exceed the requested limit")
            location = _redirect_location(raw_response)
            if raw_response.status_code in _REDIRECT_STATUSES and location is not None:
                if redirect_count >= limits.max_redirects:
                    raise OutboundHttpLimitExceeded(
                        "HTTP redirect count exceeds the requested limit"
                    )
                redirect_count += 1
                current = _normalize_redirect(current, location)
                continue
            if _response_omits_body(method, raw_response.status_code):
                if raw_response.body:
                    raise OutboundHttpResponseInvalid("bodyless transport response contains a body")
                body = b""
                headers = _published_headers(raw_response.headers)
            else:
                body, headers = _decode_response(
                    raw_response,
                    limits.max_decompressed_response_bytes,
                )
            _require_active(context)
            duration_ms = (asyncio.get_running_loop().time() - started) * 1000
            response = OutboundHttpResponse(
                status_code=raw_response.status_code,
                headers=headers,
                body=body,
                usage=HttpTransferUsage(
                    request_count=request_count,
                    request_bytes=len(request.body),
                    response_bytes=encoded_response_bytes,
                    response_wire_bytes=response_wire_bytes,
                    decompressed_response_bytes=len(body),
                    redirect_count=redirect_count,
                    duration_ms=duration_ms,
                ),
            )
            context.grant.require_response(request, response)
            return response

    async def _await_collaborator[ResultT](
        self,
        operation: Callable[[], Coroutine[Any, Any, ResultT]],
        context: NetworkOperationContext,
    ) -> ResultT:
        _require_active(context)
        remaining = context.deadline_monotonic - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise OutboundHttpTimeout("outbound HTTP operation exceeded its deadline")
        coroutine = operation()
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            raise
        try:
            done, _pending = await asyncio.wait((task,), timeout=remaining)
        except asyncio.CancelledError:
            task.cancel()
            self._retain_settling_task(task)
            raise
        if task not in done or asyncio.get_running_loop().time() >= context.deadline_monotonic:
            task.cancel()
            self._retain_settling_task(task)
            raise OutboundHttpTimeout("outbound HTTP operation exceeded its deadline")
        result = task.result()
        _require_active(context)
        return result

    def _retain_settling_task[ResultT](self, task: asyncio.Task[ResultT]) -> None:
        retained = cast(asyncio.Task[object], task)
        self._settling_tasks.add(retained)
        retained.add_done_callback(self._settling_task_done)

    def _settling_task_done(self, task: asyncio.Task[object]) -> None:
        self._settling_tasks.discard(task)
        if not task.cancelled():
            task.exception()

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
        stable_failure: type[Exception] | None = None
        decision: object = None
        try:
            decision = await self._await_collaborator(
                lambda: self._policy.evaluate(facts),
                context,
            )
        except asyncio.CancelledError:
            if _caller_is_cancelling():
                raise
            failed = True
        except OutboundHttpCancelled:
            if _cancellation_is_active(context):
                stable_failure = OutboundHttpCancelled
            else:
                failed = True
        except OutboundHttpTimeout:
            stable_failure = OutboundHttpTimeout
        except Exception:
            failed = True
        if stable_failure is OutboundHttpCancelled:
            raise OutboundHttpCancelled("network policy evaluation was cancelled")
        if stable_failure is OutboundHttpTimeout:
            raise OutboundHttpTimeout("network policy evaluation timed out")
        if failed or not isinstance(decision, NetworkPolicyDecision):
            raise OutboundHttpDenied("network policy evaluation failed closed")
        if decision.outcome is not NetworkPolicyOutcome.ALLOW:
            raise OutboundHttpDenied("network policy denied the destination")
        _require_active(context)

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
            resolution = await self._await_collaborator(
                lambda: self._resolver.resolve(url.hostname, url.port, context),
                context,
            )
        except asyncio.CancelledError:
            if _caller_is_cancelling():
                raise
            failed = True
        except OutboundHttpCancelled:
            if _cancellation_is_active(context):
                stable_failure = OutboundHttpCancelled
            else:
                failed = True
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
        _require_active(context)
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
            response = await self._await_collaborator(
                lambda: self._transport.send(request, context),
                context,
            )
        except asyncio.CancelledError:
            if _caller_is_cancelling():
                raise
            failed = True
        except OutboundHttpCancelled:
            if _cancellation_is_active(context):
                stable_failure = OutboundHttpCancelled
            else:
                failed = True
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
        _require_active(context)
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
) -> HttpTransportResponse:
    try:
        status = cast(object, response.status_code)
        headers = cast(object, response.headers)
        body = cast(object, response.body)
        wire_bytes = cast(object, response.wire_bytes)
    except AttributeError:
        raise OutboundHttpResponseInvalid("HTTP transport returned an invalid response") from None
    if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status <= 599:
        raise OutboundHttpResponseInvalid("HTTP transport returned an invalid response")
    validated_headers = _require_transport_headers(headers)
    if not isinstance(body, bytes):
        raise OutboundHttpResponseInvalid("HTTP transport returned an invalid response")
    if isinstance(wire_bytes, bool) or not isinstance(wire_bytes, int) or wire_bytes < len(body):
        raise OutboundHttpResponseInvalid("HTTP transport returned an invalid response")
    validated = HttpTransportResponse(
        status_code=status,
        headers=validated_headers,
        body=body,
        wire_bytes=wire_bytes,
    )
    if _headers_size(validated.headers) > request.max_response_header_bytes:
        raise OutboundHttpLimitExceeded("HTTP response headers exceed the requested limit")
    if len(validated.body) > request.max_response_body_bytes:
        raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
    if validated.wire_bytes > request.max_response_wire_bytes:
        raise OutboundHttpLimitExceeded("HTTP response wire bytes exceed the requested limit")
    content_lengths = [
        header.value for header in validated.headers if header.name.lower() == "content-length"
    ]
    if len(content_lengths) > 1:
        raise OutboundHttpResponseInvalid("HTTP response has ambiguous content length")
    if content_lengths:
        declared = parse_http_content_length(content_lengths[0])
        body_omitted = _response_omits_body(request.method, validated.status_code) or (
            validated.status_code in _REDIRECT_STATUSES
            and _redirect_location(validated) is not None
        )
        if not body_omitted and declared != len(validated.body):
            raise OutboundHttpResponseInvalid(
                "HTTP response content length does not match its body"
            )
    return validated


def _require_transport_headers(headers: object) -> tuple[HttpHeader, ...]:
    if not isinstance(headers, tuple):
        raise OutboundHttpResponseInvalid("HTTP transport returned an invalid response")
    validated: list[HttpHeader] = []
    for header in cast(tuple[object, ...], headers):
        if not isinstance(header, HttpHeader):
            raise OutboundHttpResponseInvalid("HTTP transport returned an invalid response")
        invalid = False
        name: object | None = None
        value: object | None = None
        try:
            name = cast(object, header.name)
            value = cast(object, header.value)
        except AttributeError:
            invalid = True
        validated_header: HttpHeader | None = None
        if not invalid and isinstance(name, str) and isinstance(value, str):
            try:
                validated_header = HttpHeader(name, value)
            except (OutboundHttpLimitExceeded, OutboundHttpRequestInvalid, TypeError):
                invalid = True
        else:
            invalid = True
        if invalid or validated_header is None:
            raise OutboundHttpResponseInvalid("HTTP transport returned an invalid response")
        validated.append(validated_header)
    return tuple(validated)


def _redirect_location(response: HttpTransportResponse) -> str | None:
    values = [header.value for header in response.headers if header.name.lower() == "location"]
    if len(values) > 1:
        raise OutboundHttpResponseInvalid("HTTP redirect has multiple locations")
    if not values:
        return None
    if not values[0]:
        raise OutboundHttpResponseInvalid("HTTP redirect location is empty")
    return values[0]


def _normalize_redirect(
    current: NormalizedHttpUrl,
    location: str,
) -> NormalizedHttpUrl:
    invalid = False
    normalized: NormalizedHttpUrl | None = None
    try:
        redirected = urljoin(current.canonical_url, location)
        normalized = normalize_http_url(redirected)
    except (OutboundHttpRequestInvalid, UnicodeError, ValueError):
        invalid = True
    if invalid or normalized is None:
        raise OutboundHttpResponseInvalid("HTTP redirect location is invalid")
    return normalized


def _response_omits_body(method: HttpMethod, status_code: int) -> bool:
    return method is HttpMethod.HEAD or status_code in (204, 205, 304)


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
    return body, _published_headers(response.headers)


def _published_headers(headers: tuple[HttpHeader, ...]) -> tuple[HttpHeader, ...]:
    return tuple(
        header
        for header in headers
        if header.name.lower() not in _FRAMING_RESPONSE_HEADERS | _SENSITIVE_RESPONSE_HEADERS
    )


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


def _require_active(context: NetworkOperationContext) -> None:
    if _cancellation_is_active(context):
        raise OutboundHttpCancelled("outbound HTTP operation was cancelled")
    if asyncio.get_running_loop().time() >= context.deadline_monotonic:
        raise OutboundHttpTimeout("outbound HTTP operation exceeded its deadline")


def _cancellation_is_active(context: NetworkOperationContext) -> bool:
    cancellation = context.cancellation
    return cancellation is not None and cancellation.is_set()


def _caller_is_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
