# OpenAI Agents SDK Integration

## Purpose and ownership

This package owns translation between MemSandbox public contracts and the OpenAI Agents
SDK sandbox client, session, state, snapshot, and capability APIs. It does not own
filesystem semantics, command execution, lifecycle state, policy, secrets, events, or
snapshot content.

Install the optional dependency with:

```text
pip install "mem-sandbox[openai-agents]"
```

The supported SDK range is `openai-agents>=0.22,<0.23`. Contract tests execute against
exactly `0.22.0`.

## Public surface

The integration exports:

- `InMemorySandboxClientOptions`
- `InMemorySandboxSessionState`
- `InMemorySandboxSession`
- `InMemorySandboxClient`
- `InMemorySandboxSnapshot`
- `InMemorySandboxSnapshotSpec`
- `InMemorySandboxCapability`

## Milestone 5.2 client and session design

Milestone 5.2 adds the SDK client/session profile while keeping all filesystem,
execution, lifecycle, and archive rules in their owning core modules. The adapter owns
only SDK state plus translation.

The provider discriminator is `mem_sandbox`.

The client advertises SDK default-option support. When `SandboxRunConfig` omits
`options`, creation uses a fresh `InMemorySandboxClientOptions()` instance.

The host owns the injected `SandboxService` lifetime. The client consistently uses that
one service, while each provider session references one opaque `SandboxHandle` and one
public `SandboxSession`. Provider state serializes both `sandbox_handle` and the expected
public core `session_id`. A live lookup is reused only when both identities match. This
pair is a consistency check, not authorization; applications still authorize handle and
snapshot access.

Provider state has an explicit schema version. Version 1 also records the snapshot
checkpoint's cwd and approved environment. Those fields have root/empty defaults so
payloads serialized before their addition remain readable without a version bump.
Unknown fields, unsupported versions, malformed identities, invalid execution context,
incomplete archive metadata, and unsupported manifest state are rejected before service
lookup or allocation.

Resume follows two paths:

1. Reattach when the serialized handle resolves in the injected service and the core
   session identity matches.
2. When the handle is absent or identifies another core session, require a restorable
   snapshot, allocate an independent replacement, and let the SDK snapshot lifecycle
   hydrate it from the provider archive metadata in state.

`SandboxNotFound` and identity mismatch select replacement allocation. A mismatched live
session is never attached to or deleted. Other service lookup failures propagate without
allocating a divergent workspace. If the snapshot is unavailable, resume fails before
allocation rather than returning an empty workspace.

Repeated resumes from state whose original handle is unavailable create independent
snapshot-backed forks without mutating the caller's state. Multiple resumes while the
original handle is live are wrapper aliases over the same core session; deletion through
any alias removes the shared backend handle.

Passing an already-restorable snapshot to `create()` is unsupported in profile 1 because
there is no matching provider state carrying the archive metadata. The client checks and
rejects that case before core allocation. SDK-managed resume state is serialized after
cleanup calls `stop()`, so successful provider persistence updates the metadata before the
state is saved.

The public core audit confirmed these reusable contracts:

| Required behavior | Public core contract |
|---|---|
| Complete binary reads and writes | `SandboxSession.read_bytes` and `SandboxSession.write_bytes` |
| Metadata and directory listing | `SandboxSession.stat` and `SandboxSession.list_entries` |
| Owned lifecycle | `SandboxService.create`, `get_session`, `resume`, `delete`, and `close` |
| Portable workspace snapshots | A narrow archive capability owned by the service runtime and exposed through the public session boundary |
| Constrained command execution | `SandboxSession.execute` |

The owning core archive seam is deliberately narrow:

- `SandboxSession.export_portable_archive()`
- `SandboxSession.restore_portable_archive(WorkspaceArchiveData)`

The session delegates those operations to its injected `SessionWorkspaceSnapshotPort`.
The default service runtime injects the same `MemoryWorkspace` that owns validation,
quotas, decompressed-size limits, entry-count limits, path validation, and atomic
publication. The OpenAI adapter must not access `MemoryWorkspace` directly or parse
archive members.

## Manifest profile version 1

The profile accepts only a lossless synthetic workspace:

