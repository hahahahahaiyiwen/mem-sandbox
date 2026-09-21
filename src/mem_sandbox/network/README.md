# Controlled network module

`mem_sandbox.network` owns the framework-neutral boundary for bounded outbound HTTP.
Milestone 9A implements the immutable contract, host authority binding, stable errors,
deterministic fake, and conformance driver. It does not implement DNS, sockets, TLS,
redirects, proxies, credentials, or a real HTTP client.

## Boundary ownership

The module owns:

- HTTP/HTTPS and `GET`/`HEAD` domain values;
- immutable request, response, transfer-limit, usage, grant, and operation-context
  models;
- the async `OutboundHttpGateway` interface;
- stable network-specific errors;
- the host-scoped `OutboundHttpBinding` and its fail-closed grant-narrowing rule;
- deterministic fake and conformance support in `mem_sandbox.network.testing`.

The sandbox service owns profile selection and construction. `SandboxSession` owns
operation serialization, lifecycle, policy admission, deadlines, cancellation, and
operation events. Later network work owns URL canonicalization, destination admission,
resolution, transport, credentials, resource accounting, and network-specific audit
events.

No agent SDK, concrete HTTP client, resolver, socket, environment proxy, cookie jar,
cache, or TLS configuration is imported by this module.

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
the request's host-bounded transfer timeout.

Session close cooperatively cancels an active HTTP operation and then waits for the
operation gate. The gateway is borrowed and is never closed by the session.

## Fake and conformance support

`FakeOutboundHttpGateway` returns scripted responses or failures, records exact calls,
and can block for timeout and cancellation tests. It opens no network connection.
`OutboundHttpGatewayConformanceDriver` provides reusable round-trip and stable-failure
probes; real transports added later must satisfy the same boundary.

## Security invariants

- Networking is absent unless the host selects a connected profile.
- Models and grant ceilings are immutable.
- Model-facing input cannot choose a destination policy or add a credential reference.
- Request/context/response representations do not expose URLs, headers, bodies,
  credential routes, or grant details.
- Unexpected gateway exceptions become a cause-free stable error at the session
  boundary.
- Basic scheme recognition is not destination admission. Until the controlled resolver
  and transport ship, the fake is the only provided gateway implementation.
- Library contracts do not contain arbitrary guest code.

## Maintenance

Changes to methods, schemes, grant ordering, limits, context contents, error codes,
profile binding, snapshot authority, lifecycle cancellation, or gateway conformance must
update this README, the detailed controlled-egress design, and behavior tests in the
same change.
