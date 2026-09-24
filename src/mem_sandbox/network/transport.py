"""Single-attempt bounded HTTP transport values and asyncio implementation support."""

from __future__ import annotations

import asyncio
import os
import socket
import ssl
import sys
import warnings
from dataclasses import dataclass
from typing import cast

from mem_sandbox.network.destination import NormalizedHttpUrl, ResolvedHttpAddress
from mem_sandbox.network.errors import (
    OutboundHttpCancelled,
    OutboundHttpLimitExceeded,
    OutboundHttpRequestInvalid,
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

_MINIMUM_STATUS_FRAMING_BYTES = 17


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
    max_response_wire_bytes: int

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
        for name in (
            "max_response_header_bytes",
            "max_response_body_bytes",
            "max_response_wire_bytes",
        ):
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
            f"max_response_body_bytes={self.max_response_body_bytes!r}, "
            f"max_response_wire_bytes={self.max_response_wire_bytes!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class HttpTransportResponse:
    status_code: int
    headers: tuple[HttpHeader, ...]
    body: bytes
    header_bytes: int
    header_wire_bytes: int
    metadata_wire_bytes: int
    body_wire_bytes: int
    wire_bytes: int
    body_omitted: bool = False

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
        if type(self.body_omitted) is not bool:
            raise TypeError("body_omitted must be a boolean")
        for name in (
            "header_bytes",
            "header_wire_bytes",
            "metadata_wire_bytes",
            "body_wire_bytes",
            "wire_bytes",
        ):
            value = cast(object, getattr(self, name))
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.header_bytes < _headers_size(self.headers):
            raise ValueError("header_bytes must include the response headers")
        if self.header_wire_bytes < _headers_wire_size(self.headers):
            raise ValueError("header_wire_bytes must include the response headers")
        if self.header_bytes < self.header_wire_bytes:
            raise ValueError("header_bytes must include raw header wire bytes")
        if self.metadata_wire_bytes < self.header_wire_bytes + _MINIMUM_STATUS_FRAMING_BYTES:
            raise ValueError("metadata_wire_bytes must include the final response head")
        if self.body_wire_bytes < len(self.body):
            raise ValueError("body_wire_bytes must include the encoded body")
        if self.body_omitted and (self.body or self.body_wire_bytes):
            raise ValueError("omitted response bodies must not include body bytes")
        _require_supported_response_framing(
            self.headers,
            len(self.body),
            self.body_wire_bytes,
            self.metadata_wire_bytes,
            self.header_wire_bytes,
            self.body_omitted,
        )
        if self.wire_bytes != self.metadata_wire_bytes + self.body_wire_bytes:
            raise ValueError("wire_bytes must equal metadata and body wire bytes")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status_code={self.status_code!r}, "
            "headers=<redacted>, body=<redacted>, "
            f"header_bytes={self.header_bytes!r}, "
            f"header_wire_bytes={self.header_wire_bytes!r}, "
            f"metadata_wire_bytes={self.metadata_wire_bytes!r}, "
            f"body_wire_bytes={self.body_wire_bytes!r}, "
            f"wire_bytes={self.wire_bytes!r}, "
            f"body_omitted={self.body_omitted!r})"
        )


