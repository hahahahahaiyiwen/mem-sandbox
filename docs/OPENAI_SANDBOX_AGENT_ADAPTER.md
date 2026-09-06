# OpenAI `SandboxAgent` Backend Contract

**Pinned SDK:** `openai-agents==0.22.0`
**Pinned source:** commit
[`89c02c8`](https://github.com/openai/openai-agents-python/tree/89c02c828ee8510fe9a84ee6675608193aa13b02)
**Status:** Milestone 5.2 client/session and manifest profile implemented with
behavior-first tests. Sandbox Agents are beta.

## Package boundary

Install the integration dependency with:

```text
pip install "mem-sandbox[openai-agents]"
```

The production namespace is `mem_sandbox.integrations.openai_agents`. Core packages must
remain importable without the optional SDK dependency. The integration package may import
OpenAI SDK types and public MemSandbox contracts, but it must not import concrete
workspace implementations.

## Direct answer

OpenAI does not ask a provider to implement one generic `Sandbox` object. A custom
provider implements this object graph:

```text
SandboxAgent
    |
Runner + SandboxRunConfig
    |
BaseSandboxClient              create / resume / delete
    |
SandboxSession                 SDK instrumentation wrapper
    |
BaseSandboxSession subclass    execution, files, lifecycle, snapshots
    |
framework-neutral sandbox core
```

For the in-memory project, the OpenAI-specific adapter should therefore contain four
types:

1. `InMemorySandboxClientOptions`
2. `InMemorySandboxSessionState`
3. `InMemorySandboxSession`
4. `InMemorySandboxClient`

The client is passed to `SandboxRunConfig`; no global provider registration is required.
The options and state subclasses register their `type` discriminators automatically for
configuration and saved-state round trips.

## 1. Exact abstract API

### Client options

Subclass
[`BaseSandboxClientOptions`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/sandbox_client.py)
and give `type` a non-empty string default, normally a `Literal`:

```python
class InMemorySandboxClientOptions(BaseSandboxClientOptions):
    type: Literal["mem_sandbox"] = "mem_sandbox"
    owner_id: str = "openai-agents"
    workspace_limits: WorkspaceLimits = WorkspaceLimits()
    max_stream_bytes: int = 32 * 1024 * 1024
    manifest_profile_version: Literal[1] = 1
    exposed_ports: tuple[int, ...] = ()
```

The model is frozen. The discriminator should equal the client's `backend_id`.
`max_stream_bytes` bounds adapter-side buffering before data reaches the core.
`exposed_ports` exists only to reject non-empty configuration explicitly in profile 1.

### Session state

Subclass
[`SandboxSessionState`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/sandbox_session_state.py).
The base fields are:

```python
type: str
session_id: UUID
snapshot: SnapshotBase
manifest: Manifest
exposed_ports: tuple[int, ...]
snapshot_fingerprint: str | None
snapshot_fingerprint_version: str | None
workspace_root_ready: bool
```

Add only the provider identity and immutable configuration needed to reconnect or
recreate the backend. Do not serialize credentials.

```python
class InMemorySandboxSessionState(SandboxSessionState):
    type: Literal["mem_sandbox"] = "mem_sandbox"
    sandbox_handle: str
    owner_id: str
    workspace_limits: WorkspaceLimits
    max_stream_bytes: int
    manifest_profile_version: Literal[1] = 1
    workspace_archive_format_version: int | None = None
    workspace_archive_revision: int | None = None
    workspace_archive_root_hash: str | None = None
```

`sandbox_handle` is the string form of the service's opaque process-local handle. It is a
reattachment hint for the same live service, not a durable workspace identity or an
authorization token. The three archive metadata fields remain outside the tar bytes so
equivalent workspace trees produce identical archives even when their revision histories
differ. They are required when restoring a provider-produced snapshot; version 1 rejects
a bare snapshot stream that has no matching provider state metadata.

### Session

These are all the abstract methods on
[`BaseSandboxSession`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/base_sandbox_session.py):

```python
async def _exec_internal(
    self,
    *command: str | Path,
    timeout: float | None = None,
) -> ExecResult: ...


async def read(
    self,
    path: Path,
    *,
    user: str | User | None = None,
) -> io.IOBase: ...


async def write(
    self,
    path: Path,
    data: io.IOBase,
    *,
    user: str | User | None = None,
) -> None: ...


async def running(self) -> bool: ...


async def persist_workspace(self) -> io.IOBase: ...


async def hydrate_workspace(self, data: io.IOBase) -> None: ...
```

`persist_workspace()` must produce a portable tar byte stream whose members are relative
to the workspace root. `hydrate_workspace()` must safely restore that stream underneath
the current workspace root.

### Client

These are all the abstract methods on `BaseSandboxClient`:

```python
async def create(
    self,
    *,
    snapshot: SnapshotSpec | SnapshotBase | None = None,
    manifest: Manifest | None = None,
    options: ClientOptionsT,
) -> SandboxSession: ...


async def delete(self, session: SandboxSession) -> SandboxSession: ...


async def resume(
    self,
    state: SandboxSessionState,
) -> SandboxSession: ...


def deserialize_session_state(
    self,
    payload: dict[str, object],
) -> SandboxSessionState: ...
```

The custom client must return `self._wrap_session(inner)`, not the raw
`BaseSandboxSession` subclass. The wrapper supplies SDK tracing, audit events, and
dependency lifecycle behavior.

## 2. Who actually calls the session methods?

The model does not directly call the six abstract methods. The model sees tools supplied
by `SandboxAgent.capabilities`. Those tools call the public `SandboxSession` API, and the
public API eventually delegates to provider hooks.

```text
model
  -> capability tool
     -> public SandboxSession operation
        -> custom BaseSandboxSession implementation
           -> framework-neutral sandbox core
```

The important call paths are:

| Provider method | Typical caller | Functionality |
|---|---|---|
| `_exec_internal` | `session.exec()`, normally through the `exec_command` tool | Executes one prepared command and returns stdout, stderr, and an exit code |
| `read` | Image/file capabilities, patching, archive logic, manifest logic, or custom tools | Returns the complete binary content of one file as a stream |
| `write` | Patching, manifest materialization, archive logic, or custom tools | Writes a complete binary or text payload |
| `running` | `Runner` and session lifecycle management | Checks whether the backing environment is available |
| `persist_workspace` | `session.stop()` and snapshot lifecycle | Exports the complete workspace for persistence |
| `hydrate_workspace` | `session.start()` while restoring a snapshot | Rebuilds the workspace from persisted content |

Therefore:

- `_exec_internal`, `read`, and `write` are operation primitives that may support
  model-visible tools.
- `running`, `persist_workspace`, and `hydrate_workspace` are primarily runtime lifecycle
  primitives. They are not normally exposed as tools to the model.
- Supporting a provider method does not automatically expose it to the model. A
  capability must provide the corresponding tool.

The default capability set is also important:

- `Shell` exposes `exec_command`, which calls `session.exec()` and therefore
  `_exec_internal()`.
- `Filesystem` exposes `apply_patch` and `view_image`; it does not provide general
  text-file read, list, or search tools.
- General text inspection with the default capabilities is normally done through shell
  commands such as `cat`, `sed`, `ls`, or `rg`.

Consequently, a backend without POSIX-like shell behavior should replace the default
`Shell` capability and add explicit typed tools for text reads, directory listing, search,
and the project's constrained command language.

## 3. Is the existing in-memory sandbox functionally complete?

The original four model-facing operations are a strong framework-neutral foundation:

```text
execute
read a range
write a file
apply a patch
```

They are sufficient for a custom OpenAI capability adapter, but they are not by
themselves a complete drop-in implementation of the current `SandboxAgent` backend
contract.

Use the following levels when judging completeness.

### Core tool functionality

The core is functionally complete for the original product model when it provides:

- constrained command execution with timeout, stdout, stderr, and exit status
- deterministic text-range reads
- complete file writes
- patch application
- per-session ownership, limits, policy, telemetry, and snapshots

This is enough to expose the original four tools to any agent framework.

### OpenAI session compatibility

The completed Milestone 5.1 audit confirms that the public core already provides complete
binary reads and writes, stat/list operations, service-owned lifecycle, process-local
snapshots, and deterministic constrained command execution.

The remaining owning-module prerequisites are:

- native directory creation and removal through an owning core/session boundary
  (issue #31)
- POSIX path normalization and confinement, including safe symlink behavior if symlinks
  are supported
- bounded workspace-wide export and import for OpenAI snapshot persistence (issue #32)
- exact mapping of OpenAI create, start, running, stop, close, delete, serialized state,
  and resume behavior onto `SandboxService` ownership
- manifest materialization or explicit rejection of unsupported manifest features
- a deliberate choice between POSIX-compatible `sh -lc` execution and custom
  model-facing capabilities

The ranged-read API should remain model-facing because it controls context size. OpenAI
backend reads should translate through the existing public `SandboxSession.read_bytes`
operation rather than introducing a private full-byte path.

### Drop-in compatibility with default `SandboxAgent` capabilities

This is the highest compatibility level. It requires the virtual command executor to
understand the shell form and commands used by both the model and SDK internals, including
at least `sh -lc`, `ls`, `mkdir`, `rm`, `chmod`, and commonly `cat` and `rg`. Optional
manifest features add more commands and semantics.

This level is not required for the first Python version. A cleaner first milestone is:

1. Keep the original four operations as explicit typed tools in a custom OpenAI
   capability.
2. Reuse the existing public binary read/write and stat/list operations; add only the
   missing native directory and portable workspace-persistence ports.
3. Implement the OpenAI client/session lifecycle and snapshot bridge.
4. Replace the default OpenAI shell/filesystem capabilities instead of claiming support
   for their POSIX assumptions.
5. Add POSIX compatibility incrementally only where it provides concrete value.

This makes the design functionally complete without pretending that the in-memory
workspace is a full Unix machine.

## 4. Final adapter boundary

The client receives one `SandboxService` through constructor injection. A provider
session retains:

- its serializable `InMemorySandboxSessionState`;
- the injected service;
- the opaque `SandboxHandle` represented by `state.sandbox_handle`;
- the public `SandboxSession` returned by `service.get_session(handle)`.

No workspace, executor, archive codec, snapshot store, policy engine, or event sink is
exposed to the adapter.

### Create

1. Resolve default options, manifest, and SDK snapshot.
2. Validate the complete manifest profile, including file, aggregate-byte, and node
   quotas, then translate it to immutable seed files plus empty-directory requests.
3. Reject non-empty exposed ports before allocation.
4. Reject an already-restorable snapshot because create has no matching provider archive
   metadata; restorable snapshots are accepted only through serialized resume state.
5. Call `SandboxService.create()` once with the validated limits and seed files.
6. Materialize required empty directories through public native session operations.
7. Construct provider state, mark the provider start as already materialized, and return
   `self._wrap_session(inner)`.
8. Rebind the pinned wrapper hooks that do not delegate safely: `apply_manifest`,
   `_apply_entry_batch`, and `extract`.
9. If any post-allocation step fails, attempt service deletion and propagate the primary
   failure.

The core session is already running when the SDK wrapper is returned. Provider
`start()` therefore performs no second core start and no duplicate manifest apply.

### Resume

1. Validate state type, manifest profile, limits, and archive metadata shape.
2. Parse `state.sandbox_handle` as an opaque handle value.
3. Attempt `service.get_session(handle)`.
4. If found, mark the provider start as preserved and reuse that session.
5. If not found, allocate a replacement with the same immutable limits and translated
   manifest seed plan, update the provider handle, and mark the start as not preserved.
6. During SDK `start()`, hydrate a restorable snapshot into the replacement through the
   portable archive bridge. A live reattachment performs no hydration.

Only `SandboxNotFound` selects the replacement branch. Other service lookup failures
propagate without allocating a divergent workspace.

The provider overrides `_probe_workspace_root_for_preserved_resume()` and
`_can_skip_snapshot_restore_on_resume()` using the runtime-only fact that the exact same
opaque handle resolved in the same service. It does not invoke the SDK's `test -d`
command or fingerprint helper.

### Session translation

| SDK operation | Public MemSandbox translation |
|---|---|
| `_exec_internal(argv)` | Quote each argv element into one constrained command string and call `SandboxSession.execute` |
| `read` | `SandboxSession.read_bytes` |
| `write` | Bounded binary stream read, then `SandboxSession.write_bytes` |
| `running` | Compare public session state with `RUNNING` |
| `ls` | `SandboxSession.list_entries` and translate immutable metadata |
| `mkdir` | `SandboxSession.create_directory` |
| `rm` | `SandboxSession.remove_path` |
| `persist_workspace` | `SandboxSession.export_portable_archive` |
| `hydrate_workspace` | Rebuild `WorkspaceArchiveData`, then `SandboxSession.restore_portable_archive` |

The argv quoting helper is adapter-owned syntax translation, not a shell. It must preserve
arguments exactly for the constrained parser, reject NUL and unsupported values, and
never invoke `sh`, a host process, or executable lookup.

SDK `user` values are rejected before core calls. `exec(..., shell=True)` and custom shell
prefix lists are overridden and rejected before the SDK can prepend `sh -lc`.

Boundary failures use SDK errors where the SDK defines a matching category:
`InvalidManifestPathError` for confined-path failures, `WorkspaceWriteTypeError` for
non-binary writes, and `WorkspaceArchiveReadError` for stream, metadata, or core archive
validation failures. The original core error is retained as the cause.

### Manifest materialization

Profile validation recursively visits every entry before allocation. Supported `Dir` and
`File` entries are flattened into:

- `WorkspaceSeedFile` values for all files;
- ordered `CreateDirectoryRequest` values for explicit and empty directories.

The normalized plan rejects duplicate paths, file-as-parent conflicts, and entries that
target the workspace root. It also preflights file size, aggregate bytes, and the complete
explicit/implicit node set before allocation. The adapter does not call
`BaseEntry.apply()`, inherited `_apply_entry_batch()`, `chmod`, `chgrp`, or a host/local
source API. The profile permits descriptions but requires default per-kind permissions,
no group, and `ephemeral=False`.

`remote_mount_command_allowlist` must remain equal to the pinned SDK default. It has no
runtime effect because all mount entry types are rejected, but rejecting custom values
keeps the supported configuration matrix exact instead of silently ignoring input.

The provider overrides `_start_workspace`, `_validate_manifest_application`,
`_apply_manifest`, `_apply_entry_batch`, and `provision_manifest_accounts`. Initial
materialization is already complete before the SDK wrapper is returned; later supported
manifest applications and runtime-manager entry batches use the supplied entries with the
same native translation plan. Live updates are applied to a temporary service-owned
sandbox restored from the current portable archive, then atomically published back through
`SandboxSession.restore_portable_archive()` only if the live revision and root hash still
match the captured workspace identity. The staging sandbox is deleted before publication.
A failed quota check, concurrent live mutation, staging mutation, or staging cleanup
therefore cannot expose a partial or stale manifest result. No inherited SDK path can reach
account or metadata commands.

### Portable archive bridge

The adapter enforces `max_stream_bytes` on both persisted and hydrated snapshot streams.
It stores only archive format version, revision, and root hash in provider state; archive
bytes remain in the SDK snapshot. Each persistence attempt writes a new immutable snapshot
identity and publishes that identity plus its metadata only after the snapshot backend
accepts the matching bytes. An uncertain write failure can therefore leave only an
unreferenced candidate while the previous payload and metadata pair remains authoritative.
Hydration reconstructs the typed archive value and delegates all tar validation,
decompressed quotas, entry limits, path checks, and atomic publication to the session/core
boundary.

Direct `persist_workspace()` calls remain usable without changing durable resume state:
the returned raw-byte stream carries transient in-process archive metadata consumed by a
matching direct `hydrate_workspace()` call. Only successful SDK snapshot persistence
publishes serialized archive metadata.

The adapter must not import `MemoryWorkspace`, `WorkspaceArchivePort`, or concrete workspace
snapshot/archive codecs.

The provider overrides `_clear_workspace_root_on_resume()` as a no-op and disables SDK
snapshot fingerprint computation. Malformed hydration therefore cannot destroy the live
workspace before validation.

Provider state must be serialized after successful `stop()` when a durable snapshot is
required. This is the ordering used by the pinned runtime cleanup path.

## 5. The ABC minimum is not the usable minimum

Implementing the six abstract session methods makes the class instantiable, but inherited
SDK behavior assumes a POSIX-like environment.

| Inherited feature | Commands or behavior assumed |
|---|---|
| `exec(..., shell=True)` | Prefixes the request with `sh -lc` |
| `user=` execution | Prefixes the request with `sudo -u <user> --` |
| Default `ls`, `mkdir`, and `rm` | Executes those POSIX utilities |
| Default archive `extract` | May spool unbounded input into a host temporary file |
| Manifest files and directories | Uses `chmod`, and optionally `chgrp` |
| Manifest users and groups | Uses `groupadd`, `useradd`, and `usermod` |
| Preserved-workspace probe | Uses `test -d` |
| Snapshot fingerprinting | Installs and executes an SDK shell helper; also uses `cat` and `rm` |
| `GitRepo` manifest entries | Uses `git`, `cp`, and `rm` |
| Built-in `Shell` capability | Sends model commands through `sh -lc` or `sh -c` |

There are two valid implementation strategies:

### Strategy A: emulate the expected POSIX surface

Implement `_exec_internal()` as a virtual shell that supports the SDK's internal commands
and the commands exposed to the model. This allows more inherited behavior to remain
unchanged, but it is a much larger compatibility commitment than the abstract API
suggests.

### Strategy B: override at the virtual-workspace level

Implement `ls`, `mkdir`, `rm`, path authorization, manifest materialization, archive
handling, and snapshot fingerprinting directly against the in-memory core. This is the
recommended strategy because it avoids pretending the virtual workspace is a complete
Unix host.

For the first adapter version:

- override `ls`, `mkdir`, `rm`, `extract`, and `_validate_path_access`; profile 1 rejects
  archive extraction before reading input and uses the bounded snapshot bridge for portable
  whole-workspace hydration
- disable the SDK's POSIX fingerprint helper or replace it with a native hash
- override manifest validation and materialization so accepted synthetic entries use
  public seed/directory operations and never invoke `chmod` or `chgrp`
- reject manifest users, groups, mounts, `GitRepo`, exposed ports, and PTY unless the core
  explicitly supports them
- reject `exec(..., shell=True)` and custom shell prefixes; `_exec_internal` accepts only
  the argv form produced by `shell=False`
- do not enable the default `Shell` capability; use the adapter-owned capability from
  Milestone 5.3

If the backend does not provide POSIX shell semantics, define a custom capability whose
typed tools call the project's constrained command API directly.

## 6. Snapshot contract

Snapshots are separate from sessions. A custom `SnapshotBase` must implement:

```python
async def persist(
    self,
    data: io.IOBase,
    *,
    dependencies: Dependencies | None = None,
) -> None: ...


async def restore(
    self,
    *,
    dependencies: Dependencies | None = None,
) -> io.IOBase: ...


async def restorable(
    self,
    *,
    dependencies: Dependencies | None = None,
) -> bool: ...
```

`Runner` cleanup calls `stop()` before `delete()`. `stop()` persists the workspace to the
configured snapshot, and `delete()` can then release the live workspace. Consequently:

- `NoopSnapshot` gives no workspace recovery after the client deletes the workspace.
- A process-local in-memory snapshot store supports resume only in the same process.
- Cross-process or durable resume needs a local or remote snapshot implementation.

## 7. Wiring the client to `SandboxAgent`

The runtime backend belongs in `SandboxRunConfig`, not on the agent:

```python
from agents import Runner
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig

client = InMemorySandboxClient(store)

agent = SandboxAgent(
    name="In-memory workspace agent",
    instructions="Use the workspace tools to complete the task.",
    default_manifest=Manifest(),
    # Specify capabilities explicitly if the backend does not implement POSIX shell
    # semantics. SandboxAgent defaults include the Shell capability.
    capabilities=[my_virtual_workspace_capability],
)

result = await Runner.run(
    agent,
    "Create notes.txt containing the final answer.",
    run_config=RunConfig(
        sandbox=SandboxRunConfig(
            client=client,
            options=InMemorySandboxClientOptions(),
            snapshot=my_snapshot_spec,
        )
    ),
)
```

For a caller-managed live session:

```python
sandbox = await client.create(
    manifest=agent.default_manifest,
    options=InMemorySandboxClientOptions(),
)
try:
    async with sandbox:
        result = await Runner.run(
            agent,
            "Inspect the workspace.",
            run_config=RunConfig(sandbox=SandboxRunConfig(session=sandbox)),
        )
finally:
    await client.delete(sandbox)
```

## 8. Recommended adapter boundary

Keep this adapter outside the framework-neutral core:

```text
src/mem_sandbox/
  integrations/
    openai_agents/
      __init__.py
      client.py
      session.py
      state.py
      capabilities.py
      snapshots.py

tests/
  integrations/
    openai_agents/
      test_client.py
      test_session.py
      test_state.py
      test_capabilities.py
```

Expose the adapter through an optional integration extra supporting
`openai-agents>=0.22,<0.23`, and run contract tests against exactly `0.22.0`. The Sandbox
Agents API is explicitly beta, and methods that are concrete today may become abstract or
change semantics before general availability.

## Official references

- [Sandbox clients](https://openai.github.io/openai-agents-python/sandbox/clients/)
- [Sandbox guide](https://openai.github.io/openai-agents-python/sandbox/guide/)
- [`BaseSandboxClient`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/sandbox_client.py)
- [`BaseSandboxSession`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/base_sandbox_session.py)
- [`SandboxSessionState`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/sandbox_session_state.py)
- [`SnapshotBase`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/snapshot.py)
- [`SandboxRunConfig`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/run_config.py)