| Manifest surface | Version 1 behavior |
|---|---|
| `version` | Exactly `1` |
| `root` | Exactly `/workspace` |
| Entries | Recursive synthetic `Dir` and `File` only |
| File content | Preserved as arbitrary bytes |
| Empty directories | Preserved |
| Description | Accepted because it has no runtime effect |
| Permissions | Only the SDK default permissions for that entry kind |
| Group/owner metadata | Must be absent |
| `ephemeral` | Must be `False` |
| Environment, users, groups | Must be empty |
| Host grants, local files/directories | Rejected |
| Git, mounts, symlinks, devices | Rejected |
| Remote mount command allowlist | Must equal the SDK default; it is inert because mounts are rejected |
| Exposed ports and PTY | Rejected |
| Executable hooks | Rejected |

Validation is complete and synchronous before `SandboxService.create()` is called. The
adapter then flattens supported entries to public `WorkspaceSeedFile` values and explicit
directory requests. Duplicate normalized paths, file-as-parent conflicts, root targets,
file-size violations, aggregate-byte violations, and explicit or implicit node-count
violations are rejected before allocation. The adapter never invokes SDK metadata
commands such as `chmod` or `chgrp`.

## Native SDK session profile

- `read` and `write` translate complete binary streams to public session operations.
- SDK users are unsupported; any non-`None` `user` is rejected before a core call.
- `ls`, `mkdir`, `rm`, and path validation use native public session operations.
- SDK archive `extract` is rejected before reading input because its inherited implementation
  may spill to host temporary storage. Portable whole-workspace hydration uses the bounded
  snapshot bridge instead.
- `_exec_internal` translates argv to the constrained virtual command language. It never
  starts a host process or performs host executable lookup. Every argv element remains one
  literal parser token; separators, quotes, expansion syntax, glob characters, and empty
  strings cannot gain syntax meaning. NUL-containing arguments are rejected.
- `exec(..., shell=True)` and custom shell prefixes are rejected instead of accepting the
  SDK's default `sh -lc` wrapping. Callers must use `shell=False` or the later
  adapter-owned capability profile.
- PTY and exposed-port operations remain unsupported.
- SDK snapshot fingerprint helpers are disabled.
- Live reattachment overrides the SDK fingerprint decision and reuses the proven same
  service handle without restoring an older snapshot.
- SDK workspace pre-clear on resume is disabled because core hydration validates first
  and publishes exactly once.
- The pinned instrumentation wrapper is retained, while its non-delegating
  `apply_manifest`, `_apply_entry_batch`, and `extract` hooks are rebound to the provider.
- Live manifest applications and runtime entry batches are staged in a temporary
  service-owned sandbox, cleaned up, and then published through a revision-and-root-hash
  conditional public atomic archive restore.

## Milestone 5.3 four-tool capability

`InMemorySandboxCapability` is the model-facing profile for the OpenAI runner. Configure
it explicitly on `SandboxAgent`; doing so replaces the SDK's default shell, filesystem,
and compaction capability set:

```python
from agents import RunConfig, Runner
from agents.sandbox import SandboxAgent, SandboxRunConfig
from mem_sandbox.integrations.openai_agents import InMemorySandboxCapability

agent = SandboxAgent(
    name="sandboxed",
    capabilities=[InMemorySandboxCapability()],
)
result = await Runner.run(
    agent,
    "Update the workspace",
    run_config=RunConfig(sandbox=SandboxRunConfig(session=sdk_session)),
)
```

The SDK clones the capability for each run and binds the clone to the live provider
session. Binding accepts the concrete `InMemorySandboxSession` and the SDK's
instrumented wrapper around that concrete provider only. The model cannot provide a
handle or session ID, and the capability performs no service lookup. The original
capability stays unbound and neither the capability nor its tools close or delete the
host-owned session.

Profile 1 rejects a non-empty `SandboxRunConfig.cwd`. Relative file paths, expected
hashes, patch headers, and commands all continue to resolve through the core session cwd.
This avoids parsing or rewriting the approved opaque patch contract. A future scoped-cwd
profile must define and test those translation rules explicitly.

The profile exposes exactly:

| Tool | Input |
|---|---|
| `execute` | `command`, optional `timeout_seconds`, optional `max_output_bytes` |
| `read_file` | `path`, optional `start_line`, optional `end_line` |
| `write_file` | `path`, `content`, explicit `write_condition`, optional `expected_hash`, optional `create_parents` |
| `apply_patch` | `patch`, `expected_hashes[]` containing `path` and `content_hash` |

