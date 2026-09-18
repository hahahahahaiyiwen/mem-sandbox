# Controlled HTTP Ecosystem Evidence

**Research date:** 2026-09-18

**Issue:** [#54](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/54)

**Evidence class:** Observed ecosystem evidence from first-party public documentation

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

| Product | Starting posture | Documented scenarios | Control and enforcement boundary |
| --- | --- | --- | --- |
| [OpenAI Codex cloud](https://developers.openai.com/codex/cloud/internet-access) | The agent phase blocks internet access by default; setup scripts retain access for dependency installation. | Dependency and source retrieval; an issue URL is also used to explain the risk of agent-fetched, untrusted content. | Per-environment off/on control, domain allowlists, and an option to permit only `GET`, `HEAD`, and `OPTIONS`. The control covers commands in the cloud agent environment. |
| [GitHub Copilot cloud agent](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-the-firewall) | A built-in firewall limits internet access. A recommended dependency allowlist is enabled by default in addition to hosts required for GitHub operation. | Download operating-system and language dependencies, use container registries, validate certificates, and download browsers for Playwright MCP. | Organization and repository domain or URL/path allowlists. Blocked requests are reported on the pull request. GitHub documents that the firewall applies to processes started by the agent's Bash tool and is not a comprehensive boundary for setup or MCP processes. |
| [Anthropic Claude Code](https://code.claude.com/docs/en/sandboxing) | Sandboxed commands have no pre-allowed domains by default; a new destination prompts for approval unless managed or auto-mode policy decides it. | Run builds and tests that require package or source hosts; authenticated GitHub, npm, and AWS command-line workflows are documented in the sandbox configuration examples. | An operating-system sandbox routes command processes through a proxy with domain allowlists, strict managed lockdown, per-command destinations, and corporate/custom proxy support. Masked credentials can be injected only for named hosts without exposing their values to the command. |
| [E2B](https://docs.e2b.dev/network/internet-access) | Outbound internet access is enabled by default and can be disabled or narrowed. | [AI code review CI](https://docs.e2b.dev/use-cases/ci-cd) clones a repository and installs dependencies inside the sandbox; its model request and PR-comment API call remain host-side, making that boundary explicit. [Runtime package installation](https://docs.e2b.dev/quickstart/install-custom-packages) is a separate documented use case. | Sandbox-wide disable, IP/CIDR and domain allow/deny rules, proxy routing, runtime policy replacement, and per-host header transforms. Secret and workload-identity values can be substituted outside the sandbox for an admitted HTTPS host. The controls cover guest traffic. |
| [Modal Sandboxes](https://modal.com/docs/guide/sandbox-networking) | Public-IP outbound connections are allowed by default. | [Sandbox examples](https://modal.com/docs/guide/sandboxes) include generated or untrusted code, Git checkout, tests, and dependency setup. A network-policy example starts broad for dependency installation and later narrows access to tool domains. | Full block, CIDR allowlist, TLS domain allowlist, runtime policy replacement, and optional sidecar proxy. Controls apply below arbitrary sandbox code; Modal documents SNI/domain-fronting limits for the domain-only mode. |
| [Daytona](https://www.daytona.io/docs/en/network-limits/) | The default is tier-dependent: lower tiers are restricted; higher tiers permit full internet unless a sandbox or organization rule narrows it. | AI-generated code and agent environments use essential Git, package-registry, model-provider, and related service domains. The guide uses HTTP checks and package-manager operations to test policy. | Mutually exclusive CIDR allowlist, domain allowlist, or block-all settings, plus an upstream HTTP(S) proxy. Rules can change on a running sandbox. These are guest-network controls; Daytona's preview URLs are a separate inbound feature. |
| [Azure Container Apps Sandboxes](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-egress-policies) | Policy supports either default allow or deny; Microsoft recommends default deny with explicit destinations for untrusted code. | AI-generated scripts, agent tool calls, arbitrary user code, authenticated LLM API calls, and access to environment-specific upstream services. | A built-in egress proxy matches host, path, and HTTP method, then allows, denies, transforms, or rewrites. Secret or managed-identity headers can be attached outside guest code. Inspection mode determines whether non-HTTP traffic is blocked. |
| [Cloudflare Sandbox SDK](https://developers.cloudflare.com/sandbox/guides/outbound-traffic/) | Public internet access is enabled by default and can be disabled. | Agentic workloads access GitHub or an internal VCS during setup and can call Workers bindings or approved upstream services at runtime. | Host/IP allow and deny lists plus trusted programmable HTTP handlers outside the sandbox. Handlers can enforce methods, reroute requests, or inject per-host credentials without exposing them to guest code. Non-HTTP traffic is denied when public internet is disabled. |
| [Google Managed Agents](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/managed-agents/sandbox-environment) | External network access is disabled by default. | Download external libraries, access public internet resources, use standard web APIs, and connect to configured remote MCP servers. | Environment-owned domain allowlists. Mounted data receives downscoped tokens, MCP headers are sent only to configured endpoints, and the sandbox has no ambient project credentials. The policy covers the managed command environment. |
| [Docker Sandboxes](https://docs.docker.com/ai/sandboxes/governance/access-controls/network/) | Network access is policy-controlled; the documentation does not state one universal starting posture on the comparison page. | [Workflow guidance](https://docs.docker.com/ai/sandboxes/workflows/) covers Git, dependency installation, builds, tests, published services, registries, and authenticated command-line tools. | Local or organization rules allow or deny outbound TCP by hostname, CIDR, and port. Organization allow rules own grants when governance is active; local deny rules may narrow them. The microVM boundary covers agent processes and containers. |

## Cross-product findings

### Destination policy is the shared minimum

All ten products document destination-scoped control through domains, hosts, URLs,
CIDRs, ports, or a combination. Several also provide a complete network-off mode.
Starting defaults vary, but no reviewed product presents unrestricted egress as the only
usable configuration.

This supports the MemSandbox design choice to keep the default profile network-disabled
and let the host select a narrower connected profile. It does not establish whether
MemSandbox should use another product's mutable-policy or broad wildcard behavior.

### Read-only methods are a meaningful first boundary

Codex explicitly supports limiting agent access to `GET`, `HEAD`, and `OPTIONS`.
Azure policies match on HTTP method, and Cloudflare demonstrates a trusted handler that
rejects every method except `GET`. These independent designs support beginning with
bounded retrieval instead of a general networking or state-changing API.

Method restriction is not enough on its own. The reviewed products pair it with
destination policy, and the existing MemSandbox design additionally requires DNS,
redirect, proxy, TLS, byte, time, concurrency, and cumulative-session controls.

### Credentials belong outside the untrusted workload

Azure, Cloudflare, E2B, Claude Code, and Google Managed Agents all document a form of
destination-scoped credential mediation or downscoped authorization. In each design,
trusted infrastructure selects where a credential may be sent rather than relying on
untrusted code to protect a raw value.

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
routing control rather than a strict boundary on shared infrastructure. Their warnings
support MemSandbox's stricter plan to normalize the URL, admit before DNS, classify
resolved addresses, pin admitted results, and repeat the process on redirects.

## Scenario relevance for MemSandbox

| Scenario family | Ecosystem evidence | Fit for the current product | Direction |
| --- | --- | --- | --- |
| Package and operating-system dependency installation | Very common across Codex, Copilot, E2B, Modal, Daytona, Google, and Docker | Low. It requires an execution environment or host setup, not merely a trusted HTTP tool. Prior contributor CDN failures remain host evidence. | Keep with host setup or future external execution. |
| Git clone, source checkout, and VCS mutation | Common across coding sandboxes | Low for the first HTTP slice. Current artifact and Git decisions are separately gated, and state-changing VCS behavior exceeds bounded retrieval. | Preserve the Milestone 8/12 Git decision boundary. |
| Current public web or API retrieval for a workspace task | Explicitly enabled by Codex, Azure, Cloudflare, E2B, and Google; compatible with the other destination-control models | High. The existing workspace can inspect inputs and produce a verified report, but cannot obtain a runtime-current remote fact. | Strongest first connected scenario for bounded `GET`/`HEAD`. |
| Authenticated upstream API call | Strong control evidence from Azure, Cloudflare, E2B, Claude, and Google | Medium to high, with more security work. It validates destination-bound credential routing but increases secret and audit scope. | Follow an unauthenticated/read-only slice or require a separately justified credential route. |
| Arbitrary guest-code networking | Core to full execution sandboxes | Low for Milestone 9 alone. A library gateway cannot constrain `socket` in external Python. | Compose only after Milestone 10 has a system-level egress boundary. |
| Inbound previews, tunnels, or hosted services | Documented by Modal and Daytona | Out of scope. This reverses the traffic direction and adds service identity, exposure, and lifetime concerns. | Do not infer inbound networking from this evidence. |

## Recommended product scenario

The best next validation target is a **current-source verification workflow**:

1. The host seeds a document, dataset, or review request into the workspace.
2. Inspection discovers public URLs or identifiers whose required destination set is not
   fixed before the task starts.
3. The agent performs bounded `HEAD` or `GET` requests against host-approved destinations
   to verify status, freshness, redirect destination, or current metadata.
4. The agent writes a path-linked report and the host verifies the output artifact.

Examples include checking external references in a document, validating current issue or
release metadata cited by a review, and enriching a local artifact with a bounded public
API response. These extend the existing document-review positioning rather than changing
MemSandbox into a code runner or browser.

This scenario favors controlled HTTP over the current alternatives:

- Static host seeding works when every input and response is known before the task. It
  does not provide a runtime-current observation for destinations discovered while
  inspecting workspace content. A host can add a custom orchestration loop, but then
  every application must independently own request policy, redirects, accounting,
  provenance, and result reinjection.
- Network-disabled Python can parse or calculate over local data but cannot obtain the
  remote observation.
- Artifact exchange can move host-owned bytes but does not define remote retrieval,
  destination policy, or freshness.
- A trusted, host-granted HTTP gateway centralizes exactly that missing authority without
  granting arbitrary code or sockets.

The candidate is deliberately read-only. Authenticated mutation, package installation,
Git transport, browser execution, and arbitrary-code egress remain separate decisions.

## What would satisfy the product trigger

This report remains **observed ecosystem evidence**, not a linked reproducible
MemSandbox blocker. Before changing #77 or creating Milestone 9 child issues, retain one
maintained or user workflow that records:

- the concrete input artifact and expected verified output;
- why destinations or freshness cannot be fully prepared through static host seeding;
- the exact `GET` or `HEAD` interaction required;
- an actual current-profile result classified as unsupported by MemSandbox, not model
  behavior, policy denial, or provider failure;
- why a host callback, artifact exchange, network-disabled execution, or Git provider is
  not the narrower solution; and
- the destination, response, time, redirect, and cumulative budgets needed for a bounded
  implementation.

Only that evidence can reopen next-capability selection. Applicable Milestone 8 grant,
policy, accounting, and event work must still be scoped before transport ships.

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
  [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing)
- E2B:
  [internet access](https://docs.e2b.dev/network/internet-access),
  [AI review CI/CD](https://docs.e2b.dev/use-cases/ci-cd), and
  [runtime packages](https://docs.e2b.dev/quickstart/install-custom-packages)
- Modal:
  [networking and security](https://modal.com/docs/guide/sandbox-networking) and
  [sandbox scenarios](https://modal.com/docs/guide/sandboxes)
- Daytona:
  [network limits](https://www.daytona.io/docs/en/network-limits/) and
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
  [Sandbox network policies](https://docs.docker.com/ai/sandboxes/governance/access-controls/network/),
  [product boundary](https://docs.docker.com/ai/sandboxes/), and
  [workflow patterns](https://docs.docker.com/ai/sandboxes/workflows/)
