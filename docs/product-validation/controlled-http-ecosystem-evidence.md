# Controlled HTTP Ecosystem Evidence

**Research date:** 2026-09-18

**Issue:** [#54](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/54)

**Evidence class:** Documented ecosystem behavior from first-party public sources;
directional demand remains speculative under #77

**Decision impact:** Controlled HTTP is a well-supported product direction, but this
research does not by itself satisfy the
[#77 reproducible-workflow trigger](./workflow-blocker-assessment.md#review-triggers)
or authorize Milestone 9 implementation.

## Question and result

This research asks:

> Where does network access materially expand agent-sandbox scenarios, and what control
> boundary do established products place around that access?

Ten reviewed coding-agent and sandbox products all document destination-scoped outbound
network controls. Their starting postures differ: some begin network-disabled or behind
a limited firewall, while others permit public egress and provide explicit block or
allowlist modes. The common product decision is therefore not unrestricted networking.
It is **useful outbound reachability under host- or administrator-owned policy**.

The documented scenarios fall into three groups:

1. **Environment setup:** clone source, install packages, download browsers, and prepare
   a build or test environment.
2. **Runtime interaction:** call public or authenticated APIs, retrieve current web
   content, use remote agent tools, and return results to another service.
3. **Private-service integration:** reach internal APIs, databases, model endpoints, or
   platform bindings through a proxy, private network, or transformed request.

The first group dominates coding-sandbox examples but does not directly justify
MemSandbox networking. MemSandbox does not currently execute arbitrary guest code, and
the accepted #77 decision classifies contributor package retrieval as a host concern.
The second group is the strongest fit for MemSandbox: a trusted, typed `GET`/`HEAD`
operation can add current external evidence to an otherwise file-based workflow without
adding a host shell, package manager, browser, or general socket authority.

The ecosystem evidence therefore supports this bounded conclusion:

> A host-granted HTTP gateway is a credible key to MemSandbox's first connected
> workspace scenarios, especially current-source verification and bounded API
> enrichment. It is not evidence for unrestricted guest networking, and it is not yet
> proof that controlled HTTP should be the next implementation milestone.

## Method and evidence limits

The comparison uses public first-party documentation retrieved on the research date.
Product pages, configuration references, and maintained examples establish documented
behavior and intended scenarios. They do not establish adoption, request frequency,
customer priority, performance, or a blocked MemSandbox workflow.

The review records only behavior stated by a source. An example destination is not
treated as customer demand, and a configurable control is not assumed to be enabled by
default. Inbound previews, tunnels, and exposed services are separated from outbound
egress because they do not support the Milestone 9 goal.

No sandbox, model provider, public endpoint, credential, or billable service was invoked
for this research.

## Ecosystem comparison

The scenario phase distinguishes environment preparation from network use during an
agent task and from service calls deliberately retained in the host application.

| Product | Starting posture | Scenario phase | Documented scenarios | Control and enforcement boundary |
| --- | --- | --- | --- | --- |
| [OpenAI Codex cloud](https://developers.openai.com/codex/cloud/internet-access) | The agent phase blocks internet access by default; setup scripts retain access for dependency installation. | Setup and agent runtime | Setup retrieves dependencies and source. An agent-runtime issue URL illustrates the risk of fetching untrusted content. | Per-environment off/on control, domain allowlists, and an option to permit only `GET`, `HEAD`, and `OPTIONS`. The control covers commands in the cloud agent environment. |
| [GitHub Copilot cloud agent](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-the-firewall) | A built-in firewall limits internet access. A recommended dependency allowlist is enabled by default in addition to hosts required for GitHub operation. | Setup; runtime API use not documented | Setup downloads operating-system and language dependencies, uses container registries, validates certificates, and downloads browsers for Playwright MCP. | Organization and repository domain or URL/path allowlists. Blocked requests are reported on the pull request. GitHub documents that the firewall applies to processes started by the agent's Bash tool and is not a comprehensive boundary for setup or MCP processes. |
| [Anthropic Claude Code](https://code.claude.com/docs/en/sandboxing) | The sandbox feature is disabled by default. Once enabled, sandboxed commands have no pre-allowed domains; a new destination prompts for approval unless managed or auto-mode policy decides it. Startup failure and blocked commands can fall back to unsandboxed execution under defaults, so mandatory enforcement also requires fail-closed startup and disabling unsandboxed retries. | Setup and command runtime | Setup reaches package or source hosts for builds and tests. Runtime examples cover authenticated GitHub and AWS command-line operations. | An operating-system sandbox routes command processes through a proxy with domain allowlists, strict managed lockdown, per-command destinations, and corporate/custom proxy support. Masked credentials can be injected only for named hosts without exposing their values to the command. |
| [E2B](https://docs.e2b.dev/network/internet-access) | Outbound internet access is enabled by default and can be disabled or narrowed. | Environment setup in sandbox; model and PR API calls host-side in the maintained CI example | [AI code review CI](https://docs.e2b.dev/use-cases/ci-cd) clones a repository and installs dependencies inside the sandbox; its model request and PR-comment API call remain host-side. A separate [runtime package guide](https://docs.e2b.dev/quickstart/install-custom-packages) installs packages after sandbox start for environment preparation. | Sandbox-wide disable, IP/CIDR allow and deny rules, domain allowlists, runtime policy replacement, and per-host header transforms. Private-beta BYOP tunnels TCP from the E2B host to a customer-operated SOCKS5 proxy after E2B filtering; transforms run before dialing, while UDP, DNS, and QUIC/HTTP3 are not tunneled. |
| [Modal Sandboxes](https://modal.com/docs/guide/sandbox-networking) | Public-IP outbound connections are allowed by default. | Setup and runtime | [Sandbox examples](https://modal.com/docs/guide/sandboxes) cover Git checkout, tests, and dependency setup. A policy example begins broad for installation and narrows to runtime tool domains. | Full block, CIDR allowlist, TLS domain allowlist, runtime policy replacement, and optional sidecar proxy. Controls apply below arbitrary sandbox code; Modal documents SNI/domain-fronting limits for the domain-only mode. |
| [Daytona](https://www.daytona.io/docs/en/network-limits/) | The default is tier-dependent: lower tiers are restricted; higher tiers permit full internet unless a sandbox or organization rule narrows it. | Setup and runtime | Setup uses Git and package registries; runtime agent environments use model-provider and related service domains. The guide tests policy with HTTP and package-manager operations. | CIDR allowlist, domain allowlist, or block-all firewall settings can change on a running sandbox. A create-time-only upstream HTTP(S) proxy sets `HTTP_PROXY`/`HTTPS_PROXY`; its URL can contain credentials, is encrypted at rest, and is returned by single-sandbox reads. Proxy-unaware clients can bypass it unless a domain allowlist also constrains egress. |
| [Azure Container Apps Sandboxes](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-egress-policies) | Policy supports either default allow or deny; Microsoft recommends default deny with explicit destinations for untrusted code. | Runtime; setup not documented | Runtime scenarios include AI-generated scripts, agent tool calls, arbitrary user code, authenticated LLM API calls, and environment-specific upstream services. | A built-in egress proxy matches host, path, and HTTP method, then allows, denies, transforms, or rewrites. Secret or managed-identity headers can be attached outside guest code. Inspection mode determines whether non-HTTP traffic is blocked. |
| [Cloudflare Sandbox SDK](https://developers.cloudflare.com/sandbox/guides/outbound-traffic/) | Public internet access is enabled by default and can be disabled. | Setup and runtime | Agentic workloads access GitHub or an internal VCS during setup and call Workers bindings or approved upstream services at runtime. | Host/IP allow and deny lists plus trusted programmable HTTP handlers outside the sandbox. Handlers can enforce methods, reroute requests, or inject per-host credentials without exposing them to guest code. With public internet disabled, non-HTTP traffic is denied except for DNS through Cloudflare's resolvers. |
| [Google Managed Agents](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/managed-agents/sandbox-environment) | External network access is disabled by default. | Setup and runtime | Setup downloads external libraries; runtime accesses public resources, standard web APIs, and configured remote MCP servers. | Environment-owned domain allowlists. Mounted data receives downscoped tokens, MCP headers are sent only to configured endpoints, and the sandbox has no ambient project credentials. The policy covers the managed command environment. |
| [Docker Sandboxes](https://docs.docker.com/ai/sandboxes/security/defaults/) | `sbx run claude` with no extra flags denies unmatched outbound TCP, including HTTP, HTTPS, and SSH; active local or organization rules may allow named destinations. Direct external UDP and ICMP remain blocked. | Setup and runtime | [Workflow guidance](https://docs.docker.com/ai/sandboxes/workflows/) covers source and dependency setup plus runtime builds, tests, development services, registries, and authenticated command-line tools. | Local or organization rules allow or deny outbound TCP by hostname, CIDR, and port. A mandatory host proxy enforces network policy and credential injection. It can optionally chain HTTP(S), but not other TCP, through an experimental upstream proxy using host routing or explicit configuration. |

### Control coverage and source maturity

`Not documented` means that the reviewed first-party pages did not establish the
dimension. It does not claim the product lacks the feature.

| Product | Destination policy | HTTP-method policy | Proxy boundary | Private-network path | Credential boundary | Audit or denied-request evidence | Maturity and important caveat |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OpenAI Codex cloud | Domain presets/custom allowlist | Optional `GET`/`HEAD`/`OPTIONS` restriction | Not documented | Not documented | Not documented | Work-log review is recommended; a durable egress-decision record is not documented | No maturity label on the reviewed page |
| GitHub Copilot cloud agent | Required hosts plus organization/repository domain and URL/path allowlists | Not documented | Not documented | Not documented | Not documented by the firewall sources | Blocked address and command are added to the PR | No maturity label on the reviewed pages; firewall scope excludes setup and direct MCP processes and is documented as bypassable |
| Anthropic Claude Code | Session, persisted, strict-managed, and per-command domains | Not documented | Corporate or custom upstream proxy | Not documented | File/environment sentinels can be replaced only for configured hosts | A denied host is named in the command result; durable egress audit is not documented | No overall maturity label on the reviewed pages; sandboxing is off by default, fail-if-unavailable and unsandboxed retry are permissive, and credential substitution requires experimental TLS termination |
| E2B | Network off; IP/CIDR allow and deny; domains are valid only in `allowOut` | Not documented | Optional private-beta TCP tunnel from the E2B host to a customer-operated SOCKS5 proxy after built-in filtering; transforms run before dialing | The customer proxy can reach corporate networks, VPNs, and internal services | Public-beta per-host transforms replace secret or workload-identity references outside the sandbox | A customer proxy can log or inspect; built-in durable egress audit is not documented | Per-host transforms are public beta; bring-your-own proxy is private beta; UDP, DNS, and QUIC/HTTP3 are not tunneled |
| Modal | Network off; CIDR allowlist; beta TLS-domain allowlist | Not documented | Alpha sidecar can transparently proxy HTTPS | Internal bridge connects the main container and sidecars; external private-network attachment is not documented | Alpha sidecar pattern can inject secrets outside the main container | Domain denials are written to system output; a sidecar can inspect/log requests; durable audit is not documented | Domain filtering is beta; live policy replacement and sidecars are alpha; a sidecar's own egress defaults open unless separately restricted |
| Daytona | Network off, CIDR allowlist, or domain allowlist | Not documented | Create-time-only upstream HTTP(S) proxy configured through in-sandbox `HTTP_PROXY`/`HTTPS_PROXY` | CIDR allowlists explicitly support private-network ranges | No destination-service broker is documented; a proxy URL may contain userinfo, is encrypted at rest, appears in guest proxy variables, and is returned by single-sandbox reads | Not documented | No maturity label on the reviewed page; tier policy constrains availability, and a proxy alone is cooperative and needs firewall allowlisting to prevent bypass |
| Azure Container Apps Sandboxes | Ordered host/path rules with allow, deny, transform, and rewrite | Ordered rules match HTTP methods | Built-in egress proxy | Rewrite can route to an environment-specific upstream; network attachment details are not documented | Static, secret-reference, or managed-identity headers are attached by policy | Egress decisions and denied-count review are documented | Product and egress policy are preview |
| Cloudflare Sandbox SDK | Internet off; host/IP allow and deny | Programmable outbound handler can enforce methods | Trusted Worker outbound handlers | Platform bindings can mediate platform resources; general private-network attachment is not documented | Per-host handler reads Worker secrets outside the sandbox | A handler can log policy decisions; a prescribed durable audit contract is not documented | No maturity label on the reviewed page |
| Google Managed Agents | Network off with a domain allowlist | Not documented | Not documented by the reviewed sandbox sources | Not documented by the reviewed sandbox sources | Downscoped mount tokens and endpoint-specific MCP headers; no ambient project credentials | Not documented | The referenced managed base agent is preview; allowlist maturity is not separately labeled |
| Docker Sandboxes | Deny-unmatched TCP with hostname, CIDR, and port rules | Not documented | Mandatory host enforcement proxy; optional experimental upstream chaining is HTTP(S)-only, while other TCP is forwarded transparently | The proxy maps `host.docker.internal` to host `localhost` and requires an exact `localhost:port` allow rule; direct host loopback/LAN addressing is not the supported path, and direct sandbox-to-sandbox networking is blocked | Host proxy injects service/domain-matched credentials; raw values stay outside the VM | Active rules are inspectable; a durable egress-decision record is not documented | No maturity label for the core sandbox or policy; upstream proxy support is experimental, and default rules can contain broad wildcards |

## Cross-product findings

### Destination policy is the shared minimum

All ten products document destination-scoped control through domains, hosts, URLs,
CIDRs, ports, or a combination. Several also provide a complete network-off mode.
Starting defaults vary, but no reviewed product presents unrestricted egress as the only
usable configuration.

This supports the MemSandbox design choice to keep the default profile network-disabled
and let the host select a narrower connected profile. It does not establish whether
MemSandbox should use another product's mutable-policy or broad wildcard behavior.

### Nominally read-only methods are a meaningful first boundary

Codex explicitly supports limiting agent access to `GET`, `HEAD`, and `OPTIONS`.
Azure policies match on HTTP method, and Cloudflare demonstrates a trusted handler that
rejects every method except `GET`. These independent designs support beginning with
bounded retrieval instead of a general networking or state-changing API.

Method restriction is not enough on its own. The reviewed products pair it with
destination policy, and the existing MemSandbox design additionally requires DNS,
redirect, proxy, TLS, byte, time, concurrency, and cumulative-session controls.
`GET` and `HEAD` are only nominally read-only: a remote service can attach side effects,
and a failed request can leave its remote outcome uncertain.

### Credentials belong outside the untrusted workload

Azure, Cloudflare, E2B, Claude Code, Google Managed Agents, Docker, and Modal all
document a form of destination-scoped credential mediation or downscoped authorization.
In each design, trusted infrastructure selects where a credential may be sent rather
than relying on untrusted code to protect a raw value. The E2B transforms and Modal
sidecar path are maturity-labeled rather than stable, as the control-coverage table
records.

That convergence supports MemSandbox's destination-bound secret broker. It also shows
why simply placing a token in a workspace file or command environment would not be a
competitive or safe connected profile.

### Enforcement depth follows execution authority

E2B, Modal, Daytona, Azure, Cloudflare, Google, and Docker execute arbitrary or
agent-generated code, so their network controls operate below that guest code or in a
mandatory egress proxy. Claude Code similarly covers command subprocesses while
documenting that in-process tools have a separate permission boundary. GitHub documents
scope limitations for setup and MCP processes.

MemSandbox's initial Milestone 9 design is intentionally narrower: trusted virtual
commands and typed tools receive one HTTP gateway. That can govern those adapters, but
it cannot contain a later Python process that can open sockets. External execution must
compile the grant into a system-level boundary, as the current component design already
requires.

### Host tools remain a credible alternative boundary

The products do not place every network interaction inside the sandbox. E2B's maintained
AI-review example performs repository clone and dependency installation in the sandbox
but calls the model and posts the review from the host process. Claude Code's in-process
`WebFetch` tool has a permission boundary separate from sandboxed command networking.
Google Managed Agents similarly offers service-side search and URL-context tools in
addition to optional network access for its command environment.

This is evidence against treating sandbox-owned HTTP as automatically necessary. A host
tool is sufficient when the application already knows the operation and can own its
policy, credentials, accounting, and result transfer. A MemSandbox gateway becomes the
stronger reusable seam only when the same bounded remote interaction must participate in
session grants, command and typed-tool behavior, workspace publication, cumulative
budgets, and audit events across host applications.

### Domain-only filtering has known limits

E2B and Modal both document limitations of hostname filtering on shared TLS
infrastructure. Modal describes domain-fronting risk; E2B calls its hostname allowlist a
routing control rather than a strict boundary on shared infrastructure.

SSRF, DNS rebinding, and domain fronting require related but distinct controls.
Normalization, pre-DNS admission, address classification, result pinning, and redirect
re-admission address the first two. The gateway or its trusted proxy must also bind the
normalized URL hostname consistently to DNS resolution, TLS SNI and certificate
verification, and HTTP `Host` or HTTP/2 `:authority`; callers must not supply a
mismatched authority. When one admitted hostname multiplexes unrelated resources,
resource and credential scoping or an owned proxy/dedicated endpoint remains necessary.

## Scenario relevance for MemSandbox

| Scenario family | Ecosystem evidence | Fit for the current product | Direction |
| --- | --- | --- | --- |
| Package and operating-system dependency installation | Very common across Codex, Copilot, E2B, Modal, Daytona, Google, and Docker | Low. It requires an execution environment or host setup, not merely a trusted HTTP tool. Prior contributor CDN failures remain host evidence. | Keep with host setup or future external execution. |
| Git clone, source checkout, and VCS mutation | Common across coding sandboxes | Low for the first HTTP slice. Current artifact and Git decisions are separately gated, and state-changing VCS behavior exceeds bounded retrieval. | Preserve the Milestone 8/12 Git decision boundary. |
| Current public web or API retrieval for a workspace task | Explicitly enabled by Codex, Azure, Cloudflare, E2B, and Google; compatible with the other destination-control models | High. The existing workspace can inspect inputs and produce a verified report, but cannot obtain a runtime-current remote fact. | Strongest first connected scenario for nominally read-only bounded `GET`/`HEAD`. |
| Authenticated upstream API call | Strong control evidence from Azure, Cloudflare, E2B, Claude, and Google | Medium to high, with more security work. It validates destination-bound credential routing but increases secret and audit scope. | Follow an unauthenticated, nominally read-only slice or require a separately justified credential route. |
| Arbitrary guest-code networking | Core to full execution sandboxes | Low for Milestone 9 alone. A library gateway cannot constrain `socket` in external Python. | Compose only after Milestone 10 has a system-level egress boundary. |
| Inbound network tunnels or preview URLs | Modal documents network tunnels; [Daytona documents generated inbound preview URLs for HTTP services](https://www.daytona.io/docs/en/preview.md) | Out of scope. This reverses the traffic direction and adds service identity, exposure, and lifetime concerns. | Do not infer inbound networking from this evidence. |

## Recommended product scenario

The best next validation target is a **current-source verification workflow**:

1. The host seeds a document, dataset, or review request into the workspace.
2. Inspection discovers public URLs or identifiers. If they fall within a destination
   class pre-authorized by the immutable creation-time grant, the current connected
   session may continue. Otherwise, the network-free session emits bounded candidates;
   the host makes an explicit new authority decision and creates or resumes a connected
   session under a bounded immutable grant. Model input never widens the running
   session's authority.
3. The agent performs bounded `HEAD` or `GET` requests against the granted destinations
   to verify status, freshness, redirect destination, or current metadata. These methods
   remain nominally read-only and retain explicit possible-side-effect and
   unknown-remote-outcome handling.
4. The agent writes a path-linked report and the host verifies the output artifact.

Examples include checking external references in a document, validating current issue or
release metadata cited by a review, and enriching a local artifact with a bounded public
API response. These extend the existing document-review positioning rather than changing
MemSandbox into a code runner or browser.

This scenario favors controlled HTTP over the current alternatives:

- Static host seeding works when every input and response is known before the task. It
  does not provide a runtime-current observation for destinations discovered while
  inspecting workspace content. A host can add a custom orchestration loop, and host
  approval remains necessary whenever discovery falls outside a pre-authorized
  destination class. The gateway centralizes enforcement, redirects, accounting,
  provenance, and result publication after that authority decision; it does not
  eliminate the decision.
- Network-disabled Python can parse or calculate over local data but cannot obtain the
  remote observation.
- Artifact exchange can move host-owned bytes but does not define remote retrieval,
  destination policy, or freshness.
- A trusted, host-granted HTTP gateway centralizes exactly that missing authority without
  granting arbitrary code or sockets.

The candidate is deliberately limited to nominally read-only methods. Authenticated
mutation, package installation, Git transport, browser execution, and arbitrary-code
egress remain separate decisions.

## What would satisfy the product trigger

At publication, this report documented ecosystem behavior whose implication for
MemSandbox demand remained **directional and speculative** under #77 because it was not
a linked reproducible MemSandbox blocker. Any one of #77's existing five review triggers
could reopen broader next-capability selection. The required follow-up evidence was:

- the concrete input artifact and expected verified output;
- why destinations or freshness cannot be fully prepared through static host seeding;
- the exact `GET` or `HEAD` interaction required;
- an actual current-profile result classified as unsupported by MemSandbox, not model
  behavior, policy denial, or provider failure;
- why a host callback, artifact exchange, network-disabled execution, or Git provider is
  not the narrower solution; and
- the destination, response, time, redirect, and cumulative budgets needed for a bounded
  implementation.

The subsequent
[current-source verification evidence](./current-source-verification-evidence.md)
records the concrete workflow, deterministic current-profile
`command_not_found` result, callback developer-integration boundary, explicit selection
authority, and bounded evidence grant. It satisfies #77 review trigger 3 and selects
Milestone 9 for focused implementation planning. It does not retroactively turn this
ecosystem comparison into measured demand, claim that networking has shipped, or remove
the applicable Milestone 8 grant, policy, accounting, and event prerequisites.

## Source index

All sources were accessed on 2026-09-18.

- OpenAI:
  [Codex cloud internet access](https://developers.openai.com/codex/cloud/internet-access)
- GitHub:
  [Copilot firewall](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-the-firewall),
  [recommended allowlist](https://docs.github.com/en/copilot/reference/copilot-allowlist-reference#copilot-cloud-agent-recommended-allowlist),
  and
  [cloud-agent scenarios](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent)
- Anthropic:
  [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing) and
  [sandbox settings](https://code.claude.com/docs/en/settings-reference#sandbox-settings)
- E2B:
  [internet access](https://docs.e2b.dev/network/internet-access),
  [bring your own proxy](https://docs.e2b.dev/network/byop),
  [AI review CI/CD](https://docs.e2b.dev/use-cases/ci-cd), and
  [runtime packages](https://docs.e2b.dev/quickstart/install-custom-packages)
- Modal:
  [networking and security](https://modal.com/docs/guide/sandbox-networking),
  [sandbox sidecars](https://modal.com/docs/guide/sandbox-sidecars), and
  [sandbox scenarios](https://modal.com/docs/guide/sandboxes)
- Daytona:
  [network limits](https://www.daytona.io/docs/en/network-limits/),
  [preview URLs](https://www.daytona.io/docs/en/preview.md), and
  [sandbox overview](https://www.daytona.io/docs/en/)
- Microsoft:
  [Azure Container Apps Sandboxes egress policies](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-egress-policies)
- Cloudflare:
  [Sandbox outbound traffic](https://developers.cloudflare.com/sandbox/guides/outbound-traffic/)
- Google Cloud:
  [Managed Agents sandbox environment](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/managed-agents/sandbox-environment)
  and
  [network configuration](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/managed-agents/create-manage#configure-network-access)
- Docker:
  [default security posture](https://docs.docker.com/ai/sandboxes/security/defaults/),
  [security model](https://docs.docker.com/ai/sandboxes/security/),
  [architecture and upstream proxy](https://docs.docker.com/ai/sandboxes/architecture/),
  [Sandbox network policies](https://docs.docker.com/ai/sandboxes/governance/access-controls/network/),
  [credential management](https://docs.docker.com/ai/sandboxes/configuration/credentials/),
  [product boundary](https://docs.docker.com/ai/sandboxes/), and
  [workflow patterns](https://docs.docker.com/ai/sandboxes/workflows/), including
  [policy-gated host services](https://docs.docker.com/ai/sandboxes/workflows/development/)
