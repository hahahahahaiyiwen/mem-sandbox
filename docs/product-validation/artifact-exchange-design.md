# Bounded Artifact Exchange Design

**Design date:** 2026-09-15  
**Source baseline:** `72a1b3fb6d99181d8103dfe2ae281cb39be7bf60`  
**Scope:** [#92](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/92), a
documentation-only design child for Milestone 8 planning.

## Design stance

MemSandbox should keep artifact exchange reuse-first until a linked
[workflow-blocker trigger](./workflow-blocker-assessment.md#review-triggers) proves that
current host composition cannot reasonably satisfy a concrete workflow. The current
[artifact-workflow audit](./artifact-workflow-audit.md) shows that host-owned bytes,
binary session operations, full portable archive export/restore, and revision/root-hash
guards already support deterministic bounded input and verified output without adding a
new public API.

This design therefore records conditional semantics for a future owning-module facade,
not accepted implementation scope. Proposed contracts below are sketches for a later
approved issue; they are not importable symbols and do not change current archive,
session, adapter, Git, network, or host-filesystem behavior.

## Current reusable path

The default recommendation is to compose existing host-only operations:

1. The host acquires input bytes using its own authority, credentials, network, package,
   repository, or filesystem mechanisms.
2. The host seeds initial files through `CreateSandboxRequest.initial_files` or writes
   bytes through `SandboxSession.write_bytes`.
3. The workspace enforces path normalization, quotas, content hashes, and one-operation
   mutation atomicity.
4. The host reads selected result paths with `read_bytes` and verifies exact content
   hashes, or retains `WorkspaceArchiveData` for a complete portable archive.
5. Whole-workspace replacement uses `restore_portable_archive` with both expected current
   workspace revision and root hash when stale-target protection is required.

This path intentionally does not grant the model authority to choose host paths,
destination paths, remote URLs, repository credentials, Git configuration, or arbitrary
archive extraction.

## Boundary ownership

| Concern | Owner | Design decision |
|---|---|---|
| Path normalization, containment, node validation, quotas, hashes and prepared candidate publication | `mem_sandbox.workspace` | Keep archive parsing, content-tree validation, deterministic ordering and atomic publication workspace-owned. |
| Session operation admission, deadlines, cancellation, event delivery and revision/root-hash restore guards | `mem_sandbox.session` | Expose any future host-facing facade as session-owned orchestration over workspace-owned candidates. |
| Host input acquisition, credentials, repository checkout, destination selection and exact output assertions | Host application | Do not encode host filesystem or network authority into core request types. |
| SDK translation | Optional adapter package | Keep framework manifests separate from core contracts; adapter defaults must not imply host import/export authority. |
| Product evidence and trigger evaluation | Product-validation docs and issues | Treat this document as planned semantics, not passing implementation evidence. |

No global artifact interface bucket is needed. If future collaborators are introduced,
their interfaces should be narrow and owned by the module consuming them.

## Conditional contract sketches

The names in this section are descriptive sketches for a future issue. They should be
converted to concrete dataclasses and protocols only after a separate approval supplies a
reproducible blocked workflow.

### Host content-tree input

```python
@dataclass(frozen=True, slots=True)
class ArtifactInputFile:
    path: str
    content: bytes
    content_hash: ContentHash | None = None


@dataclass(frozen=True, slots=True)
class ArtifactInputDirectory:
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PublishArtifactTreeRequest:
    entries: tuple[ArtifactInputFile | ArtifactInputDirectory, ...]
    expected_current_revision: int | None = None
    expected_current_root_hash: ContentHash | None = None
    conflict_policy: Literal["reject"] = "reject"
    provenance: ArtifactProvenance | None = None
    limits: ArtifactTransferLimits = field(default_factory=ArtifactTransferLimits)
    operation_limits: OperationLimits = field(default_factory=OperationLimits)
```

The request carries sandbox-relative paths and bytes only. It must reject host paths,
absolute host destinations, credentials, sensitive URLs, model-selected repository URLs,
symbolic links, device nodes, sparse files, path aliases, duplicate entries, and entries
that exceed workspace or transfer limits. If one baseline guard is supplied, both
revision and root hash must be supplied.

The recommended conflict policy is reject-only for the first facade. Replacement and
overlay semantics are materially different product choices:

- **Replacement** matches current whole-archive restore but can delete unrelated files.
- **Overlay** preserves unrelated files but needs explicit conflict and delete semantics.
- **Conflict rejection** is simplest, safest, and easiest to prove at behavior seams.

### Selected output export

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ExportArtifactSelectionRequest:
    paths: tuple[str, ...]
    include_empty_directories: bool = True
    expected_current_revision: int | None = None
    expected_current_root_hash: ContentHash | None = None
    limits: ArtifactTransferLimits = field(default_factory=ArtifactTransferLimits)
    operation_limits: OperationLimits = field(default_factory=OperationLimits)


@dataclass(frozen=True, slots=True)
class ExportedArtifact:
    path: SandboxPath
    kind: Literal["file", "directory"]
    content: bytes | None
    content_hash: ContentHash
```

Current behavior can already read selected file bytes one path at a time. A future
selected export would be a convenience for producing one deterministic, bounded result at
one workspace identity. It must not choose or write a host destination; the host receives
bytes and decides what to do with them.

### Input-bound change report

```python
@dataclass(frozen=True, slots=True)
class ArtifactChange:
    path: SandboxPath
    kind: Literal["file", "directory"]
    change: Literal["added", "modified", "deleted"]
    before_hash: ContentHash | None
    after_hash: ContentHash | None


@dataclass(frozen=True, slots=True)
class ArtifactChangeReport:
    baseline_revision: int
    baseline_root_hash: ContentHash
    current_revision: int
    current_root_hash: ContentHash
    changes: tuple[ArtifactChange, ...]
```

An input-bound report compares the current workspace against a host-supplied baseline
identity and deterministic entry set. It must include added, modified and deleted paths,
binary content hashes, empty-directory changes, and stable path ordering. A stale or
unknown baseline fails explicitly rather than silently comparing against the wrong tree.

## Consistency and failure semantics

One workspace/session method call remains the transaction boundary. Multi-call host
composition can be correct for exclusive workflows, but it is not atomic across the
sequence. A future facade should validate into a private prepared candidate and publish
once under the workspace lock.

| Condition | Required behavior |
|---|---|
| Invalid request, denied policy, unsupported node type, duplicate path, traversal or quota overflow | Reject before publication and leave revision/root hash unchanged. |
| Exact limit | Accept when every workspace and transfer limit is exactly satisfied. |
| One over limit | Reject the specific exceeded limit without conflating file, node, path, archive/input or transfer budgets. |
| Stale baseline revision/root-hash pair | Raise the session/workspace changed error before publication. |
| Cancellation or deadline before publish | Surface cancellation/deadline and leave live state unchanged. |
| Cancellation after authoritative publish | Report the committed mutation as authoritative; do not claim rollback. |
| Required start event failure | Do not invoke the mutating collaborator. |
| Required terminal event or cleanup failure after commit | Surface the error and require re-read before retry because outcome may already be committed. |

Failure responses should include stable error categories and enough workspace identity to
support safe recovery. They should not include credentials, host paths, sensitive URLs or
raw input content unless the host explicitly requested that content as output.

## Provenance and transfer limits

Provenance is host-supplied metadata for auditability, not authority. It may include a
non-sensitive label, source kind, declared content hash, and host-owned correlation ID. It
must not include local filesystem paths, tokens, signed URLs, private repository remotes,
or model-provided claims that imply permission.

Transfer limits are separate from current workspace quotas. A future request should
bound:

- total materialized input bytes;
- total materialized output bytes;
- number of entries;
- path and segment lengths, preserving existing workspace rules;
- archive or encoded-result bytes where a packed representation is returned.

Cumulative accounting across operations and tenants remains a separate conditional design
concern for [#93](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/93); this issue
does not claim that budget exists today.

## Repository and archive compatibility

The generic portable archive profile is intentionally workspace-focused. It currently
does not ban a relative `.git/config` entry only because the path is repository-related.
A future repository importer must define repository-specific filtering, including `.git`
and unsupported metadata exclusion, without changing the compatibility promise of the
general archive format by accident.

`GitRepo` manifests, remote clone/fetch, branch selection and dependency retrieval remain
outside the current core capability. Inputs never authorize arbitrary host paths or
model-supplied repository URLs. The
[conditional Git retrieval decision](./git-retrieval-decision.md) keeps repository
retrieval deferred until a concrete trigger justifies host-granted provider work.

## Behavior-seam test matrix

Future implementation should test module seams with concrete classes under test and
small fakes/spies for collaborators:

| Case | Expected evidence |
|---|---|
| Happy path content-tree publication | Result paths, file bytes, empty directories, revision increment and root hash match expected deterministic values. |
| Selected export | Returned entries are deterministic, bounded, include expected hashes, and do not write to host destinations. |
| Input-bound diff | Added, modified, deleted and empty-directory changes are ordered deterministically with correct before/after hashes. |
| Policy denial | Session admission rejects before workspace publication and before mutating collaborator calls. |
| Invalid input | Traversal, absolute host paths, aliases, duplicates, unsupported metadata and malformed archive/content trees fail with stable errors. |
| Exact limit and one-over | Exact file/node/path/transfer limits pass; one-over cases fail the precise limit with unchanged state. |
| Stale baseline | Revision/root-hash mismatch rejects before publication. |
| Cancellation/deadline | Pre-publication cancellation leaves state unchanged; post-publication completion is not reported as rollback. |
| Event failure | Start failure prevents mutation; terminal failure after commit requires re-read before retry. |
| Provenance sanitization | Results include only bounded non-sensitive labels and never echo host paths, credentials or sensitive URLs. |
| Collaborator interactions | Constructor-injected policy/event/workspace collaborators are called once in the expected order, with immutable domain request types. |

These are planned tests, not passing evidence for the current documentation-only issue.

## Acceptance disposition

This design supports a reuse-only conclusion for current Milestone 8 planning. New
artifact-import/export APIs remain conditional on a linked blocked workflow and a
separate approved implementation issue. Completing this design does not lift the #77
deferral, complete the parent milestone, or approve future API choices.
