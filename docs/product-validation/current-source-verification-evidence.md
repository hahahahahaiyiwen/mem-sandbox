# Current-Source Verification Workflow Evidence

**Assessment date:** 2026-09-20

**Issue:** [#54](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/54)

**Implementation base:** `42632e67078251db642136c975d7a34ada0603d5`

**Recorded input:** [`controlled-http-ecosystem-evidence.md`](./controlled-http-ecosystem-evidence.md)
at this evidence revision, SHA-256
`8a77268ba9661334da19084c5769bae7da0470fc7e72180dbb442463d0efa55c`

**Decision:** Select controlled outbound HTTP for bounded Milestone 9 decomposition and
implementation. Networking remains absent from the current and default profiles. This
record does not itself implement transport or complete a Milestone 9 checklist item.

## Finding

The maintained ecosystem report supplies a concrete current-source verification
workflow. The host can seed and preserve that report, and the constrained command
surface can discover its 29 public references across 11 hosts. The same current profile
cannot perform the required observation-time `HEAD` or `GET`: under the normal
allow-all session policy, a representative command-form request fails with
`command_not_found` before any network attempt.

This is a deterministic product-surface result rather than a policy denial, model
behavior, provider failure, or contributor package-retrieval failure. It satisfies
[workflow-blocker review trigger 3](./workflow-blocker-assessment.md#review-triggers).

A host-owned semantic callback can complete one fixed lookup. It is still the preferred
composition for a narrow application operation such as
`lookup_release_status(release_id)`. It is not the selected product boundary for a
reusable current-source workflow: when the application callback must accept varying
approved sources and implement HTTP admission, redirects, response bounds, cumulative
budgets, audit correlation, lifecycle, provenance, and SDK tool wiring, the application
has recreated the proposed controlled gateway.

The [approved design record][design-record] therefore treats developer integration as
part of the product decision. Applications should configure one immutable connected
profile and use the sandbox capability rather than build a parallel networking
subsystem for every agent workflow or SDK.

## Evidence classification

| Evidence | Classification | What it establishes | Limitation |
| --- | --- | --- | --- |
| Exact report bytes, URL discovery, preserved input, and structured `command_not_found` result | **Measured** | The current product can host the workflow artifacts but cannot perform its required remote observation. | The command spelling demonstrates absence at the recorded baseline; it does not select a future command name. |
| Existing [host-owned function-tool composition](../../packages/openai-agents/README.md#compose-with-a-host-owned-function-tool) | **Observed repository contract** | An application can add a separate typed callback beside the four MemSandbox tools. | The repository has not implemented or measured a 29-source citation callback. |
| Callback-versus-gateway ownership comparison | **Design analysis** | A fixed semantic callback stays narrow; a generalized source callback duplicates gateway responsibilities and SDK bridges. | This is an architectural and developer-experience comparison, not a latency or reliability benchmark. |
| Human approval in [issue #54][design-record] | **Decision authority** | The reusable sandbox-owned gateway experience is selected for bounded implementation planning. | Approval does not waive child issue design, security, test, review, or merge gates. |

The measured result is sufficient to reopen next-capability selection. The observed
composition and explicit product decision distinguish controlled HTTP from a callback
without claiming that callbacks are impossible.

## Reproducible workflow

### Input and task

The host seeds the exact maintained ecosystem report into
`/workspace/inputs/controlled-http-ecosystem-evidence.md`. A verifier must:

1. discover every external source and its source location;
2. obtain an observation-time status and, where required, bounded supporting content;
3. record redirects and the final admitted destination;
4. decide whether the current source still supports, contradicts, or cannot establish
   the cited claim; and
5. write a host-verifiable report without changing the input.

The input set is concrete at the recorded baseline:

| Property | Value |
| --- | --- |
| Input SHA-256 | `8a77268ba9661334da19084c5769bae7da0470fc7e72180dbb442463d0efa55c` |
| Unique public URLs | 29 |
| Unique hosts | 11 |
| Authentication | None |
| Required methods | Nominally read-only `HEAD` and `GET` |

### Authority transition

Discovery does not widen a running sandbox. A network-free session may emit candidate
destinations. The host then either:

- approves an immutable connected profile containing those destinations and creates or
  resumes under that profile; or
- rejects the candidates and leaves the task network-free.

The evidence fixture uses the exact 11 hosts already present in the input. A later input
with a new host requires a new host decision; model content never mutates the grant.

### Expected verified output

The connected workflow writes
`/workspace/review/current-source-report.json`. Each discovered source has exactly one
terminal record. The following record illustrates the schema only; its remote status and
verdict are not observations from this network-free study:

```json
{
  "source_sha256": "8a77268ba9661334da19084c5769bae7da0470fc7e72180dbb442463d0efa55c",
  "observations": [
    {
      "citation_id": "source-001",
      "source_path": "/workspace/inputs/controlled-http-ecosystem-evidence.md",
      "source_line": 73,
      "requested_url": "https://developers.openai.com/codex/cloud/internet-access",
      "method": "HEAD",
      "observed_at_utc": "runtime value",
      "status_code": 200,
      "final_url": "runtime value",
      "redirect_count": 0,
      "response_sha256": "runtime value",
      "content_truncated": false,
      "claim_verdict": "supported"
    }
  ]
}
```

Remote values are intentionally not pinned as timeless constants. Host verification
checks behavior and provenance instead:

- the input bytes and hash remain unchanged;
- the discovered URL set and terminal record set are equal;
- source paths and lines resolve to the recorded requested URLs;
- every request and redirect appears in the session's bounded network evidence;
- every destination and method was admitted by the immutable grant;
- response hashes and truncation state agree with the gateway result;
- each verdict is `supported`, `contradicted`, or `inconclusive`; and
- no model prose, raw secret, or unverified callback output substitutes for the
  workspace artifact and host checks.

Deterministic implementation tests will use fake resolver and transport responses to
produce exact artifact bytes. A public-network observation is optional, non-gating, and
requires separate authority.

## Current-profile result

Run this only against the recorded network-free source baseline after the declared
[development setup](../../CONTRIBUTING.md#development-setup). It creates an in-memory
sandbox, makes no public request, and uses no SDK model or credential:

```python
import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from mem_sandbox.command_executor import CommandFailureCode
from mem_sandbox.service import CreateSandboxRequest, OwnerId, WorkspaceSeedFile
from mem_sandbox.session import ReadBytesRequest, SessionExecuteRequest
from samples.shared.service import create_sample_service

SOURCE = Path("docs/product-validation/controlled-http-ecosystem-evidence.md")
WORKSPACE_PATH = "inputs/controlled-http-ecosystem-evidence.md"
PROBE_URL = "https://developers.openai.com/codex/cloud/internet-access"


async def main() -> None:
    source_bytes = SOURCE.read_bytes()
    service = create_sample_service()
    try:
        handle = await service.create(
            CreateSandboxRequest(
                owner_id=OwnerId("current-source-verification"),
                initial_files=(WorkspaceSeedFile(WORKSPACE_PATH, source_bytes),),
            )
        )
        session = await service.get_session(handle)
        discovery = await session.execute(
            SessionExecuteRequest(command=f'grep "https://" {WORKSPACE_PATH}')
        )
        probe = await session.execute(
            SessionExecuteRequest(command=f"curl --head {PROBE_URL}")
        )
        preserved = await session.read_bytes(ReadBytesRequest(path=WORKSPACE_PATH))

        urls = sorted(set(re.findall(r"\]\((https?://[^)]+)\)", discovery.stdout)))
        hosts = sorted({urlsplit(url).hostname for url in urls})
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()

        assert discovery.exit_code == 0
        assert len(urls) == 29
        assert len(hosts) == 11
        assert probe.exit_code == 127
        assert probe.failure_code is CommandFailureCode.COMMAND_NOT_FOUND
        assert preserved.content == source_bytes

        print(
            json.dumps(
                {
                    "artifact_seeded_and_preserved": True,
                    "current_profile": {
                        "exit_code": probe.exit_code,
                        "failure_code": probe.failure_code.value,
                        "stderr": probe.stderr.strip(),
                    },
                    "discovered_hosts": len(hosts),
                    "discovered_urls": len(urls),
                    "network_attempted": False,
                    "required_interaction": {"method": "HEAD", "url": PROBE_URL},
                    "source_sha256": source_sha256,
                },
                sort_keys=True,
            )
        )
    finally:
        await service.close()


asyncio.run(main())
```

Expected output:

```json
{"artifact_seeded_and_preserved": true, "current_profile": {"exit_code": 127, "failure_code": "command_not_found", "stderr": "curl: command not found"}, "discovered_hosts": 11, "discovered_urls": 29, "network_attempted": false, "required_interaction": {"method": "HEAD", "url": "https://developers.openai.com/codex/cloud/internet-access"}, "source_sha256": "8a77268ba9661334da19084c5769bae7da0470fc7e72180dbb442463d0efa55c"}
```

`curl` is intentionally an unknown virtual command here. MemSandbox does not invoke a
host executable or attempt the URL. The future command remains an implementation
decision; this baseline probe must not be reused as a capability test after a connected
profile ships.

## Narrow callback comparison

A host can read the same input before seeding, create an immutable mapping from
`citation_id` to URL, and expose a tool such as:

```python
class CitationVerifier(Protocol):
    async def verify(
        self,
        request: VerifyCitationRequest,
    ) -> CitationEvidence: ...
```

If the request contains only a registered citation ID and the result is one fixed
domain record, that callback is narrower than general HTTP and should remain
application-owned. It can complete this exact fixed-input run.

For a reusable document workflow, however, the application must additionally own:

- extraction and registration for each new input;
- SDK-specific tool schemas, binding, collisions, and lifecycle;
- URL normalization, destination admission, DNS and redirect handling;
- proxy, TLS, credential, retry, and response-decompression policy;
- request, response, time, concurrency, and cumulative budgets;
- safe errors, provenance, audit correlation, and unknown outcomes; and
- transfer of returned evidence into sandbox artifacts and host verification.

There is no interception requirement, but there is a separate application bridge
between agent tools, host networking, and sandbox state. Once the callback accepts
varying URLs or methods and centralizes these controls, it has the same authority and
responsibilities as the proposed gateway while remaining specific to one application or
SDK.

| Requirement | Fixed semantic callback | Controlled HTTP gateway |
| --- | --- | --- |
| Caller input | Pre-registered domain identifier | URL narrowed by an immutable host grant |
| Scope | One application operation | Reusable current-source workflow family |
| HTTP policy | Hidden inside the application | One framework-neutral network boundary |
| SDK integration | Application-defined function tool per SDK | Standard command and typed-tool adapters |
| Accounting and audit | Application-correlated | Session-coordinated and cumulative |
| Workspace provenance | Application bridge | Shared sandbox operation contract |

The gateway is selected because the product goal is the right-hand column. This does
not convert every external lookup into sandbox-owned HTTP.

## Alternatives

| Alternative | Why it does not satisfy the selected workflow family |
| --- | --- |
| Static host seeding | It can capture a host-fetched snapshot, but not an agent-selected observation tied to session authority and request evidence. A custom fetch-and-reseed loop remains possible but retains the application bridge. |
| Artifact exchange | It moves host-owned bytes; it does not define destination admission, freshness, redirects, or HTTP provenance. |
| Network-disabled execution | It can parse local evidence but cannot obtain an observation-time remote fact. |
| Git provider | It does not cover the eleven heterogeneous documentation hosts or ordinary web/API semantics. |
| Fixed semantic callback | It remains preferred for one known operation, but does not supply the reusable connected sandbox experience selected here. |
| Arbitrary guest networking | It is substantially broader and cannot be contained by a library gateway; system-level enforcement remains a separate external-execution concern. |

## Evidence grant and budgets

These bounds describe this evidence fixture, not final product defaults:

| Dimension | Fixture bound |
| --- | --- |
| Scheme and methods | HTTPS; `HEAD` and `GET` only |
| Destinations | The exact 11 approved hostnames discovered from the recorded input |
| Request body and credentials | None |
| Ambient state | No proxy, cookie, cache, credential, client certificate, custom TLS root, or environment inheritance |
| Request attempts | 64 cumulative, including redirects |
| Response | 512 KiB decompressed per response |
| Transfer | 8 MiB cumulative |
| Redirects | At most three per logical request; every hop is re-admitted |
| Concurrency | Four |
| Time | Ten seconds per attempt; 120 seconds for the workflow |
| Retry | None |

The exact approved hostnames are:

- `code.claude.com`
- `developers.cloudflare.com`
- `developers.openai.com`
- `docs.cloud.google.com`
- `docs.docker.com`
- `docs.e2b.dev`
- `docs.github.com`
- `github.com`
- `learn.microsoft.com`
- `modal.com`
- `www.daytona.io`

The cumulative request limit can terminate the workflow before every logical request
uses its per-request redirect allowance; the first exhausted limit wins.

`GET` and `HEAD` are only nominally read-only. A timeout, cancellation, or audit failure
after dispatch may leave the remote outcome unknown. A future implementation must report
that state rather than imply rollback or retry automatically.

## Selection scope and next work

This record selects the [controlled network egress design](../components/network-egress/README.md)
for focused implementation under #54:

- default networking remains absent;
- the host selects an immutable connected profile;
- a framework-neutral gateway owns normalized HTTP behavior;
- trusted virtual-command and typed-tool adapters share that gateway;
- deterministic fake resolver/transport evidence precedes a real transport; and
- applicable Milestone 8 grants, policy, accounting, and event foundations remain
  prerequisites.

After this record is merged, issue planning may create focused children for the gateway
and profile, destination policy and SSRF controls, accounting and audit, adapters, and
security conformance. Each child retains its own design, tests, review, and merge gates.
Neither this document nor the approval record authorizes implementation directly on the
tracker branch.

## Limitations

- The successful sandbox probe invoked no public endpoint, provider sandbox,
  credential, model, or billable service. An initial `uv run` stopped before the probe
  when host build-dependency retrieval encountered the previously documented package-CDN
  TLS failure; the retained result used the already prepared repository environment.
- The complete callback alternative was analyzed from the repository's existing
  composition contract; it was not implemented or benchmarked.
- No real resolver, redirect, TLS, decompression, or transport behavior has been proven.
- The mutable external sources may change; deterministic conformance must use injected
  fakes rather than pin public responses as release gates.
- The configured repository-relative product-manifest path was absent during this work.
  The accepted workspace-owned manifest revision is recorded in the
  [design record][design-record]; this evidence does not modify project configuration.

[design-record]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/54#issuecomment-5752829171
