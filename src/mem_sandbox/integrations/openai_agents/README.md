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

## Milestone 5.2 design

Milestone 5.2 adds the SDK client/session profile while keeping all filesystem,
execution, lifecycle, and archive rules in their owning core modules. The adapter owns
only SDK state plus translation.

The provider discriminator is `mem_sandbox`. Public provider types are:

- `InMemorySandboxClientOptions`
- `InMemorySandboxSessionState`
- `InMemorySandboxSession`
- `InMemorySandboxClient`

The client advertises SDK default-option support. When `SandboxRunConfig` omits
`options`, creation uses a fresh `InMemorySandboxClientOptions()` instance.

The client owns exactly one injected `SandboxService`. Each provider session owns one
opaque `SandboxHandle` and one public `SandboxSession`. The handle UUID is serialized as
`sandbox_handle` only so a client using the same live service can attempt reattachment;
the adapter never treats it as authorization or reconstructs a handle for a different
service instance.

Resume follows two paths:

1. Reattach when the serialized handle still resolves in the injected service.
2. Allocate a replacement when it does not, then let the SDK snapshot lifecycle hydrate
   the replacement from the provider archive metadata in state.

Only `SandboxNotFound` selects replacement allocation. Other service lookup failures
propagate without allocating a divergent workspace.

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

## Portable snapshot bridge

Durable SDK snapshot persistence writes only `WorkspaceArchiveData.encoded` and commits
the following JSON-safe metadata in `InMemorySandboxSessionState` together with the new
snapshot identity:

- `workspace_archive_format_version`
- `workspace_archive_revision`
- `workspace_archive_root_hash`

Hydration requires all three values. The adapter reads the incoming `IOBase` incrementally
under `max_stream_bytes`, rejects text or unsupported stream values, and passes one
`WorkspaceArchiveData` object to the public core restore operation. A malformed,
oversized, or metadata-mismatched archive leaves the live workspace unchanged.
Persistence enforces the same stream limit and writes each archive under a fresh snapshot
identity. The provider publishes that identity and its metadata together only after the
snapshot backend accepts the matching bytes. Failed or uncertain snapshot writes therefore
leave the previous metadata and payload pair usable for replacement resume.

Direct `persist_workspace()` calls return a raw-byte in-process stream that carries its
archive metadata transiently for a matching direct `hydrate_workspace()` call. They do not
modify serialized resume metadata or the durable snapshot identity.

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
- Translate requests and results; do not duplicate core validation or mutation behavior.
- Reject unsupported SDK features explicitly and never use the host filesystem or shell
  as a fallback.
- Keep serialized provider state JSON-compatible and free of live sessions, services,
  handles as Python objects, credentials, secrets, and host paths.
- Validate the complete manifest profile before allocating or mutating core state.
- Preserve SDK instrumentation by returning `BaseSandboxClient._wrap_session(inner)`.
- Do not introduce a cross-framework adapter abstraction until another implemented SDK
  proves identical reusable behavior.
