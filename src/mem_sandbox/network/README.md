# Controlled network module

`mem_sandbox.network` owns the framework-neutral boundary for bounded outbound HTTP.
Milestone 9A implements the immutable contract, host authority binding, stable errors,
deterministic fake, and conformance driver. Milestone 9B adds strict URL normalization,
two-phase destination policy, controlled resolution, non-overridable IP safety,
redirect orchestration, and one direct bounded asyncio HTTP/1.1 transport. Credentials,
cumulative accounting, network-specific audit, commands, and framework tools remain
separate work.

## Boundary ownership

The module owns:

- HTTP/HTTPS and `GET`/`HEAD` domain values;
- immutable request, response, transfer-limit, usage, grant, and operation-context
  models;
- async gateway, destination-policy, resolver, and single-attempt transport interfaces;
- stable network-specific errors;
- the host-scoped `OutboundHttpBinding` and its fail-closed grant-narrowing rule;
- normalized, round-trip-safe IDNA host/origin-form URL values, exact/subdomain
  destination rules, and IPv4/IPv6 classification;
- `BoundedOutboundHttpGateway`, `SystemNetworkResolver`, and `AsyncioHttpTransport`;
- deterministic fake and conformance support in `mem_sandbox.network.testing`.

The sandbox service owns profile selection and construction. `SandboxSession` owns
operation serialization, lifecycle, high-level policy admission, deadlines,
cancellation, and operation events. Later network work owns credentials, cumulative
resource accounting, and network-specific audit events.

No agent SDK or third-party HTTP client is imported by this module. The concrete
transport uses direct asyncio streams, a verifying standard-library TLS context, and
one connection per admitted attempt. It never consults ambient proxy variables or owns
a cookie jar, cache, authentication retry, or model-selected TLS configuration.

## Host authority

`OutboundHttpBinding` combines a borrowed gateway with the maximum grant approved by the
host composition root. `with_grant()` returns an equal or narrower per-session view or
fails with `OutboundHttpDenied`.

Narrowing requires:

- the same opaque destination-policy identifier;
- method, scheme, and credential-route subsets;
- equal or lower transfer maxima;
- an equal or stronger event-delivery requirement.

The service's default `VirtualSandboxProfile` injects no binding into the session.
`ConnectedSandboxProfile` requires an explicit grant that fits the configured binding
ceiling. The profile is host configuration, not snapshot state. Resume always uses the
current request options; snapshots cannot enable networking or restore an old grant.

## Session integration

`SandboxSession.send_http()` is a typed host-facing operation. It:

1. rejects a missing capability or an out-of-grant method, scheme, limit, or route
   before gateway use;
2. performs the existing session operation gate, policy decision, deadline, event, and
   cancellation sequence with `OperationKind.OUTBOUND_HTTP`;
3. passes the effective grant and operation identity in a non-revealing
   `NetworkOperationContext`;
4. returns a complete bounded response with normal session result metadata.

The gateway deadline is the earlier of the admitted session collaborator deadline and
the request's host-bounded transfer timeout. Policy, resolver, and transport calls run
in independently timed child tasks created only after the current cancellation and
deadline checks pass. Cooperative cancellation observed before or after either policy
phase remains a cancellation rather than being reported as a policy denial. On timeout
or cancellation, a child that suppresses cancellation remains retained and observed but
cannot resume the request pipeline or publish a response after the deadline.

Session close cooperatively cancels an active HTTP operation and then waits for the
operation gate. Gateway cancellation settlement has a small bound within the remaining
terminal-event budget. If a gateway suppresses cancellation beyond that bound, the
session fails closed, retains and observes the task, and close re-signals it without
waiting indefinitely. The provider call runs in a distinct child task so provider
self-cancellation cannot impersonate orchestration cancellation. Settlement tracking is
installed before the bounded wait so repeated native cancellation cannot leave an
unfinished task unobserved. The gateway is borrowed and is never closed by the session.

## Destination-safe gateway

`BoundedOutboundHttpGateway` executes the following sequence for the initial target and
every redirect:

Before the sequence starts, the gateway reconstructs the request and operation context
into gateway-owned values. Policy, resolver, and transport collaborators receive only
detached copies of identifiers, grants, limits, address facts, cancellation views, and
single-attempt transport requests. Their mutations therefore cannot widen the
authoritative grant, alter a later redirect decision, or weaken response validation.
Identifier strings and every numeric transfer limit are canonicalized to exact built-in
primitives during model construction, so overloaded equality, hashing, arithmetic, or
comparison behavior cannot influence grant narrowing or policy lookup.

1. normalize one HTTP/HTTPS URL to an ASCII IDNA hostname, effective port, canonical
   origin, and origin-form target; reject Unicode labels whose built-in IDNA conversion
   performs compatibility or deletion mappings rather than an NFC/lowercase round trip,
   plus user information, fragments, legacy numeric host forms (including mixed dotted
   hexadecimal forms), malformed escapes, scope identifiers, raw or canonical URLs
   above the byte limit, malformed header controls, and controlled or sensitive request
   headers;
2. evaluate the selected focused policy before DNS;
3. classify an IP literal directly or invoke the injected resolver, reconstruct its
   hostname and address values into gateway-owned models, require an explicitly allowed
   final canonical hostname when one is reported, and recompute the classification of
   every unique final address;
4. deny the whole answer if any address is loopback, private, link-local, unspecified,
   multicast, reserved/non-global, deprecated site-local, transition/translation/mapped
   (including IETF protocol-assignment, AS112/AMT service, documentation, deprecated
   6bone, 6to4 relay anycast, standard NAT64, and both ISATAP interface-identifier
   ranges), or known metadata space including Azure WireServer;
5. evaluate a detached copy of the post-resolution facts, then pass exactly the first
   gateway-owned admitted numeric address and the original hostname to the transport;
6. enforce attempt, redirect, encoded-body, decompressed-body, transferred-wire-byte,
   header, cancellation, and deadline limits before publishing a complete response.

GET and HEAD retain their method across 301, 302, 303, 307, and 308 in this slice. A
redirect repeats normalization, policy, resolution, classification, and pinning.
Failure does not fall back to another resolved address: that would be an unaccounted
retry. Credential routes fail before policy or resolution until the destination-bound
credential slice is implemented.

## Resolver and transport

`SystemNetworkResolver` invokes only the host's stream-address resolver after
pre-resolution admission. It exposes the final canonical hostname when the platform
provides one and returns immutable typed addresses; it opens no connection.

`AsyncioHttpTransport` accepts only an `AdmittedHttpDestination`. It opens one direct
connection to the numeric address with `AI_NUMERICHOST`, sends HTTP/1.1 with
`Connection: close`, and preserves the original hostname in `Host`, TLS SNI, and normal
certificate hostname verification. It owns an environment-independent TLS context that
loads only system/compiled trust sources, exposes no custom trust or client-certificate
configuration, ignores `SSL_CERT_FILE`/`SSL_CERT_DIR`, enables strict/partial-chain
verification when supported, and accepts immutable Windows certificate-purpose
collections when loading system roots. It performs no address fallback, implicit retry,
redirect, proxy lookup, cookie/cache operation, or authentication negotiation.

The transport strictly parses response status, headers, content length, and chunk
framing, cumulatively bounds raw informational/final/trailer header bytes, bounds
encoded body bytes before allocation, counts every consumed response-head, chunk-line,
data, terminator, and trailer byte against the transferred-wire budget, and closes the
stream after each attempt. Content lengths allow only HTTP SP/HTAB whitespace and a
bounded ASCII decimal representation. The gateway supports identity, gzip, and deflate
decoding with a separate decompressed limit. Framing and sensitive response headers
such as `Set-Cookie` and authentication challenges are not published. Provisional
responses may be consumed internally but cannot cross the transport, gateway, grant, or
session boundary as a final result. Each transport result separates cumulative
logical header-budget bytes, raw header-field wire bytes, response-head/trailer wire
bytes, and encoded-body/framing wire bytes. The gateway revalidates their exact types,
cross-counter relationships, supported framing, minimum visible structure, total
equality, and per-attempt limits, including metadata absent from published headers.
For chunked bodies, the zero-chunk line belongs to body/framing wire usage while the
terminating trailer-section CRLF belongs to metadata wire usage.
Error, timeout, and cancellation cleanup aborts the stream immediately; successful
graceful shutdown remains bounded by the operation
deadline. HEAD and status-defined bodyless responses skip content decoding while
retaining safe header filtering.

