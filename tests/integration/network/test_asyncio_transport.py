import asyncio
import inspect
import ssl
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from mem_sandbox.core import OperationId, SessionId
from mem_sandbox.network import (
    AdmittedHttpDestination,
    AsyncioHttpTransport,
    HttpHeader,
    HttpMethod,
    HttpScheme,
    HttpTransferLimits,
    HttpTransportRequest,
    NetworkOperationContext,
    NetworkPolicyId,
    OutboundHttpCancelled,
    OutboundHttpGrant,
    OutboundHttpLimitExceeded,
    OutboundHttpResponseInvalid,
    OutboundHttpTimeout,
    OutboundHttpTransportFailed,
    ResolvedHttpAddress,
    SystemNetworkResolver,
    normalize_http_url,
)


class Cancellation:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_set(self) -> bool:
        return self.cancelled


def context(
    *,
    deadline: float | None = None,
    cancellation: Cancellation | None = None,
) -> NetworkOperationContext:
    transfer_limits = HttpTransferLimits(
        timeout_seconds=1,
        max_request_header_bytes=1024,
        max_request_body_bytes=0,
        max_response_header_bytes=1024,
        max_response_body_bytes=1024,
        max_decompressed_response_bytes=1024,
        max_redirects=1,
        max_requests=2,
        max_transferred_bytes=2048,
    )
    return NetworkOperationContext(
        session_id=SessionId(UUID(int=1)),
        operation_id=OperationId(UUID(int=2)),
        grant=OutboundHttpGrant(
            policy_id=NetworkPolicyId("fixture"),
            methods=(HttpMethod.GET,),
            schemes=(HttpScheme.HTTP, HttpScheme.HTTPS),
            limits=transfer_limits,
        ),
        deadline_monotonic=deadline or asyncio.get_running_loop().time() + 10,
        cancellation=cancellation,
    )


def transport_request(
    port: int,
    *,
    maximum_body: int = 1024,
    maximum_headers: int = 1024,
    maximum_wire: int = 2048,
    scheme: HttpScheme = HttpScheme.HTTP,
    hostname: str = "example.test",
) -> HttpTransportRequest:
    url = normalize_http_url(f"{scheme.value}://{hostname}:{port}/source?q=1")
    return HttpTransportRequest(
        method=HttpMethod.GET,
        destination=AdmittedHttpDestination(
            url=url,
            address=ResolvedHttpAddress("127.0.0.1"),
        ),
        headers=(
            HttpHeader("Host", url.authority),
            HttpHeader("Connection", "close"),
            HttpHeader("Accept-Encoding", "gzip, deflate"),
        ),
        max_response_header_bytes=maximum_headers,
        max_response_body_bytes=maximum_body,
        max_response_wire_bytes=maximum_wire,
    )


async def run_server(
    handler: Callable[
        [asyncio.StreamReader, asyncio.StreamWriter],
        Awaitable[None],
    ],
) -> tuple[asyncio.AbstractServer, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    socket = server.sockets[0]
    return server, int(socket.getsockname()[1])


def transport_with_test_trust(
    monkeypatch: pytest.MonkeyPatch,
    context: ssl.SSLContext,
) -> AsyncioHttpTransport:
    monkeypatch.setattr(
        "mem_sandbox.network.transport._create_system_ssl_context",
        lambda: context,
    )
    return AsyncioHttpTransport()


def test_transport_does_not_accept_external_tls_configuration() -> None:
    assert tuple(inspect.signature(AsyncioHttpTransport).parameters) == ()


def test_system_tls_context_loads_immutable_purpose_sets_and_secure_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_root = Path(__file__).parents[2] / "fixtures" / "network"
    certificate = fixture_root / "example-test-cert.pem"
    certificate_der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))

    def enum_certificates(_store: str) -> list[tuple[bytes, str, frozenset[str]]]:
        return [
            (
                certificate_der,
                "x509_asn",
                frozenset((ssl.Purpose.SERVER_AUTH.oid,)),
            )
        ]

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "platform", "win32")
        scoped.delattr(ssl, "enum_certificates", raising=False)
        scoped.setattr(ssl, "enum_certificates", enum_certificates, raising=False)
        subject = AsyncioHttpTransport()

    owned_context = cast(ssl.SSLContext, vars(subject)["_ssl_context"])
    assert certificate_der in owned_context.get_ca_certs(binary_form=True)
    secure_flags = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
    assert owned_context.verify_flags & secure_flags == secure_flags


