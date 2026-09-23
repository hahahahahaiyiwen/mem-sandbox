from dataclasses import FrozenInstanceError
from ipaddress import IPv6Address, IPv6Network, ip_address
from uuid import UUID

import pytest

from mem_sandbox.core import OperationId, SessionId
from mem_sandbox.network import (
    HttpDestinationRule,
    HttpMethod,
    HttpRequestClass,
    HttpScheme,
    IpAddressClass,
    NetworkPolicyDecision,
    NetworkPolicyId,
    NetworkPolicyOutcome,
    NetworkPolicyPhase,
    NetworkPolicyRequest,
    NormalizedHttpUrl,
    ResolvedHttpAddress,
    StaticNetworkPolicy,
    StaticNetworkPolicyEngine,
    classify_ip_address,
    normalize_http_url,
)
from mem_sandbox.network.errors import OutboundHttpRequestInvalid


@pytest.mark.parametrize(
    ("raw", "scheme", "hostname", "port", "target", "canonical"),
    [
        (
            "HTTPS://BÜCHER.Example.:443/a%2fb?q=é",
            HttpScheme.HTTPS,
            "xn--bcher-kva.example",
            443,
            "/a%2Fb?q=%C3%A9",
            "https://xn--bcher-kva.example/a%2Fb?q=%C3%A9",
        ),
        (
            "http://[2001:4860:4860:0:0:0:0:8888]:8080/value",
            HttpScheme.HTTP,
            "2001:4860:4860::8888",
            8080,
            "/value",
            "http://[2001:4860:4860::8888]:8080/value",
        ),
        (
            "https://example.test",
            HttpScheme.HTTPS,
            "example.test",
            443,
            "/",
            "https://example.test/",
        ),
        (
            "https://xn--fa-hia.de/value",
            HttpScheme.HTTPS,
            "xn--fa-hia.de",
            443,
            "/value",
            "https://xn--fa-hia.de/value",
        ),
    ],
)
def test_url_normalization_produces_one_comparison_and_transport_form(
    raw: str,
    scheme: HttpScheme,
    hostname: str,
    port: int,
    target: str,
    canonical: str,
) -> None:
    normalized = normalize_http_url(raw)

    assert normalized.scheme is scheme
    assert normalized.hostname == hostname
    assert normalized.port == port
    assert normalized.target == target
    assert normalized.canonical_url == canonical
    assert raw not in repr(normalized)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/value",
        "https://user@example.test/value",
        "https://:password@example.test/value",
        "https://example.test/value#fragment",
        "https://example.test/a\\b",
        "https://example.test/a b",
        "https://example.test/%GG",
        "https://[fe80::1%25eth0]/value",
        "https://example.test:0/value",
        "https://example.test:65536/value",
        "https://example.test:/value",
        "https://exa_mple.test/value",
        "https://127.1/value",
        "https://2130706433/value",
        "https://0x7f000001/value",
        "https://0x7f.0.0.1/value",
        "https://0x7f.0x0.0x0.0x1/value",
        "https://127.0.0x0.1/value",
        "https://faß.de/value",
        "https://ab\u200dcd.example/value",
        "https:///value",
    ],
)
def test_url_normalization_rejects_ambiguous_or_unsupported_forms(url: str) -> None:
    with pytest.raises(OutboundHttpRequestInvalid):
        normalize_http_url(url)