class AsyncioHttpTransport:
    """Perform one direct HTTP/1.1 attempt against an admitted numeric peer."""

    def __init__(self) -> None:
        self._ssl_context = _create_system_ssl_context()

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
        reader_limit = max(
            min(
                request.max_response_header_bytes + 1028,
                request.max_response_wire_bytes + 1028,
            ),
            4096,
        )
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
            (
                status,
                headers,
                remaining_header_bytes,
                response_header_bytes,
                response_header_wire_bytes,
                response_head_wire_bytes,
            ) = await _read_final_response_head(
                reader,
                request.max_response_header_bytes,
                request.max_response_wire_bytes,
            )
            (
                body,
                response_body_wire_bytes,
                trailer_header_bytes,
                trailer_header_wire_bytes,
                trailer_wire_bytes,
                body_omitted,
            ) = await _read_response_body(
                reader,
                request,
                status,
                headers,
                remaining_header_bytes,
                request.max_response_wire_bytes - response_head_wire_bytes,
            )
            _require_not_cancelled(context)
            return HttpTransportResponse(
                status_code=status,
                headers=headers,
                body=body,
                header_bytes=response_header_bytes + trailer_header_bytes,
                header_wire_bytes=response_header_wire_bytes + trailer_header_wire_bytes,
                metadata_wire_bytes=response_head_wire_bytes + trailer_wire_bytes,
                body_wire_bytes=response_body_wire_bytes,
                wire_bytes=(
                    response_head_wire_bytes + trailer_wire_bytes + response_body_wire_bytes
                ),
                body_omitted=body_omitted,
            )
        finally:
            active_error = sys.exception()
            if writer is not None:
                await _close_writer(
                    writer,
                    context.deadline_monotonic,
                    abort=active_error is not None,
                )


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
    maximum_wire_bytes: int,
) -> tuple[int, tuple[HttpHeader, ...], int, int, int, int]:
    informational_count = 0
    remaining_header_bytes = maximum_header_bytes
    remaining_wire_bytes = maximum_wire_bytes
    header_wire_bytes = 0
    while True:
        (
            status,
            headers,
            consumed_header_bytes,
            consumed_header_wire_bytes,
            consumed_wire_bytes,
        ) = await _read_response_head(
            reader,
            remaining_header_bytes,
            remaining_wire_bytes,
        )
        remaining_header_bytes -= consumed_header_bytes
        remaining_wire_bytes -= consumed_wire_bytes
        header_wire_bytes += consumed_header_wire_bytes
        if status == 101:
            raise OutboundHttpResponseInvalid("HTTP protocol upgrades are not supported")
        if status < 200:
            informational_count += 1
            if informational_count > 8:
                raise OutboundHttpResponseInvalid(
                    "HTTP response contains too many informational messages"
                )
            continue
        return (
            status,
            headers,
            remaining_header_bytes,
            maximum_header_bytes - remaining_header_bytes,
            header_wire_bytes,
            maximum_wire_bytes - remaining_wire_bytes,
        )


async def _read_response_head(
    reader: asyncio.StreamReader,
    maximum_header_bytes: int,
    maximum_wire_bytes: int,
) -> tuple[int, tuple[HttpHeader, ...], int, int, int]:
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
    if len(raw) > maximum_wire_bytes:
        raise OutboundHttpLimitExceeded("HTTP response wire bytes exceed the requested limit")
    lines = raw[:-4].split(b"\r\n")
    if not lines or not lines[0] or len(lines[0]) > 1024:
        raise OutboundHttpResponseInvalid("HTTP response status line is invalid")
    status = _parse_status_line(lines[0])
    headers = _parse_header_lines(lines[1:])
    wire_header_bytes = len(raw) - len(lines[0]) - 4
    accounted_header_bytes = max(wire_header_bytes, _headers_size(headers))
    if accounted_header_bytes > maximum_header_bytes:
        raise OutboundHttpLimitExceeded("HTTP response headers exceed the requested limit")
    return status, headers, accounted_header_bytes, wire_header_bytes, len(raw)


