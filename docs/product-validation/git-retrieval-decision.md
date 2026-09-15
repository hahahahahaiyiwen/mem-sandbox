# Conditional Git Retrieval Decision

**Decision date:** 2026-09-15  
**Source baseline:** `01b7fac8fe68225adefcef251f7b07d637d6c2b8`  
**Scope:** [#94](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/94), a
documentation-only decision child for Milestone 8 planning.

## Decision

Defer Git retrieval implementation.

MemSandbox should keep repository input host-controlled until a linked
[workflow-blocker trigger](./workflow-blocker-assessment.md#review-triggers) proves that
host-prepared artifacts are insufficient for a concrete workflow. Current Milestone 8
artifact exchange remains useful without Git: the host can acquire repository content
outside MemSandbox, seed bounded bytes, use full portable archives, and verify selected
outputs.

This decision does not implement or advertise a Git provider, provider archive API,
network client, subprocess, credential flow, repository manifest support, refresh
operation, push, or remote mutation. Approval of this document is not approval to run a
live/billable retrieval experiment.

## Initiating actor comparison

| Initiator | Authority model | Benefits | Risks / unresolved questions | Disposition |
|---|---|---|---|---|
| Host-prepared input | Application acquires content with its existing credentials and policy, then supplies sandbox-relative bytes or archives. | Smallest current authority surface; no model-selected host path or remote URL; works with current artifact APIs. | Host must own checkout/filtering and exact input provenance. | Current recommendation. |
| Agent-triggered request under immutable host grant | Host selects a narrow retrieval grant at create/resume; model may only narrow permitted repository/ref/path fields. | Could reduce host orchestration for proven repository workflows. | Needs authenticated authority, destination rules, egress/DNS/redirect policy, credential redaction, ref race handling and resource accounting. | Conditional future design only. |

Model input, restored snapshots, archive members, and `OwnerId` cannot grant repository
authority. Resume may use an equal or narrower grant only.

## Transport family comparison

| Transport family | Benefits | Limits and failure modes | Disposition |
|---|---|---|---|
| Provider archive API | Avoids exposing arbitrary Git commands; can pin returned archive to a commit; simpler fake-provider conformance. | Provider-specific auth/rate-limit/errors; archive may omit submodules/LFS; must handle redirects, decompression bounds, unsupported metadata and cleanup. | Preferred first candidate if a trigger proves retrieval is needed. |
| Constrained Git backend | More complete Git semantics and refs; can support refresh decisions explicitly. | Larger authority surface; must forbid hooks/filters/subprocess escape, push/remotes mutation, credential leakage, local config execution, LFS/submodule surprises and shell fallback. | Defer until provider archive is insufficient. |

Neither transport is implemented. A future backend must feed a workspace-owned artifact
candidate; it must not expose host checkout paths to the model.

## Initial import versus refresh

Initial import and refresh are separate product choices.

An initial import can publish a prepared tree into an empty or explicitly selected
destination. A refresh compares an existing workspace subtree against a pinned baseline.
Refresh must specify:

- requested ref and resolved commit SHA;
- baseline commit and workspace revision/root hash;
- stale-baseline rejection before publication;
- added, modified and deleted entries;
- preservation of local edits by default;
- explicit replacement, conflict rejection, or merge disposition;
- deterministic ordering and binary hashes;
- cleanup and unknown-outcome guidance.

No future operation should imply `git pull`. A ref race is resolved by pinning the
retrieved content to a commit and reporting that commit; a changed ref after resolution
does not silently change the published tree.

## Conditional provider boundary sketch

Names below are descriptive sketches, not importable APIs.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RepositoryRetrievalRequest:
    repository: RepositoryLocator
    ref: str
    destination: str
    allowed_hosts: tuple[str, ...]
    expected_commit: str | None = None
    expected_workspace_revision: int | None = None
    expected_workspace_root_hash: ContentHash | None = None
    limits: RepositoryTransferLimits = field(default_factory=RepositoryTransferLimits)
    operation_limits: OperationLimits = field(default_factory=OperationLimits)


@dataclass(frozen=True, slots=True)
class RetrievedRepositoryArtifact:
    resolved_commit: str
    entries: tuple[ArtifactInputFile | ArtifactInputDirectory, ...]
    provenance: ArtifactProvenance
    unsupported_features: tuple[str, ...]
```

A provider interface would be owned by the module consuming it, likely a future
repository-import component or a session-owned facade:

```python
class RepositoryArtifactProvider(Protocol):
    async def retrieve(
        self,
        request: RepositoryRetrievalRequest,
    ) -> RetrievedRepositoryArtifact: ...
```

The provider returns immutable facts and bytes for workspace validation. It does not
publish workspace state, choose host destinations, expose credentials, or run arbitrary
subprocess arguments.

## Required authority, limits, and redaction

A future implementation must define:

- public/private repository authority and credential ownership;
- allowed repository hosts, redirects, DNS and egress boundaries where applicable;
- destination scope inside the virtual workspace;
- transfer, decompression, entry count, file, path, time and temporary-storage limits;
- cancellation before publication and cleanup after failed retrieval/import;
- content-free events with source class, resolved commit, counts, sizes, stable reason
  codes and publication outcome;
- redaction rules for tokens, signed URLs, headers, private remotes, host paths and
  provider error text.

Unsupported features must fail explicitly or appear as bounded unsupported facts before
publication. These include submodules, LFS pointers/content, symbolic links, executable
hooks, smudge/clean filters, `.git` control directories, permissions, device nodes,
sparse archives and unsupported compression formats.

## Fake-provider conformance plan

Future tests should use a fake provider and workspace/session spies:

| Case | Required evidence |
|---|---|
| No grant / denied repository | Provider is not contacted; workspace state is unchanged. |
| Pinned happy path | Requested ref resolves to a commit; published files and hashes match immutable provider output. |
| Ref race | A moving ref is reported as the resolved commit; expected-commit mismatch rejects before publication. |
| Private credential path | Credentials are supplied by host-owned provider configuration and never appear in request echoes or events. |
| Unsupported metadata | Submodules, LFS, links, hooks/filters and `.git` control entries fail or are reported according to the approved policy before publication. |
| Limit exact/one-over | Exact transfer/decompression/file/path/entry limits pass; one-over cases fail with unchanged workspace identity. |
| Retrieval failure | Provider error prevents workspace preparation/publication and records a sanitized stable reason. |
| Import failure after retrieval | Retrieved bytes may be charged/settled, but workspace state remains unchanged. |
| Post-commit event/cleanup failure | Committed publication is not reported as rollback; retry requires re-reading state. |
| Refresh conflict | Local edits, stale baseline and replacement/conflict/merge choices are handled deterministically. |

These are planned tests only.

## Future trigger and approval requirements

Before any retrieval implementation work starts, the project needs all of:

1. a linked #77 workflow-blocker trigger showing host-prepared input is insufficient;
2. a separate detailed design approval selecting initiating actor, transport family,
   authority, limits, update semantics and cleanup behavior;
3. a narrowly scoped implementation issue with behavior tests and fake-provider
   conformance;
4. explicit permission for any live/billable provider evaluation.

Absent that evidence, Git retrieval remains deferred and no speculative implementation
issue is created by this decision.