`execute` uses the constrained MemSandbox command language, never a host shell.
`max_output_bytes` independently bounds stdout and stderr. Write conditions map exactly
to `AnyCurrentState`, `PathMustNotExist`, or `ContentHashMustEqual`; omission of an
expected hash never implies overwrite. Patch input retains the core's atomic multi-file
contract. Duplicate expected-hash paths, including aliases that normalize to the same
workspace path, are rejected as correctable tool input before an operation starts.

Successful calls return a JSON object with `ok: true` and the complete JSON-safe domain
result, including operation metadata and all result-specific hash, truncation, cwd, and
environment fields. Expected domain failures return `ok: false` with stable category,
code, safe message, `correctable`, `retryable`, and an operation ID when present.
Policy denials are terminal. Timeouts are retryable. Cancellation propagates to the
runner. Unexpected and internal failures are redacted and include a correlation ID.

The capability intentionally does not expose `sh -lc`, arbitrary shell selection, PTY,
image viewing, host filesystem access, lifecycle operations, snapshots, policy
configuration, or secret grants.

## Portable snapshot bridge

Durable SDK snapshot persistence writes only `WorkspaceArchiveData.encoded` and commits
the following JSON-safe metadata in `InMemorySandboxSessionState` together with the new
snapshot identity:

- `workspace_archive_format_version`
- `workspace_archive_revision`
- `workspace_archive_root_hash`
- `cwd`
- `approved_environment`

Hydration requires all three archive values. Replacement resume atomically publishes the
validated cwd and approved environment with the restored workspace, while direct
`hydrate_workspace()` keeps the target session's current execution context. The adapter
reads the incoming `IOBase` incrementally
under `max_stream_bytes`, rejects text or unsupported stream values, and passes one
`WorkspaceArchiveData` object to the public core restore operation. A malformed,
oversized, or metadata-mismatched archive leaves the live workspace unchanged.
Persistence enforces the same stream limit and writes each archive under a fresh snapshot
identity when workspace metadata changes, while unchanged persistence reuses the current
durable identity. The provider publishes a new identity and its metadata together only
after the snapshot backend accepts the matching bytes. Failed or uncertain snapshot writes
therefore leave the previous metadata and payload pair usable for replacement resume.

Direct `persist_workspace()` calls return a raw-byte in-process stream that carries its
archive metadata transiently for a matching direct `hydrate_workspace()` call. They do not
modify serialized resume metadata or the durable snapshot identity.

## Snapshot store integration

`FactorySnapshotStore` remains the framework-neutral persistence extension point.
`InMemorySandboxSnapshot` is the OpenAI `SnapshotBase` bridge, and
`InMemorySandboxSnapshotSpec` lets the SDK assign each initial snapshot identity. Configure
the client with the same store abstraction used elsewhere in MemSandbox:

```python
client = InMemorySandboxClient(
    service,
    snapshot_store=snapshot_store,
    clock=clock,
)
snapshot = InMemorySandboxSnapshotSpec()
```

The client binds the live store and clock into cloned SDK `Dependencies`; neither object
is serialized. Create/resume snapshot preflight also uses a temporary dependency clone
that is closed immediately, so owned factory results never accumulate in the client
template. The bridge stores a canonical envelope containing the portable archive bytes,
archive format, workspace revision, and root hash under the store format
`openai-portable-workspace`. It also records owner provenance and rejects mismatched
owners, formats, schema versions, sizes, payload hashes, and archive metadata.
Owner provenance is a consistency tag checked against provider state, not an authorization
boundary; the application must authorize access to serialized state and snapshot IDs.

Unchanged repeated persistence reuses the current immutable snapshot. Changed workspaces
receive new snapshot identities so previously serialized states remain independently
resumable. Store quotas and expiration therefore remain explicit host policy rather than
being bypassed by destructive adapter cleanup. If a changed save exceeds store quota,
`stop()` fails and leaves the previous serialized snapshot pair authoritative; hosts
should size or purge the store according to expected durable-stop frequency.