def _parse_status_line(line: bytes) -> int:
    parts = line.split(b" ", 2)
    if (
        len(parts) != 3
        or parts[0] not in (b"HTTP/1.0", b"HTTP/1.1")
        or len(parts[1]) != 3
        or not parts[1].isdigit()
        or any((byte < 32 and byte != 9) or byte == 127 for byte in parts[2])
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
        except (OutboundHttpRequestInvalid, UnicodeError, ValueError):
            raise OutboundHttpResponseInvalid("HTTP response header is invalid") from None
        parsed.append(header)
    return tuple(parsed)


async def _read_response_body(
    reader: asyncio.StreamReader,
    request: HttpTransportRequest,
    status: int,
    headers: tuple[HttpHeader, ...],
    remaining_header_bytes: int,
    remaining_wire_bytes: int,
) -> tuple[bytes, int, int, int, int, bool]:
    transfer_encoding = _single_header(headers, "transfer-encoding")
    content_length = _single_header(headers, "content-length")
    if transfer_encoding is not None and content_length is not None:
        raise OutboundHttpResponseInvalid("HTTP response contains conflicting body framing")
    if request.method is HttpMethod.HEAD or status in (204, 205, 304):
        return b"", 0, 0, 0, 0, True
    if status in (301, 302, 303, 307, 308) and _single_header(headers, "location"):
        return b"", 0, 0, 0, 0, True
    if transfer_encoding is not None:
        if [token.strip().lower() for token in transfer_encoding.split(",")] != ["chunked"]:
            raise OutboundHttpResponseInvalid("HTTP response transfer encoding is unsupported")
        (
            body,
            body_wire_bytes,
            trailer_header_bytes,
            trailer_header_wire_bytes,
            trailer_wire_bytes,
        ) = await _read_chunked_body(
            reader,
            request.max_response_body_bytes,
            remaining_header_bytes,
            remaining_wire_bytes,
        )
        return (
            body,
            body_wire_bytes,
            trailer_header_bytes,
            trailer_header_wire_bytes,
            trailer_wire_bytes,
            False,
        )
    if content_length is not None:
        length = parse_http_content_length(content_length)
        if length > request.max_response_body_bytes:
            raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
        if length > remaining_wire_bytes:
            raise OutboundHttpLimitExceeded("HTTP response wire bytes exceed the requested limit")
        return await _read_exactly(reader, length), length, 0, 0, 0, False
    body, body_wire_bytes = await _read_to_eof(
        reader,
        request.max_response_body_bytes,
        remaining_wire_bytes,
    )
    return body, body_wire_bytes, 0, 0, 0, False


async def _read_chunked_body(
    reader: asyncio.StreamReader,
    maximum_body_bytes: int,
    maximum_trailer_bytes: int,
    maximum_wire_bytes: int,
) -> tuple[bytes, int, int, int, int]:
    body = bytearray()
    wire_bytes = 0
    while True:
        line = await _read_line(reader, 4096)
        wire_bytes += len(line) + 2
        if wire_bytes > maximum_wire_bytes:
            raise OutboundHttpLimitExceeded("HTTP response wire bytes exceed the requested limit")
        if b";" in line:
            raise OutboundHttpResponseInvalid("HTTP chunk extensions are unsupported")
        size_value = line
        if not size_value or any(byte not in b"0123456789abcdefABCDEF" for byte in size_value):
            raise OutboundHttpResponseInvalid("HTTP chunk size is invalid")
        significant_size = size_value.lstrip(b"0") or b"0"
        if len(significant_size) > 16:
            raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
        size = int(significant_size, 16)
        if size == 0:
            (
                trailer_header_bytes,
                trailer_header_wire_bytes,
                trailer_wire_bytes,
            ) = await _read_trailers(
                reader,
                maximum_trailer_bytes,
                maximum_wire_bytes - wire_bytes,
            )
            return (
                bytes(body),
                wire_bytes,
                trailer_header_bytes,
                trailer_header_wire_bytes,
                trailer_wire_bytes,
            )
        if size > maximum_body_bytes - len(body):
            raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
        if size + 2 > maximum_wire_bytes - wire_bytes:
            raise OutboundHttpLimitExceeded("HTTP response wire bytes exceed the requested limit")
        chunk = await _read_exactly(reader, size + 2)
        wire_bytes += size + 2
        if chunk[-2:] != b"\r\n":
            raise OutboundHttpResponseInvalid("HTTP chunk terminator is invalid")
        body.extend(chunk[:-2])


async def _read_trailers(
    reader: asyncio.StreamReader,
    maximum_bytes: int,
    maximum_wire_bytes: int,
) -> tuple[int, int, int]:
    logical_header_bytes = 0
    header_wire_bytes = 0
    wire_bytes = 0
    while True:
        line = await _read_line(reader, maximum_bytes + 2)
        wire_bytes += len(line) + 2
        if wire_bytes > maximum_wire_bytes:
            raise OutboundHttpLimitExceeded("HTTP response wire bytes exceed the requested limit")
        if not line:
            return max(logical_header_bytes, header_wire_bytes), header_wire_bytes, wire_bytes
        parsed = _parse_header_lines([line])
        logical_header_bytes += _headers_size(parsed)
        header_wire_bytes += len(line) + 2
        if max(logical_header_bytes, header_wire_bytes) > maximum_bytes:
            raise OutboundHttpLimitExceeded(
                "HTTP response trailers exceed the requested header limit"
            )


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
    maximum_wire_bytes: int,
) -> tuple[bytes, int]:
    body = bytearray()
    while True:
        chunk = await reader.read(
            min(
                64 * 1024,
                maximum - len(body) + 1,
                maximum_wire_bytes - len(body) + 1,
            )
        )
        if not chunk:
            return bytes(body), len(body)
        body.extend(chunk)
        if len(body) > maximum:
            raise OutboundHttpLimitExceeded("HTTP response body exceeds the requested limit")
        if len(body) > maximum_wire_bytes:
            raise OutboundHttpLimitExceeded("HTTP response wire bytes exceed the requested limit")


def _single_header(
    headers: tuple[HttpHeader, ...],
    name: str,
) -> str | None:
    values = [header.value.strip(" \t") for header in headers if header.name.lower() == name]
    if len(values) > 1:
        raise OutboundHttpResponseInvalid(f"HTTP response has multiple {name} headers")
    return values[0] if values else None


