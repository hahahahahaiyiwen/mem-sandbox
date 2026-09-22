"""Single-attempt bounded HTTP transport values and asyncio implementation support."""

from __future__ import annotations

import asyncio
import socket
import ssl
import sys
from dataclasses import dataclass
from typing import cast

from mem_sandbox.network.destination import NormalizedHttpUrl, ResolvedHttpAddress
from mem_sandbox.network.errors import (
    OutboundHttpCancelled,
    OutboundHttpLimitExceeded,
    OutboundHttpResponseInvalid,
    OutboundHttpTimeout,
    OutboundHttpTransportFailed,
)
from mem_sandbox.network.models import (
    HttpHeader,
    HttpMethod,
    HttpScheme,
    NetworkOperationContext,
)


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class AdmittedHttpDestination:
    url: NormalizedHttpUrl
    address: ResolvedHttpAddress

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.url), NormalizedHttpUrl):
            raise TypeError("url must be NormalizedHttpUrl")
        if not isinstance(cast(object, self.address), ResolvedHttpAddress):
            raise TypeError("address must be ResolvedHttpAddress")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(url=<redacted>, address=<redacted>)"


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class HttpTransportRequest:
    method: HttpMethod
    destination: AdmittedHttpDestination
    headers: tuple[HttpHeader, ...]
    max_response_header_bytes: int
    max_response_body_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.method), HttpMethod):
            raise TypeError("method must be HttpMethod")
        if not isinstance(cast(object, self.destination), AdmittedHttpDestination):
            raise TypeError("destination must be AdmittedHttpDestination")
        headers = cast(object, self.headers)
        if not isinstance(headers, tuple):
            raise TypeError("headers must be a tuple")
        if any(not isinstance(header, HttpHeader) for header in cast(tuple[object, ...], headers)):
            raise TypeError("headers must contain HttpHeader values")
        for name in ("max_response_header_bytes", "max_response_body_bytes"):
            value = cast(object, getattr(self, name))
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must not be negative")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(method={self.method.value!r}, "
            "destination=<redacted>, headers=<redacted>, "
            f"max_response_header_bytes={self.max_response_header_bytes!r}, "
            f"max_response_body_bytes={self.max_response_body_bytes!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class HttpTransportResponse:
    status_code: int
    headers: tuple[HttpHeader, ...]
    body: bytes

    def __post_init__(self) -> None:
        status = cast(object, self.status_code)
        if isinstance(status, bool) or not isinstance(status, int):
            raise TypeError("status_code must be an integer")
        if status < 100 or status > 599:
            raise ValueError("status_code must be between 100 and 599")
        headers = cast(object, self.headers)
        if not isinstance(headers, tuple):
            raise TypeError("headers must be a tuple")
        if any(not isinstance(header, HttpHeader) for header in cast(tuple[object, ...], headers)):
            raise TypeError("headers must contain HttpHeader values")
        if not isinstance(cast(object, self.body), bytes):
            raise TypeError("body must be bytes")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status_code={self.status_code!r}, "
            "headers=<redacted>, body=<redacted>)"
        )