def test_url_normalization_rejects_canonical_percent_expansion_over_limit() -> None:
    with pytest.raises(OutboundHttpRequestInvalid):
        normalize_http_url(f"https://example.test/{'é' * 1400}")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("8.8.8.8", IpAddressClass.GLOBAL),
        ("0.0.0.0", IpAddressClass.UNSPECIFIED),
        ("127.0.0.1", IpAddressClass.LOOPBACK),
        ("10.0.0.1", IpAddressClass.PRIVATE),
        ("169.254.1.1", IpAddressClass.LINK_LOCAL),
        ("224.0.0.1", IpAddressClass.MULTICAST),
        ("240.0.0.1", IpAddressClass.RESERVED),
        ("192.88.99.1", IpAddressClass.RESERVED),
        ("168.63.129.16", IpAddressClass.METADATA),
        ("169.254.169.254", IpAddressClass.METADATA),
        ("2001:4860:4860::8888", IpAddressClass.GLOBAL),
        ("::", IpAddressClass.UNSPECIFIED),
        ("::1", IpAddressClass.LOOPBACK),
        ("fc00::1", IpAddressClass.PRIVATE),
        ("fec0::1", IpAddressClass.PRIVATE),
        ("fec0:0:0:ffff::1", IpAddressClass.PRIVATE),
        ("fe80::1", IpAddressClass.LINK_LOCAL),
        ("ff02::1", IpAddressClass.MULTICAST),
        ("100::1", IpAddressClass.RESERVED),
        ("fd00:ec2::254", IpAddressClass.METADATA),
    ],
)
def test_address_classification_is_exact_for_ipv4_and_ipv6(
    value: str,
    expected: IpAddressClass,
) -> None:
    assert classify_ip_address(ip_address(value)) is expected
    assert ResolvedHttpAddress(value).classification is expected


def test_ipv4_mapped_ipv6_cannot_bypass_private_or_metadata_classification() -> None:
    assert classify_ip_address(ip_address("::ffff:127.0.0.1")) is IpAddressClass.LOOPBACK
    assert classify_ip_address(ip_address("::ffff:168.63.129.16")) is IpAddressClass.METADATA
    assert classify_ip_address(ip_address("::ffff:169.254.169.254")) is IpAddressClass.METADATA
    assert classify_ip_address(ip_address("::ffff:8.8.8.8")) is IpAddressClass.RESERVED


@pytest.mark.parametrize(
    "value",
    [
        "2001::1",
        "2002:0808:0808::1",
        "64:ff9b::808:808",
        "64:ff9b:1::808:808",
        "2001:4860:0:0:0:5efe:a00:1",
        "2001:4860:0:0:200:5efe:a00:1",
    ],
)
def test_ipv6_transition_and_translation_ranges_are_denied(value: str) -> None:
    assert classify_ip_address(ip_address(value)) is IpAddressClass.RESERVED


@pytest.mark.parametrize(
    "value",
    [
        "192.0.0.9",
        "192.0.0.10",
        "2001:1::1",
        "2001:1::2",
        "2001:3::1",
        "2001:4:112::1",
        "2001:20::1",
        "2001:30::1",
        "192.31.196.1",
        "192.52.193.1",
        "192.175.48.1",
        "2620:4f:8000::1",
        "3ffe::1",
        "3fff::1",
    ],
)
def test_ietf_protocol_assignment_exceptions_are_denied(value: str) -> None:
    assert classify_ip_address(ip_address(value)) is IpAddressClass.RESERVED


def test_documentation_prefix_denial_is_independent_of_stdlib_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = IPv6Network("3fff::/20")
    original_global = IPv6Address.is_global
    original_reserved = IPv6Address.is_reserved

    def is_global(address: IPv6Address) -> bool:
        if address in prefix:
            return True
        return bool(original_global.__get__(address, IPv6Address))

    def is_reserved(address: IPv6Address) -> bool:
        if address in prefix:
            return False
        return bool(original_reserved.__get__(address, IPv6Address))

    monkeypatch.setattr(IPv6Address, "is_global", property(is_global))
    monkeypatch.setattr(IPv6Address, "is_reserved", property(is_reserved))

    assert classify_ip_address(IPv6Address("3fff::1")) is IpAddressClass.RESERVED