`NoopSnapshot` remains available when recovery is intentionally unnecessary. Explicit
third-party SDK snapshot providers are caller-owned extensions; the adapter never selects
one as a fallback.

## Lifecycle ownership

- A newly allocated create or replacement-resume handle is cleaned up if adapter
  construction or SDK startup fails.
- Cleanup is cancellation-resilient and bounded by the session lifecycle timeout. A
  cleanup failure or timeout is secondary context on the original failure.
- Failed startup also closes the session-scoped SDK dependency clone so owned factory
  results do not outlive the terminal provider session.
- Failed startup of a live reattachment preserves the pre-existing backend because the
  attempt allocated no resource.
- Closing or stopping an unstarted replacement preserves the original durable snapshot;
  the manifest-only replacement is not persisted before successful hydration.
- SDK `stop()` persists only. SDK `aclose()` performs SDK stop/shutdown and dependency
  cleanup but does not delete the backend.
- Provider `stop()` and `shutdown()` share one lifecycle lock; shutdown marks the SDK
  session closing before dependency cleanup, so concurrent persistence cannot use closed
  dependency resources.
- `client.delete()` is the authoritative backend release and is safe after prior or
  concurrent deletion. Later `aclose()` calls do not attempt to persist a deleted backend.

## Error translation

The adapter preserves core errors as causes while presenting SDK-shaped boundary errors:

| Condition | OpenAI SDK error |
|---|---|
| Path escapes or invalid absolute path | `InvalidManifestPathError` |
| Write stream is not binary | `WorkspaceWriteTypeError` |
| Archive stream, metadata, or core archive validation fails | `WorkspaceArchiveReadError` |
| Unsupported manifest/options/profile feature | `ValueError` with the rejected field or entry type |
| Unsupported SDK user, shell, PTY, or port behavior | `ValueError` with the unsupported feature |

Normal command non-zero results remain `ExecResult` values. Core cancellation and timeout
remain exceptional and retain their original causes so later capability translation can
map them without losing domain classification. An SDK execution timeout is forwarded into
both the core operation and command-execution limits.

## Product conformance

The model-free reference scenario passes through the direct service/session, OpenAI
sandbox client/session, and OpenAI capability drivers with one identical normalized
trace and correctness checksum. The trace covers revisions, file hashes, guarded patch
and stale-write behavior, snapshot workspace root and complete-state hashes, cwd,
approved environment, SDK lifecycle rejection, replacement identity, fork isolation,
and live/snapshot cleanup.

Product validation lives in `benchmarks.validation` rather than this runtime adapter.
The OpenAI sandbox driver uses the documented `core_session` seam for revisioned and
preconditioned operations that are not represented by the SDK's generic binary stream
methods. The capability driver exercises only `execute`, `read_file`, `write_file`, and
`apply_patch`. Neither path performs a model or provider-network call.

## Compatibility policy

OpenAI Sandbox Agents are beta. Any change to the pinned abstract methods, method
signatures, state or run-configuration fields, lifecycle ordering, serialization,
capability cloning/binding, or snapshot protocol must fail contract tests and receive an
explicit compatibility review.

Support is limited to the documented `>=0.22,<0.23` range. Expanding that range requires
running the contract and conformance suites against the proposed versions and updating
this README and the integration design documents.

## Maintenance rules

- Import MemSandbox behavior only from public package exports such as
  `mem_sandbox.session` and `mem_sandbox.service`.
- Keep OpenAI-native types and dependencies inside this package.
- Use constructor injection for core service dependencies. The SDK-required capability
  `bind` hook may attach the live session to a per-run clone but must not perform global
  lookup.
- Keep the provider-to-domain capability seam read-only. Resolving the SDK's instrumented
  wrapper is a pinned compatibility concern owned by this package.
- Translate requests and results; do not duplicate core validation or mutation behavior.
- Reject unsupported SDK features explicitly and never use the host filesystem or shell
  as a fallback.
- Keep serialized provider state JSON-compatible and free of live sessions, services,
  handles as Python objects, credentials, secrets, and host paths.
- Validate the complete manifest profile before allocating or mutating core state.
- Preserve SDK instrumentation by returning `BaseSandboxClient._wrap_session(inner)`.
- Do not introduce a cross-framework adapter abstraction until another implemented SDK
  proves identical reusable behavior.