class AsyncioHttpTransport:
    """Perform one direct HTTP/1.1 attempt against an admitted numeric peer."""

    def __init__(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        context = ssl_context or ssl.create_default_context()
        if not isinstance(cast(object, context), ssl.SSLContext):
            raise TypeError("ssl_context must be SSLContext or None")
        if not context.check_hostname or context.verify_mode is not ssl.CERT_REQUIRED:
            raise ValueError("ssl_context must require certificate and hostname validation")
        self._ssl_context = context

    async def send(
        self,
        request: HttpTransportRequest,
        context: NetworkOperationContext,
    ) -> HttpTransportResponse:
        if not isinstance(cast(object, request), HttpTransportRequest):
            raise TypeError("request must be HttpTransportRequest")
        if not isinstance(cast(object, context), NetworkOperationContext):
            raise TypeError("context must be NetworkOperationContext")
        _require_not_cancelled(context)
        timed_out = False
        failed = False
        safe_failure: type[Exception] | None = None
        response: HttpTransportResponse | None = None
        try:
            async with asyncio.timeout_at(context.deadline_monotonic):
                response = await self._send_once(request, context)
        except TimeoutError:
            timed_out = True
        except asyncio.CancelledError:
            raise
        except OutboundHttpCancelled:
            safe_failure = OutboundHttpCancelled
        except OutboundHttpLimitExceeded:
            safe_failure = OutboundHttpLimitExceeded
        except OutboundHttpResponseInvalid:
            safe_failure = OutboundHttpResponseInvalid
        except Exception:
            failed = True
        if timed_out:
            raise OutboundHttpTimeout("outbound HTTP transport timed out")
        if safe_failure is OutboundHttpCancelled:
            raise OutboundHttpCancelled("outbound HTTP transport was cancelled")
        if safe_failure is OutboundHttpLimitExceeded:
            raise OutboundHttpLimitExceeded("outbound HTTP transport exceeded a limit")
        if safe_failure is OutboundHttpResponseInvalid:
            raise OutboundHttpResponseInvalid("outbound HTTP transport response is invalid")
        if failed:
            raise OutboundHttpTransportFailed("outbound HTTP transport attempt failed")
        assert response is not None
        return response

    async def _send_once(
        self,
        request: HttpTransportRequest,
        context: NetworkOperationContext,
    ) -> HttpTransportResponse:
        url = request.destination.url
        address = request.destination.address
        family = socket.AF_INET if address.ip.version == 4 else socket.AF_INET6
        tls = self._ssl_context if url.scheme is HttpScheme.HTTPS else None
        server_hostname = url.hostname if tls is not None else None
        reader_limit = max(request.max_response_header_bytes + 1028, 4096)
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_connection(
                host=address.value,
                port=url.port,
                family=family,
                flags=socket.AI_NUMERICHOST,
                ssl=tls,
                server_hostname=server_hostname,
                limit=reader_limit,
            )
            _require_not_cancelled(context)
            writer.write(_encode_request(request))
            await writer.drain()
            status, headers, remaining_header_bytes = await _read_final_response_head(
                reader,
                request.max_response_header_bytes,
            )
            body = await _read_response_body(
                reader,
                request,
                status,
                headers,
                remaining_header_bytes,
            )
            _require_not_cancelled(context)
            return HttpTransportResponse(status_code=status, headers=headers, body=body)
        finally:
            active_error = sys.exception()
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    if active_error is None:
                        raise


def _encode_request(request: HttpTransportRequest) -> bytes:
    start = (f"{request.method.value} {request.destination.url.target} HTTP/1.1\r\n").encode(
        "ascii"
    )
    headers = b"".join(
        header.name.encode("ascii") + b": " + header.value.encode("utf-8") + b"\r\n"
        for header in request.headers
    )
    return start + headers + b"\r\n"


async def _read_final_response_head(
    reader: asyncio.StreamReader,
    maximum_header_bytes: int,
) -> tuple[int, tuple[HttpHeader, ...], int]:
    informational_count = 0
    remaining_header_bytes = maximum_header_bytes
    while True:
        status, headers, consumed_header_bytes = await _read_response_head(
            reader,
            remaining_header_bytes,
        )
        remaining_header_bytes -= consumed_header_bytes
        if status == 101:
            raise OutboundHttpResponseInvalid("HTTP protocol upgrades are not supported")
        if status < 200:
            informational_count += 1
            if informational_count > 8:
                raise OutboundHttpResponseInvalid(
                    "HTTP response contains too many informational messages"
                )
            continue
        return status, headers, remaining_header_bytes


async def _read_response_head(
    reader: asyncio.StreamReader,
    maximum_header_bytes: int,
) -> tuple[int, tuple[HttpHeader, ...], int]:
    too_large = False
    incomplete = False
    raw = b""
    try:
        raw = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        too_large = True
    except asyncio.IncompleteReadError:
        incomplete = True
    if too_large:
        raise OutboundHttpLimitExceeded("HTTP response headers exceed the requested limit")
    if incomplete:
        raise OutboundHttpResponseInvalid("HTTP response headers are incomplete")
    lines = raw[:-4].split(b"\r\n")
    if not lines or not lines[0] or len(lines[0]) > 1024:
        raise OutboundHttpResponseInvalid("HTTP response status line is invalid")
    status = _parse_status_line(lines[0])
    headers = _parse_header_lines(lines[1:])
    wire_header_bytes = len(raw) - len(lines[0]) - 4
    accounted_header_bytes = max(wire_header_bytes, _headers_size(headers))
    if accounted_header_bytes > maximum_header_bytes:
        raise OutboundHttpLimitExceeded("HTTP response headers exceed the requested limit")
    return status, headers, accounted_header_bytes


def _parse_status_line(line: bytes) -> int:
    parts = line.split(b" ", 2)
    if (
        len(parts) < 2
        or parts[0] not in (b"HTTP/1.0", b"HTTP/1.1")
        or len(parts[1]) != 3
        or not parts[1].isdigit()
    ):
        raise OutboundHttpResponseInvalid("HTTP response status line is invalid")
    status = int(parts[1], 10)
    if status < 100 or status > 599:
        raise OutboundHttpResponseInvalid("HTTP response status is outside its range")
    return status


def _parse_header_lines(lines: list[bytes]) -> tuple[HttpHeader, ...]:
    parsed: list[HttpHeader] = []
    for line in lines:
        if not line or line[:1] in (b" ", b"\t") or b":" not in line:
            raise OutboundHttpResponseInvalid("HTTP response header line is invalid")
        raw_name, raw_value = line.split(b":", 1)
        try:
            name = raw_name.decode("ascii")
            value_bytes = raw_value.strip(b" \t")
            if any((byte < 32 and byte != 9) or byte == 127 for byte in value_bytes):
                raise ValueError
            value = value_bytes.decode("latin-1")
            header = HttpHeader(name, value)
        except (UnicodeError, ValueError):
            raise OutboundHttpResponseInvalid("HTTP response header is invalid") from None
        parsed.append(header)
    return tuple(parsed)


async def _read_response_body(
    reader: asyncio.StreamReader,
    request: HttpTransportRequest,
    status: int,
    headers: tuple[HttpHeader, ...],
    remaining_header_bytes: int,
) -> bytes:
    transfer_encoding = _single_header(headers, "transfer-encoding")
    content_length = _single_header(headers, "content-length")
    if transfer_encoding is not None and content_length is not None:
        raise OutboundHttpResponseInvalid("HTTP response contains conflicting body framing")
    if request.method is HttpMethod.HEAD or status in (204, 205, 304):
        return b""
    if status in (301, 302, 303, 307, 308) and _single_header(headers, "location"):
        return b""
    if transfer_encoding is not None:
        if [token.strip().lower() for token in transfer_encoding.split(",")] != ["chunked"]:
            raise OutboundHttpResponseInvalid("HTTP response transfer encoding is unsupported")
        return await _read_chunked_body(
            reader,
            request.max_response_body_bytes,
            remaining_header_bytes,
        )
    if content_length is not None:
        try:
            length = int(content_length, 10)
        except ValueError:
            raise OutboundHttpResponseInvalid("HTTP response content length is invalid") from None
        if length < 0:
            raise OutboundHttpResponseInvalid("HTTP response content length is invalid")
        if length > request.max_response_body_bytes:
            raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
        return await _read_exactly(reader, length)
    return await _read_to_eof(reader, request.max_response_body_bytes)


async def _read_chunked_body(
    reader: asyncio.StreamReader,
    maximum_body_bytes: int,
    maximum_trailer_bytes: int,
) -> bytes:
    body = bytearray()
    while True:
        line = await _read_line(reader, 4096)
        size_value = line.split(b";", 1)[0].strip()
        if not size_value:
            raise OutboundHttpResponseInvalid("HTTP chunk size is invalid")
        try:
            size = int(size_value, 16)
        except ValueError:
            raise OutboundHttpResponseInvalid("HTTP chunk size is invalid") from None
        if size < 0:
            raise OutboundHttpResponseInvalid("HTTP chunk size is invalid")
        if size == 0:
            await _read_trailers(reader, maximum_trailer_bytes)
            return bytes(body)
        if size > maximum_body_bytes - len(body):
            raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
        chunk = await _read_exactly(reader, size + 2)
        if chunk[-2:] != b"\r\n":
            raise OutboundHttpResponseInvalid("HTTP chunk terminator is invalid")
        body.extend(chunk[:-2])


async def _read_trailers(
    reader: asyncio.StreamReader,
    maximum_bytes: int,
) -> None:
    consumed = 0
    while True:
        line = await _read_line(reader, maximum_bytes + 2)
        if not line:
            return
        consumed += len(line) + 2
        if consumed > maximum_bytes:
            raise OutboundHttpLimitExceeded(
                "HTTP response trailers exceed the requested header limit"
            )
        _parse_header_lines([line])


async def _read_line(reader: asyncio.StreamReader, maximum: int) -> bytes:
    too_large = False
    incomplete = False
    raw = b""
    try:
        raw = await reader.readuntil(b"\r\n")
    except asyncio.LimitOverrunError:
        too_large = True
    except asyncio.IncompleteReadError:
        incomplete = True
    if too_large or len(raw) > maximum:
        raise OutboundHttpLimitExceeded("HTTP response framing exceeds its limit")
    if incomplete:
        raise OutboundHttpResponseInvalid("HTTP response framing is incomplete")
    return raw[:-2]


async def _read_exactly(reader: asyncio.StreamReader, length: int) -> bytes:
    incomplete = False
    data = b""
    try:
        data = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        incomplete = True
    if incomplete:
        raise OutboundHttpResponseInvalid("HTTP response body is incomplete")
    return data


async def _read_to_eof(
    reader: asyncio.StreamReader,
    maximum: int,
) -> bytes:
    body = bytearray()
    while True:
        chunk = await reader.read(min(64 * 1024, maximum - len(body) + 1))
        if not chunk:
            return bytes(body)
        body.extend(chunk)
        if len(body) > maximum:
            raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")


def _single_header(
    headers: tuple[HttpHeader, ...],
    name: str,
) -> str | None:
    values = [header.value.strip() for header in headers if header.name.lower() == name]
    if len(values) > 1:
        raise OutboundHttpResponseInvalid(f"HTTP response has multiple {name} headers")
    return values[0] if values else None


def _headers_size(headers: tuple[HttpHeader, ...]) -> int:
    return sum(
        len(header.name.encode("ascii")) + 2 + len(header.value.encode("utf-8")) + 2
        for header in headers
    )


def _require_not_cancelled(context: NetworkOperationContext) -> None:
    cancellation = context.cancellation
    if cancellation is not None and cancellation.is_set():
        raise OutboundHttpCancelled("outbound HTTP transport was cancelled")