def parse_http_content_length(value: str) -> int:
    normalized = value.strip(" \t")
    maximum = str((1 << 63) - 1)
    if (
        not normalized
        or len(normalized) > len(maximum)
        or not normalized.isascii()
        or not normalized.isdigit()
        or (len(normalized) == len(maximum) and normalized > maximum)
    ):
        raise OutboundHttpResponseInvalid("HTTP response content length is invalid")
    return int(normalized, 10)


def _headers_size(headers: tuple[HttpHeader, ...]) -> int:
    return sum(
        len(str.__str__(header.name).encode("ascii"))
        + 2
        + len(str.__str__(header.value).encode("utf-8"))
        + 2
        for header in headers
    )


def _headers_wire_size(headers: tuple[HttpHeader, ...]) -> int:
    try:
        return sum(
            len(str.__str__(header.name).encode("ascii"))
            + 2
            + len(str.__str__(header.value).encode("latin-1"))
            + 2
            for header in headers
        )
    except UnicodeEncodeError:
        raise ValueError("response headers must be representable on the HTTP wire") from None


def _require_supported_response_framing(
    headers: tuple[HttpHeader, ...],
    body_bytes: int,
    body_wire_bytes: int,
    metadata_wire_bytes: int,
    header_wire_bytes: int,
    body_omitted: bool,
) -> None:
    transfer_encoding = _single_header(headers, "transfer-encoding")
    content_length = _single_header(headers, "content-length")
    if transfer_encoding is not None and content_length is not None:
        raise ValueError("response framing must not be ambiguous")
    if transfer_encoding is None:
        return
    if [token.strip().lower() for token in transfer_encoding.split(",")] != ["chunked"]:
        raise ValueError("response transfer encoding must be supported")
    if body_omitted:
        return
    minimum = 3 if body_bytes == 0 else body_bytes + len(f"{body_bytes:x}") + 7
    if body_wire_bytes < minimum:
        raise ValueError("body_wire_bytes must include chunk framing")
    if metadata_wire_bytes < header_wire_bytes + _MINIMUM_STATUS_FRAMING_BYTES + 2:
        raise ValueError("metadata_wire_bytes must include the trailer terminator")


def _create_system_ssl_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if sys.platform == "win32":
        try:
            for store in ("CA", "ROOT"):
                for certificate, encoding, trust in ssl.enum_certificates(store):
                    if encoding != "x509_asn":
                        continue
                    trusted_for_server = trust is True or (
                        isinstance(trust, set | frozenset) and ssl.Purpose.SERVER_AUTH.oid in trust
                    )
                    if not trusted_for_server:
                        continue
                    try:
                        context.load_verify_locations(cadata=certificate)
                    except ssl.SSLError as error:
                        warnings.warn(
                            f"ignored invalid certificate in Windows {store} store: {error}",
                            stacklevel=2,
                        )
        except PermissionError as error:
            warnings.warn(
                f"could not enumerate a Windows certificate store: {error}",
                stacklevel=2,
            )
    defaults = ssl.get_default_verify_paths()
    cafile = defaults.openssl_cafile
    capath = defaults.openssl_capath
    existing_cafile = cafile if cafile and os.path.isfile(cafile) else None
    existing_capath = capath if capath and os.path.isdir(capath) else None
    if existing_cafile is not None or existing_capath is not None:
        context.load_verify_locations(
            cafile=existing_cafile,
            capath=existing_capath,
        )
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.verify_flags |= ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


async def _close_writer(
    writer: asyncio.StreamWriter,
    deadline: float,
    *,
    abort: bool,
) -> None:
    try:
        writer.close()
    except Exception:
        if not abort:
            raise
        warnings.warn("failed to close outbound HTTP stream", stacklevel=2)
        _abort_writer(writer)
        return
    if abort:
        _abort_writer(writer)
        return
    try:
        async with asyncio.timeout_at(deadline):
            await writer.wait_closed()
    except BaseException:
        _abort_writer(writer)
        raise


def _abort_writer(writer: asyncio.StreamWriter) -> None:
    try:
        writer.transport.abort()
    except Exception:
        warnings.warn("failed to abort outbound HTTP stream", stacklevel=2)


def _require_not_cancelled(context: NetworkOperationContext) -> None:
    cancellation = context.cancellation
    if cancellation is not None and cancellation.is_set():
        raise OutboundHttpCancelled("outbound HTTP transport was cancelled")