## Fake, conformance, and local evidence

`FakeOutboundHttpGateway` returns scripted responses or failures, records exact calls,
and can block for timeout and cancellation tests. It opens no network connection.
`OutboundHttpGatewayConformanceDriver` provides reusable round-trip and stable-failure
probes. Destination and orchestration tests substitute deterministic policy, resolver,
and transport collaborators. Concrete HTTP/TLS tests use only controlled loopback
servers and a public test-only certificate; required tests never access the public
internet.

## Security invariants

- Networking is absent unless the host selects a connected profile.
- Models and grant ceilings are immutable.
- Model-facing input cannot choose a destination policy or add a credential reference.
- Request/context/response representations do not expose URLs, headers, bodies,
  hostnames, resolved addresses, credential routes, or grant details.
- Pre-resolution denial performs no DNS request. A prohibited or mixed resolution
  performs no transport access.
- Resolver output is rebuilt from exact plain hostname/address strings, so stored
  classifications or parsed-address objects cannot forge admission. Policy receives
  detached address facts and cannot mutate the gateway-owned peer after admission; the
  transport receives one numeric address and uses the original hostname only for
  HTTP/TLS identity.
- Ambient proxy variables, cookies, caches, client authentication, insecure TLS, and
  implicit retry are absent by construction.
- Every redirect repeats the full destination pipeline and remains within the original
  grant and request limits.
- Request headers are reconstructed from exact built-in strings before controlled-header
  filtering. Policy decisions are reconstructed as complete exact models, and malformed
  or behavior-bearing collaborator values fail closed without leaking provider errors.
- Resolver and transport contexts, policy facts, and transport requests are detached
  from gateway-owned authority. The gateway validates transport responses against its
  unexposed attempt limits rather than an object that the transport can mutate.
- `HttpTransferUsage` reports encoded response-body bytes separately from exact consumed
  response-wire bytes; transferred limits use request-body plus response-wire usage.
- A public response always has a final status from 200 through 599.
- The gateway reconstructs and revalidates exact transport response primitives, nested
  header name/value invariants, final status, structural header/metadata/body counters,
  and total wire-byte equality instead of trusting collaborator object construction.
  Header strings are snapshotted as exact built-in `str` values before behavior such as
  filtering or encoding.
- Gateway and protected-input failures retain no provider exception context, cause, or
  raw settlement note at the session boundary.
- Collaborator-supplied cancellation values are accepted only while the operation's
  cooperative cancellation signal is active; otherwise policy, resolution, and
  transport fail closed in their own stable categories.
- Provider failures, including a task-raised `GeneratorExit` and nested exception groups,
  become context-free `OutboundHttpGatewayFailed` values. During cancellation settlement,
  provider-task failures remain protected secondary failures.
- `GeneratorExit` delivered directly into a session-owned coroutine, plus bare
  `KeyboardInterrupt` and `SystemExit`, pass through unchanged. If one escapes after
  operation start, the session does not manufacture a terminal event.
- Gateway-raised session-domain errors cannot forge session classifications or operation
  identities; provider translation wraps a distinct child task at the session boundary.
- Resolution, transport/TLS, and malformed-response failures retain distinct stable
  domain codes while provider details are removed at the session boundary.
- Library contracts do not contain arbitrary guest code.

## Maintenance

Changes to methods, schemes, normalization, destination rules, address classification,
CNAME/canonical-host handling, resolver behavior, peer pinning, redirects, proxy/TLS
behavior, framing/decoding, grant ordering, limits, context contents, error codes,
profile binding, snapshot authority, lifecycle cancellation, or gateway conformance
must update this README, the detailed controlled-egress design, and adversarial behavior
tests in the same change.