@pytest.mark.asyncio
async def test_transport_connects_to_admitted_ip_and_ignores_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received = asyncio.Future[bytes]()

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_bytes = await reader.readuntil(b"\r\n\r\n")
            received.set_result(request_bytes)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 5\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"value"
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        response = await AsyncioHttpTransport().send(
            transport_request(port),
            context(),
        )
    finally:
        server.close()
        await server.wait_closed()

    sent = await received
    assert sent.startswith(b"GET /source?q=1 HTTP/1.1\r\n")
    assert f"Host: example.test:{port}\r\n".encode() in sent
    assert b"Connection: close\r\n" in sent
    assert response.status_code == 200
    assert response.body == b"value"


@pytest.mark.asyncio
async def test_https_uses_original_hostname_for_sni_and_certificate_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_root = Path(__file__).parents[2] / "fixtures" / "network"
    certificate = fixture_root / "example-test-cert.pem"
    private_key = fixture_root / "example-test-key.pem"
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, private_key)
    observed_sni: list[str | None] = []
    server_context.set_servername_callback(
        lambda _socket, server_name, _context: observed_sni.append(server_name)
    )
    client_context = ssl.create_default_context(cafile=str(certificate))

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(
        handler,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    port = int(server.sockets[0].getsockname()[1])
    try:
        response = await transport_with_test_trust(monkeypatch, client_context).send(
            transport_request(port, scheme=HttpScheme.HTTPS),
            context(),
        )
    finally:
        server.close()
        await server.wait_closed()

    assert response.body == b"ok"
    assert observed_sni == ["example.test"]


@pytest.mark.asyncio
async def test_https_rejects_certificate_for_a_different_original_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_root = Path(__file__).parents[2] / "fixtures" / "network"
    certificate = fixture_root / "example-test-cert.pem"
    private_key = fixture_root / "example-test-key.pem"
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, private_key)
    client_context = ssl.create_default_context(cafile=str(certificate))

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(
        handler,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    port = int(server.sockets[0].getsockname()[1])
    try:
        with pytest.raises(OutboundHttpTransportFailed):
            await transport_with_test_trust(monkeypatch, client_context).send(
                transport_request(
                    port,
                    scheme=HttpScheme.HTTPS,
                    hostname="other.test",
                ),
                context(),
            )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_https_ignores_ambient_custom_trust_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_root = Path(__file__).parents[2] / "fixtures" / "network"
    certificate = fixture_root / "example-test-cert.pem"
    private_key = fixture_root / "example-test-key.pem"
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, private_key)

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(
        handler,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    port = int(server.sockets[0].getsockname()[1])
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    try:
        with pytest.raises(OutboundHttpTransportFailed):
            await AsyncioHttpTransport().send(
                transport_request(port, scheme=HttpScheme.HTTPS),
                context(),
            )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_transport_decodes_chunk_framing_without_publishing_trailers() -> None:
    response_bytes = (
        b"HTTP/1.1 200 OK\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"3\r\nabc\r\n"
        b"2\r\nde\r\n"
        b"0\r\nX-Trailer: ignored\r\n\r\n"
    )

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(response_bytes)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        response = await AsyncioHttpTransport().send(
            transport_request(port),
            context(),
        )
    finally:
        server.close()
        await server.wait_closed()

    assert response.body == b"abcde"
    assert response.wire_bytes == len(response_bytes)
    assert response.headers == (
        HttpHeader("Transfer-Encoding", "chunked"),
        HttpHeader("Connection", "close"),
    )


@pytest.mark.asyncio
async def test_transport_bounds_raw_and_informational_response_header_bytes() -> None:
    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 103 Early Hints\r\nX-A: value\r\n\r\n"
                b"HTTP/1.1 200 OK\r\n"
                b"X-B:                         value\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        with pytest.raises(OutboundHttpLimitExceeded):
            await AsyncioHttpTransport().send(
                transport_request(port, maximum_headers=40),
                context(),
            )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_transport_omits_reset_content_response_body() -> None:
    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 205 Reset Content\r\nContent-Length: 5\r\nConnection: close\r\n\r\nvalue"
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        response = await AsyncioHttpTransport().send(
            transport_request(port),
            context(),
        )
    finally:
        server.close()
        await server.wait_closed()

    assert response.status_code == 205
    assert response.body == b""


@pytest.mark.asyncio
async def test_transport_rejects_declared_body_over_limit_before_reading_it() -> None:
    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\n123456")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        with pytest.raises(OutboundHttpLimitExceeded):
            await AsyncioHttpTransport().send(
                transport_request(port, maximum_body=5),
                context(),
            )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_bytes",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: +2\r\nConnection: close\r\n\r\nok",
        b"HTTP/1.1 200 OK\r\nContent-Length: 2_0\r\nConnection: close\r\n\r\n" + (b"x" * 20),
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        b"0x2\r\nok\r\n0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        b" 2\r\nok\r\n0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        b"2;name=value\r\nok\r\n0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nBad Header: value\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 bad\x00reason\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: \xa01\xa0\r\n\r\nx",
    ],
    ids=[
        "signed-content-length",
        "underscored-content-length",
        "prefixed-chunk-size",
        "space-prefixed-chunk-size",
        "unsupported-chunk-extension",
        "invalid-header-name",
        "missing-reason-separator",
        "invalid-reason-control",
        "non-http-content-length-whitespace",
    ],
)
async def test_transport_rejects_non_strict_response_framing(
    response_bytes: bytes,
) -> None:
    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(response_bytes)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        with pytest.raises(OutboundHttpResponseInvalid):
            await AsyncioHttpTransport().send(
                transport_request(port),
                context(),
            )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_transport_rejects_pathological_content_length_stably() -> None:
    response_bytes = (
        b"HTTP/1.1 200 OK\r\nContent-Length: " + (b"1" * 5000) + b"\r\nConnection: close\r\n\r\n"
    )

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(response_bytes)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        with pytest.raises(OutboundHttpResponseInvalid) as captured:
            await AsyncioHttpTransport().send(
                transport_request(port, maximum_headers=6000, maximum_wire=7000),
                context(),
            )
    finally:
        server.close()
        await server.wait_closed()

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_chunk_framing_counts_against_response_wire_limit() -> None:
    response_head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
    size_line = (b"0" * 4093) + b"1\r\n"
    response_bytes = response_head + size_line + b"a\r\n" + size_line + b"b\r\n" + b"0\r\n\r\n"

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(response_bytes)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        with pytest.raises(OutboundHttpLimitExceeded):
            await AsyncioHttpTransport().send(
                transport_request(
                    port,
                    maximum_body=10,
                    maximum_wire=len(response_head) + 100,
                ),
                context(),
            )
    finally:
        server.close()
        await server.wait_closed()


class StalledCloseTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class StalledCloseWriter:
    def __init__(self, release: asyncio.Event) -> None:
        self.transport = StalledCloseTransport()
        self.closed = False
        self._release = release

    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        await self._release.wait()


@pytest.mark.asyncio
async def test_transport_deadline_does_not_wait_for_graceful_stream_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = asyncio.StreamReader()
    release = asyncio.Event()
    writer = StalledCloseWriter(release)

    async def open_connection(
        **_kwargs: object,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return reader, cast(asyncio.StreamWriter, writer)

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    deadline = asyncio.get_running_loop().time() + 0.02
    task = asyncio.create_task(
        AsyncioHttpTransport().send(
            transport_request(443),
            context(deadline=deadline),
        )
    )

    await asyncio.sleep(0.08)
    completed_by_deadline = task.done()
    release.set()
    with pytest.raises(OutboundHttpTimeout):
        await task

    assert completed_by_deadline
    assert writer.closed
    assert writer.transport.aborted


@pytest.mark.asyncio
async def test_transport_timeout_closes_one_attempt_with_stable_error() -> None:
    accepted = 0
    release = asyncio.Event()

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal accepted
        accepted += 1
        try:
            await reader.readuntil(b"\r\n\r\n")
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    deadline = asyncio.get_running_loop().time() + 0.02
    try:
        with pytest.raises(OutboundHttpTimeout) as captured:
            await AsyncioHttpTransport().send(
                transport_request(port),
                context(deadline=deadline),
            )
    finally:
        release.set()
        server.close()
        await server.wait_closed()

    assert accepted == 1
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_transport_rejects_malformed_http_without_retry() -> None:
    accepted = 0

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal accepted
        accepted += 1
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"NOT HTTP\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        with pytest.raises(OutboundHttpResponseInvalid):
            await AsyncioHttpTransport().send(
                transport_request(port),
                context(),
            )
    finally:
        server.close()
        await server.wait_closed()

    assert accepted == 1


@pytest.mark.asyncio
async def test_transport_honors_cancellation_before_socket_access() -> None:
    accepted = 0

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal accepted
        accepted += 1
        writer.close()
        await writer.wait_closed()

    server, port = await run_server(handler)
    try:
        with pytest.raises(OutboundHttpCancelled):
            await AsyncioHttpTransport().send(
                transport_request(port),
                context(cancellation=Cancellation(cancelled=True)),
            )
    finally:
        server.close()
        await server.wait_closed()

    assert accepted == 0


@pytest.mark.asyncio
async def test_system_resolver_returns_only_typed_unique_localhost_addresses() -> None:
    result = await SystemNetworkResolver().resolve("localhost", 80, context())

    assert result.hostname == "localhost"
    assert result.addresses
    assert len(result.addresses) == len(set(result.addresses))
    assert all(address.ip.is_loopback for address in result.addresses)