def _policy_request(
    hostname: str,
    *,
    policy_id: str = "docs",
    scheme: HttpScheme = HttpScheme.HTTPS,
    port: int = 443,
    method: HttpMethod = HttpMethod.GET,
    phase: NetworkPolicyPhase = NetworkPolicyPhase.PRE_RESOLUTION,
    canonical_hostname: str | None = None,
) -> NetworkPolicyRequest:
    return NetworkPolicyRequest(
        policy_id=NetworkPolicyId(policy_id),
        session_id=SessionId(UUID(int=1)),
        operation_id=OperationId(UUID(int=2)),
        phase=phase,
        method=method,
        request_class=HttpRequestClass.BASELINE,
        scheme=scheme,
        hostname=hostname,
        port=port,
        redirect_depth=0,
        addresses=(
            () if phase is NetworkPolicyPhase.PRE_RESOLUTION else (ResolvedHttpAddress("8.8.8.8"),)
        ),
        canonical_hostname=canonical_hostname,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_request", "expected"),
    [
        (_policy_request("example.test"), NetworkPolicyOutcome.ALLOW),
        (_policy_request("api.example.test"), NetworkPolicyOutcome.DENY),
        (_policy_request("sub.allowed.test"), NetworkPolicyOutcome.ALLOW),
        (_policy_request("allowed.test"), NetworkPolicyOutcome.ALLOW),
        (_policy_request("badallowed.test"), NetworkPolicyOutcome.DENY),
        (
            _policy_request("example.test", scheme=HttpScheme.HTTP, port=80),
            NetworkPolicyOutcome.DENY,
        ),
        (_policy_request("example.test", port=8443), NetworkPolicyOutcome.DENY),
        (
            _policy_request("example.test", method=HttpMethod.HEAD),
            NetworkPolicyOutcome.DENY,
        ),
        (_policy_request("example.test", policy_id="missing"), NetworkPolicyOutcome.DENY),
    ],
)
async def test_static_policy_uses_exact_host_subdomain_scheme_port_and_method_rules(
    policy_request: NetworkPolicyRequest,
    expected: NetworkPolicyOutcome,
) -> None:
    engine = StaticNetworkPolicyEngine(
        (
            StaticNetworkPolicy(
                policy_id=NetworkPolicyId("docs"),
                rules=(
                    HttpDestinationRule(
                        hostname="example.test",
                        schemes=(HttpScheme.HTTPS,),
                        ports=(443,),
                        methods=(HttpMethod.GET,),
                    ),
                    HttpDestinationRule(
                        hostname="allowed.test",
                        include_subdomains=True,
                        schemes=(HttpScheme.HTTPS,),
                        ports=(443,),
                        methods=(HttpMethod.GET,),
                    ),
                ),
            ),
        )
    )

    decision = await engine.evaluate(policy_request)

    assert decision.outcome is expected


@pytest.mark.asyncio
async def test_static_policy_requires_explicit_canonical_hostname_authority() -> None:
    engine = StaticNetworkPolicyEngine(
        (
            StaticNetworkPolicy(
                policy_id=NetworkPolicyId("docs"),
                rules=(
                    HttpDestinationRule(
                        hostname="example.test",
                        canonical_hostnames=("edge.example.test",),
                        schemes=(HttpScheme.HTTPS,),
                        ports=(443,),
                        methods=(HttpMethod.GET,),
                    ),
                ),
            ),
        )
    )

    approved = await engine.evaluate(
        _policy_request(
            "example.test",
            phase=NetworkPolicyPhase.POST_RESOLUTION,
            canonical_hostname="EDGE.example.test.",
        )
    )
    denied = await engine.evaluate(
        _policy_request(
            "example.test",
            phase=NetworkPolicyPhase.POST_RESOLUTION,
            canonical_hostname="unapproved.example.test",
        )
    )

    assert approved.outcome is NetworkPolicyOutcome.ALLOW
    assert denied.outcome is NetworkPolicyOutcome.DENY


def test_destination_policy_values_are_immutable_and_non_revealing() -> None:
    normalized = normalize_http_url("https://example.test/private?q=secret")
    address = ResolvedHttpAddress("8.8.8.8")
    decision = NetworkPolicyDecision.allow()

    assert isinstance(normalized, NormalizedHttpUrl)
    assert decision.outcome is NetworkPolicyOutcome.ALLOW
    assert "example.test" not in repr(normalized)
    assert "private" not in repr(normalized)
    assert "secret" not in repr(normalized)
    assert "8.8.8.8" not in repr(address)
    with pytest.raises(FrozenInstanceError):
        normalized.hostname = "other.test"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        address.value = "1.1.1.1"  # type: ignore[misc]
