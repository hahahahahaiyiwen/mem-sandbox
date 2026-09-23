"""Destination normalization, classification, and focused HTTP policy."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
)
from typing import cast
from unicodedata import normalize
from urllib.parse import quote, urlsplit

from mem_sandbox.core import OperationId, SessionId
from mem_sandbox.network.errors import OutboundHttpRequestInvalid
from mem_sandbox.network.models import HttpMethod, HttpScheme, NetworkPolicyId

_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_AMBIGUOUS_NUMERIC_HOST = re.compile(
    r"(?:[0-9]+|0[xX][0-9A-Fa-f]+)"
    r"(?:\.(?:[0-9]+|0[xX][0-9A-Fa-f]+))*\Z"
)
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_MAX_HOSTNAME_BYTES = 253
_MAX_URL_BYTES = 8192

_PRIVATE_V4 = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
_PRIVATE_V6 = (
    IPv6Network("fc00::/7"),
    IPv6Network("fec0::/10"),
)
_METADATA_NETWORKS = (
    IPv4Network("168.63.129.16/32"),
    IPv4Network("169.254.169.254/32"),
    IPv4Network("169.254.170.2/32"),
    IPv4Network("100.100.100.200/32"),
    IPv4Network("192.0.0.192/32"),
    IPv6Network("fd00:ec2::254/128"),
    IPv6Network("fe80::a9fe:a9fe/128"),
)
_SPECIAL_PURPOSE_NETWORKS = (
    IPv4Network("192.0.0.0/24"),
    IPv4Network("192.31.196.0/24"),
    IPv4Network("192.52.193.0/24"),
    IPv4Network("192.175.48.0/24"),
    IPv4Network("192.88.99.0/24"),
    IPv6Network("64:ff9b::/96"),
    IPv6Network("64:ff9b:1::/48"),
    IPv6Network("2001::/23"),
    IPv6Network("2002::/16"),
    IPv6Network("2620:4f:8000::/48"),
    IPv6Network("3ffe::/16"),
)
_ISATAP_INTERFACE_PREFIXES = frozenset((0x00005EFE, 0x02005EFE))


class HttpRequestClass(StrEnum):
    BASELINE = "baseline"


class IpAddressClass(StrEnum):
    GLOBAL = "global"
    UNSPECIFIED = "unspecified"
    LOOPBACK = "loopback"
    PRIVATE = "private"
    LINK_LOCAL = "link_local"
    MULTICAST = "multicast"
    RESERVED = "reserved"
    METADATA = "metadata"


class NetworkPolicyPhase(StrEnum):
    PRE_RESOLUTION = "pre_resolution"
    POST_RESOLUTION = "post_resolution"


class NetworkPolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class NetworkPolicyReason(StrEnum):
    ALLOWED = "allowed"
    POLICY_NOT_FOUND = "policy_not_found"
    DESTINATION_NOT_ALLOWED = "destination_not_allowed"


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedHttpAddress:
    value: str
    classification: IpAddressClass = field(init=False)
    _ip: IPv4Address | IPv6Address = field(init=False, repr=False)

    def __post_init__(self) -> None:
        value = cast(object, self.value)
        if not isinstance(value, str):
            raise TypeError("resolved address must be a string")
        if "%" in value:
            raise ValueError("resolved address must not contain a scope identifier")
        try:
            parsed = ip_address(value)
        except ValueError:
            raise ValueError("resolved address must be an IPv4 or IPv6 address") from None
        object.__setattr__(self, "value", parsed.compressed)
        object.__setattr__(self, "_ip", parsed)
        object.__setattr__(self, "classification", classify_ip_address(parsed))

    @property
    def ip(self) -> IPv4Address | IPv6Address:
        return self._ip

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(value=<redacted>, classification={self.classification.value!r})"
        )


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class NormalizedHttpUrl:
    scheme: HttpScheme
    hostname: str
    port: int
    target: str
    canonical_url: str

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.scheme), HttpScheme):
            raise TypeError("scheme must be HttpScheme")
        if not isinstance(cast(object, self.hostname), str) or not self.hostname:
            raise TypeError("hostname must be a non-empty string")
        port = cast(object, self.port)
        if isinstance(port, bool) or not isinstance(port, int):
            raise TypeError("port must be an integer")
        if self.port < 1 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if not isinstance(cast(object, self.target), str) or not self.target.startswith("/"):
            raise TypeError("target must be an origin-form string")
        if not isinstance(cast(object, self.canonical_url), str):
            raise TypeError("canonical_url must be a string")

    @property
    def authority(self) -> str:
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        default_port = 443 if self.scheme is HttpScheme.HTTPS else 80
        return host if self.port == default_port else f"{host}:{self.port}"

    @property
    def ip_literal(self) -> ResolvedHttpAddress | None:
        try:
            return ResolvedHttpAddress(self.hostname)
        except ValueError:
            return None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(scheme={self.scheme.value!r}, "
            "hostname=<redacted>, port=<redacted>, "
            "target=<redacted>, canonical_url=<redacted>)"
        )


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class NetworkResolution:
    hostname: str
    addresses: tuple[ResolvedHttpAddress, ...]
    canonical_hostname: str | None = None

    def __post_init__(self) -> None:
        hostname = _normalize_hostname(self.hostname)
        object.__setattr__(self, "hostname", hostname)
        addresses = cast(object, self.addresses)
        if not isinstance(addresses, tuple):
            raise TypeError("addresses must be a tuple")
        raw_addresses = cast(tuple[object, ...], addresses)
        if any(not isinstance(address, ResolvedHttpAddress) for address in raw_addresses):
            raise TypeError("addresses must contain ResolvedHttpAddress values")
        unique = tuple(dict.fromkeys(cast(tuple[ResolvedHttpAddress, ...], addresses)))
        object.__setattr__(self, "addresses", unique)
        canonical = cast(object, self.canonical_hostname)
        if canonical is not None:
            if not isinstance(canonical, str):
                raise TypeError("canonical_hostname must be a string or None")
            object.__setattr__(self, "canonical_hostname", _normalize_hostname(canonical))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(hostname=<redacted>, "
            "addresses=<redacted>, canonical_hostname=<redacted>)"
        )


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class NetworkPolicyRequest:
    policy_id: NetworkPolicyId
    session_id: SessionId
    operation_id: OperationId
    phase: NetworkPolicyPhase
    method: HttpMethod
    request_class: HttpRequestClass
    scheme: HttpScheme
    hostname: str
    port: int
    redirect_depth: int
    addresses: tuple[ResolvedHttpAddress, ...]
    canonical_hostname: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.policy_id), NetworkPolicyId):
            raise TypeError("policy_id must be NetworkPolicyId")
        if not isinstance(cast(object, self.session_id), SessionId):
            raise TypeError("session_id must be SessionId")
        if not isinstance(cast(object, self.operation_id), OperationId):
            raise TypeError("operation_id must be OperationId")
        for name, enum_type in (
            ("phase", NetworkPolicyPhase),
            ("method", HttpMethod),
            ("request_class", HttpRequestClass),
            ("scheme", HttpScheme),
        ):
            if not isinstance(cast(object, getattr(self, name)), enum_type):
                raise TypeError(f"{name} must be {enum_type.__name__}")
        object.__setattr__(self, "hostname", _normalize_hostname(self.hostname))
        port = cast(object, self.port)
        if isinstance(port, bool) or not isinstance(port, int):
            raise TypeError("port must be an integer")
        if self.port < 1 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        redirect_depth = cast(object, self.redirect_depth)
        if isinstance(redirect_depth, bool) or not isinstance(redirect_depth, int):
            raise TypeError("redirect_depth must be an integer")
        if self.redirect_depth < 0:
            raise ValueError("redirect_depth must not be negative")
        addresses = cast(object, self.addresses)
        if not isinstance(addresses, tuple):
            raise TypeError("addresses must be a tuple")
        if any(
            not isinstance(address, ResolvedHttpAddress)
            for address in cast(tuple[object, ...], addresses)
        ):
            raise TypeError("addresses must contain ResolvedHttpAddress values")
        canonical = cast(object, self.canonical_hostname)
        if canonical is not None:
            if not isinstance(canonical, str):
                raise TypeError("canonical_hostname must be a string or None")
            object.__setattr__(self, "canonical_hostname", _normalize_hostname(canonical))
        if self.phase is NetworkPolicyPhase.PRE_RESOLUTION and (
            self.addresses or self.canonical_hostname is not None
        ):
            raise ValueError("pre-resolution policy facts must not contain resolution results")
        if self.phase is NetworkPolicyPhase.POST_RESOLUTION and not self.addresses:
            raise ValueError("post-resolution policy facts require addresses")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(policy_id=<redacted>, "
            f"session_id={self.session_id!r}, operation_id={self.operation_id!r}, "
            f"phase={self.phase.value!r}, method={self.method.value!r}, "
            f"request_class={self.request_class.value!r}, scheme={self.scheme.value!r}, "
            "hostname=<redacted>, port=<redacted>, "
            f"redirect_depth={self.redirect_depth!r}, addresses=<redacted>, "
            "canonical_hostname=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class NetworkPolicyDecision:
    outcome: NetworkPolicyOutcome
    reason: NetworkPolicyReason

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.outcome), NetworkPolicyOutcome):
            raise TypeError("outcome must be NetworkPolicyOutcome")
        if not isinstance(cast(object, self.reason), NetworkPolicyReason):
            raise TypeError("reason must be NetworkPolicyReason")
        if (
            self.outcome is NetworkPolicyOutcome.ALLOW
            and self.reason is not NetworkPolicyReason.ALLOWED
        ):
            raise ValueError("allow decisions require the allowed reason")
        if self.outcome is NetworkPolicyOutcome.DENY and self.reason is NetworkPolicyReason.ALLOWED:
            raise ValueError("deny decisions require a denial reason")

    @classmethod
    def allow(cls) -> NetworkPolicyDecision:
        return cls(NetworkPolicyOutcome.ALLOW, NetworkPolicyReason.ALLOWED)

    @classmethod
    def deny(cls, reason: NetworkPolicyReason) -> NetworkPolicyDecision:
        return cls(NetworkPolicyOutcome.DENY, reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class HttpDestinationRule:
    hostname: str
    schemes: tuple[HttpScheme, ...]
    ports: tuple[int, ...]
    methods: tuple[HttpMethod, ...]
    include_subdomains: bool = False
    canonical_hostnames: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        hostname = _normalize_hostname(self.hostname)
        object.__setattr__(self, "hostname", hostname)
        object.__setattr__(self, "schemes", _enum_tuple("schemes", self.schemes, HttpScheme))
        object.__setattr__(self, "methods", _enum_tuple("methods", self.methods, HttpMethod))
        ports = cast(object, self.ports)
        if not isinstance(ports, tuple):
            raise TypeError("ports must be a tuple")
        raw_ports = cast(tuple[object, ...], ports)
        if any(
            isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535
            for port in raw_ports
        ):
            raise ValueError("ports must contain integers between 1 and 65535")
        ordered_ports = tuple(sorted(cast(tuple[int, ...], ports)))
        if not ordered_ports:
            raise ValueError("ports must not be empty")
        if len(set(ordered_ports)) != len(ordered_ports):
            raise ValueError("ports must not contain duplicates")
        object.__setattr__(self, "ports", ordered_ports)
        if not isinstance(cast(object, self.include_subdomains), bool):
            raise TypeError("include_subdomains must be bool")
        if self.include_subdomains and _try_ip_address(hostname) is not None:
            raise ValueError("IP destination rules cannot include subdomains")
        canonical_hostnames = cast(object, self.canonical_hostnames)
        if not isinstance(canonical_hostnames, tuple):
            raise TypeError("canonical_hostnames must be a tuple")
        raw_canonical = cast(tuple[object, ...], canonical_hostnames)
        if any(not isinstance(item, str) for item in raw_canonical):
            raise TypeError("canonical_hostnames must contain strings")
        normalized_canonical = tuple(
            sorted(_normalize_hostname(item) for item in cast(tuple[str, ...], canonical_hostnames))
        )
        if len(set(normalized_canonical)) != len(normalized_canonical):
            raise ValueError("canonical_hostnames must not contain duplicates")
        object.__setattr__(self, "canonical_hostnames", normalized_canonical)

    def matches(self, request: NetworkPolicyRequest) -> bool:
        hostname_matches = request.hostname == self.hostname or (
            self.include_subdomains and request.hostname.endswith(f".{self.hostname}")
        )
        canonical_matches = (
            request.canonical_hostname is None
            or request.canonical_hostname == request.hostname
            or request.canonical_hostname in self.canonical_hostnames
        )
        return (
            hostname_matches
            and canonical_matches
            and request.scheme in self.schemes
            and request.port in self.ports
            and request.method in self.methods
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StaticNetworkPolicy:
    policy_id: NetworkPolicyId
    rules: tuple[HttpDestinationRule, ...]

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.policy_id), NetworkPolicyId):
            raise TypeError("policy_id must be NetworkPolicyId")
        rules = cast(object, self.rules)
        if not isinstance(rules, tuple):
            raise TypeError("rules must be a tuple")
        if any(
            not isinstance(rule, HttpDestinationRule) for rule in cast(tuple[object, ...], rules)
        ):
            raise TypeError("rules must contain HttpDestinationRule values")
        if not self.rules:
            raise ValueError("rules must not be empty")


class StaticNetworkPolicyEngine:
    """Fail-closed exact-host and explicit-subdomain destination policy."""

    def __init__(self, policies: tuple[StaticNetworkPolicy, ...]) -> None:
        value = cast(object, policies)
        if not isinstance(value, tuple):
            raise TypeError("policies must be a tuple")
        raw = cast(tuple[object, ...], value)
        if any(not isinstance(policy, StaticNetworkPolicy) for policy in raw):
            raise TypeError("policies must contain StaticNetworkPolicy values")
        if len({policy.policy_id for policy in policies}) != len(policies):
            raise ValueError("policies must not contain duplicate identifiers")
        self._policies = {policy.policy_id: policy for policy in policies}

    async def evaluate(self, request: NetworkPolicyRequest) -> NetworkPolicyDecision:
        if not isinstance(cast(object, request), NetworkPolicyRequest):
            raise TypeError("request must be NetworkPolicyRequest")
        policy = self._policies.get(request.policy_id)
        if policy is None:
            return NetworkPolicyDecision.deny(NetworkPolicyReason.POLICY_NOT_FOUND)
        if any(rule.matches(request) for rule in policy.rules):
            return NetworkPolicyDecision.allow()
        return NetworkPolicyDecision.deny(NetworkPolicyReason.DESTINATION_NOT_ALLOWED)


def normalize_http_url(url: str) -> NormalizedHttpUrl:
    value = cast(object, url)
    if not isinstance(value, str):
        raise TypeError("url must be a string")
    try:
        url_size = len(url.encode("utf-8"))
    except UnicodeEncodeError:
        raise OutboundHttpRequestInvalid("HTTP URL is not valid Unicode") from None
    if (
        not url
        or url_size > _MAX_URL_BYTES
        or "\\" in url
        or "#" in url
        or any(char.isspace() for char in url)
    ):
        raise OutboundHttpRequestInvalid("HTTP URL contains an unsupported character")
    try:
        split = urlsplit(url)
        scheme = HttpScheme(split.scheme.lower())
        hostname_value = split.hostname
        port_value = split.port
    except (UnicodeError, ValueError):
        raise OutboundHttpRequestInvalid("HTTP URL authority is invalid") from None
    if scheme not in (HttpScheme.HTTP, HttpScheme.HTTPS):
        raise OutboundHttpRequestInvalid("HTTP URL scheme is unsupported")
    if not split.netloc or hostname_value is None:
        raise OutboundHttpRequestInvalid("HTTP URL must contain a hostname")
    if split.username is not None or split.password is not None:
        raise OutboundHttpRequestInvalid("HTTP URL user information is not supported")
    if split.netloc.endswith(":"):
        raise OutboundHttpRequestInvalid("HTTP URL port is empty")
    hostname = _normalize_hostname(hostname_value)
    port = port_value if port_value is not None else (443 if scheme is HttpScheme.HTTPS else 80)
    if port < 1 or port > 65535:
        raise OutboundHttpRequestInvalid("HTTP URL port is outside the supported range")
    path = _normalize_url_component(split.path or "/", safe="/:@!$&'()*+,;=-._~%")
    if path.startswith("//"):
        raise OutboundHttpRequestInvalid("HTTP URL path must use unambiguous origin form")
    query = _normalize_url_component(split.query, safe="/?:@!$&'()*+,;=-._~%")
    target = path if not query else f"{path}?{query}"
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme is HttpScheme.HTTPS else 80
    authority = host if port == default_port else f"{host}:{port}"
    canonical_url = f"{scheme.value}://{authority}{target}"
    if len(canonical_url.encode("ascii")) > _MAX_URL_BYTES:
        raise OutboundHttpRequestInvalid("HTTP URL exceeds its canonical byte limit")
    return NormalizedHttpUrl(
        scheme=scheme,
        hostname=hostname,
        port=port,
        target=target,
        canonical_url=canonical_url,
    )


def classify_ip_address(address: IPv4Address | IPv6Address) -> IpAddressClass:
    if not isinstance(cast(object, address), IPv4Address | IPv6Address):
        raise TypeError("address must be IPv4Address or IPv6Address")
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        mapped = classify_ip_address(address.ipv4_mapped)
        return IpAddressClass.RESERVED if mapped is IpAddressClass.GLOBAL else mapped
    if any(address in network for network in _METADATA_NETWORKS):
        return IpAddressClass.METADATA
    if address.is_unspecified:
        return IpAddressClass.UNSPECIFIED
    if address.is_loopback:
        return IpAddressClass.LOOPBACK
    if address.is_link_local:
        return IpAddressClass.LINK_LOCAL
    if address.is_multicast:
        return IpAddressClass.MULTICAST
    if isinstance(address, IPv4Address) and any(address in network for network in _PRIVATE_V4):
        return IpAddressClass.PRIVATE
    if isinstance(address, IPv6Address) and any(address in network for network in _PRIVATE_V6):
        return IpAddressClass.PRIVATE
    if any(address in network for network in _SPECIAL_PURPOSE_NETWORKS):
        return IpAddressClass.RESERVED
    if isinstance(address, IPv6Address):
        interface_prefix = (int(address) & ((1 << 64) - 1)) >> 32
        if interface_prefix in _ISATAP_INTERFACE_PREFIXES:
            return IpAddressClass.RESERVED
    if address.is_reserved or not address.is_global:
        return IpAddressClass.RESERVED
    return IpAddressClass.GLOBAL


def _normalize_hostname(hostname: object) -> str:
    if not isinstance(hostname, str):
        raise TypeError("hostname must be a string")
    if not hostname or "%" in hostname or any(char.isspace() for char in hostname):
        raise OutboundHttpRequestInvalid("HTTP hostname is invalid")
    parsed = _try_ip_address(hostname)
    if parsed is not None:
        return parsed.compressed
    candidate = hostname[:-1] if hostname.endswith(".") else hostname
    if (
        not candidate
        or candidate.endswith(".")
        or _AMBIGUOUS_NUMERIC_HOST.fullmatch(candidate) is not None
    ):
        raise OutboundHttpRequestInvalid("HTTP hostname form is ambiguous")
    try:
        labels: list[str] = []
        for label in candidate.split("."):
            normalized_label = normalize("NFC", label).lower()
            encoded_label = normalized_label.encode("idna").decode("ascii").lower()
            if not label.isascii():
                round_trip = normalize(
                    "NFC",
                    encoded_label.encode("ascii").decode("idna"),
                ).lower()
                if round_trip != normalized_label:
                    raise UnicodeError
            labels.append(encoded_label)
    except UnicodeError:
        raise OutboundHttpRequestInvalid("HTTP hostname IDNA encoding failed") from None
    if any(
        not label or len(label) > 63 or _HOST_LABEL.fullmatch(label) is None for label in labels
    ):
        raise OutboundHttpRequestInvalid("HTTP hostname label is invalid")
    canonical = ".".join(labels)
    if len(canonical.encode("ascii")) > _MAX_HOSTNAME_BYTES:
        raise OutboundHttpRequestInvalid("HTTP hostname exceeds its byte limit")
    return canonical


def _normalize_url_component(value: str, *, safe: str) -> str:
    index = 0
    while index < len(value):
        if value[index] == "%":
            if _PERCENT_ESCAPE.match(value, index) is None:
                raise OutboundHttpRequestInvalid("HTTP URL contains an invalid percent escape")
            index += 3
            continue
        index += 1
    try:
        encoded = quote(value, safe=safe, encoding="utf-8", errors="strict")
    except UnicodeError:
        raise OutboundHttpRequestInvalid("HTTP URL component is not valid Unicode") from None
    return _PERCENT_ESCAPE.sub(lambda match: match.group(0).upper(), encoded)


def _try_ip_address(value: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(value)
    except ValueError:
        return None


def _enum_tuple[EnumT: StrEnum](
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
    if not ordered:
        raise ValueError(f"{name} must not be empty")
    if len(set(ordered)) != len(ordered):
        raise ValueError(f"{name} must not contain duplicates")
    return ordered
