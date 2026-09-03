# OpenAI `SandboxAgent` Backend Contract

**Pinned SDK:** `openai-agents==0.22.0`
**Pinned source:** commit
[`89c02c8`](https://github.com/openai/openai-agents-python/tree/89c02c828ee8510fe9a84ee6675608193aa13b02)
**Status:** Sandbox Agents are beta.

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
    type: Literal["in_memory"] = "in_memory"
    max_workspace_bytes: int = 16 * 1024 * 1024
    max_stream_bytes: int = 32 * 1024 * 1024
```

The model is frozen. The discriminator should equal the client's `backend_id`.
`max_stream_bytes` bounds adapter-side buffering before data reaches the core.

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
    type: Literal["in_memory"] = "in_memory"
    workspace_id: str
    max_workspace_bytes: int
    max_stream_bytes: int
```

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

The OpenAI adapter additionally needs:

- complete binary file reads, because `read()` returns an `io.IOBase`, not line-oriented
  text
- complete binary writes for images, archives, and manifest entries
- directory listing, creation, and removal, either as native operations or faithfully
  supported commands
- POSIX path normalization and confinement, including safe symlink behavior if symlinks
  are supported
- workspace-wide export and import for snapshot persistence
- create, start, running, stop, shutdown, delete, and resume lifecycle behavior
- manifest materialization or explicit rejection of unsupported manifest features
- a deliberate choice between POSIX-compatible `sh -lc` execution and custom
  model-facing capabilities

The old ranged-read API should remain model-facing because it controls context size, but
the core or adapter also needs a private full-byte read operation for the OpenAI contract.

### Drop-in compatibility with default `SandboxAgent` capabilities

This is the highest compatibility level. It requires the virtual command executor to
understand the shell form and commands used by both the model and SDK internals, including
at least `sh -lc`, `ls`, `mkdir`, `rm`, `chmod`, and commonly `cat` and `rg`. Optional
manifest features add more commands and semantics.

This level is not required for the first Python version. A cleaner first milestone is:

1. Keep the original four operations as explicit typed tools.
2. Add private binary read/write and native directory operations.
3. Implement the OpenAI client/session lifecycle and snapshot bridge.
4. Use a custom OpenAI capability instead of claiming support for the default POSIX shell.
5. Add POSIX compatibility incrementally only where it provides concrete value.

This makes the design functionally complete without pretending that the in-memory
workspace is a full Unix machine.

## 4. Minimal adapter skeleton

This skeleton shows the OpenAI-facing boundary. `SandboxCoreSession` and
`SandboxCoreStore` represent framework-neutral interfaces owned by the new project.

```python
from __future__ import annotations

import asyncio
import io
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from agents.sandbox import ExecResult, Manifest, User, resolve_snapshot
from agents.sandbox.files import EntryKind, FileEntry
from agents.sandbox.snapshot import SnapshotBase, SnapshotSpec
from agents.sandbox.session import (
    BaseSandboxClient,
    BaseSandboxClientOptions,
    BaseSandboxSession,
    SandboxSession,
    SandboxSessionState,
)
from agents.sandbox.types import Permissions
from agents.sandbox.workspace_paths import sandbox_path_str


@dataclass(frozen=True)
class CoreExecResult:
    stdout: bytes
    stderr: bytes
    exit_code: int


@dataclass(frozen=True)
class CoreFileEntry:
    path: str
    kind: Literal["file", "directory", "symlink", "other"]
    mode: int
    owner: str
    group: str
    size: int


class SandboxCoreSession(Protocol):
    async def start(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def is_running(self) -> bool: ...
    async def is_directory(self, path: str) -> bool: ...

    async def execute(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float | None,
    ) -> CoreExecResult: ...

    async def read_bytes(self, path: str, *, user: str | None) -> bytes: ...
    async def write_bytes(
        self,
        path: str,
        payload: bytes,
        *,
        user: str | None,
    ) -> None: ...
    async def list_entries(
        self,
        path: str,
        *,
        user: str | None,
    ) -> list[CoreFileEntry]: ...
    async def make_directory(
        self,
        path: str,
        *,
        parents: bool,
        user: str | None,
    ) -> None: ...
    async def remove(
        self,
        path: str,
        *,
        recursive: bool,
        user: str | None,
    ) -> None: ...
    async def assert_path_allowed(self, path: str, *, for_write: bool) -> None: ...

    async def export_workspace_tar(self, *, skip_paths: tuple[str, ...]) -> bytes: ...
    async def import_workspace_tar(self, payload: bytes) -> None: ...


class SandboxCoreStore(Protocol):
    async def create(
        self,
        workspace_id: str,
        *,
        max_workspace_bytes: int,
    ) -> SandboxCoreSession: ...

    async def attach(self, workspace_id: str) -> SandboxCoreSession | None: ...
    async def delete(self, workspace_id: str) -> None: ...


def read_bounded_stream(
    data: io.IOBase,
    *,
    max_bytes: int,
    allow_text: bool,
    description: str,
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = data.read(min(64 * 1024, max_bytes - total + 1))
        if chunk in (b"", ""):
            return b"".join(chunks)
        if isinstance(chunk, str):
            if not allow_text:
                raise TypeError(f"{description} stream must return bytes")
            payload = chunk.encode("utf-8")
        elif isinstance(chunk, bytes | bytearray):
            payload = bytes(chunk)
        else:
            raise TypeError(f"{description} stream returned an unsupported value")

        total += len(payload)
        if total > max_bytes:
            raise ValueError(f"{description} exceeds the {max_bytes}-byte input limit")
        chunks.append(payload)


class InMemorySandboxClientOptions(BaseSandboxClientOptions):
    type: Literal["in_memory"] = "in_memory"
    max_workspace_bytes: int = 16 * 1024 * 1024
    max_stream_bytes: int = 32 * 1024 * 1024
    exposed_ports: tuple[int, ...] = ()


class InMemorySandboxSessionState(SandboxSessionState):
    type: Literal["in_memory"] = "in_memory"
    workspace_id: str
    max_workspace_bytes: int
    max_stream_bytes: int


class InMemorySandboxSession(BaseSandboxSession):
    state: InMemorySandboxSessionState

    def __init__(
        self,
        *,
        state: InMemorySandboxSessionState,
        core: SandboxCoreSession,
    ) -> None:
        self.state = state
        self._core = core

    async def _ensure_backend_started(self) -> None:
        await self._core.start()

    async def _prepare_backend_workspace(self) -> None:
        await self._core.make_directory(
            sandbox_path_str(self._workspace_root_path()),
            parents=True,
            user=None,
        )

    async def _probe_workspace_root_for_preserved_resume(self) -> bool:
        if not self._workspace_state_preserved_on_start():
            return False
        ready = await self._core.is_directory(sandbox_path_str(self._workspace_root_path()))
        if ready:
            self._mark_workspace_root_ready_from_probe()
        return ready

    async def _shutdown_backend(self) -> None:
        await self._core.shutdown()

    async def _exec_internal(
        self,
        *command: str | Path,
        timeout: float | None = None,
    ) -> ExecResult:
        result = await self._core.execute(
            tuple(
                sandbox_path_str(part) if isinstance(part, Path) else str(part) for part in command
            ),
            timeout_seconds=timeout,
        )
        return ExecResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
        )

    async def read(
        self,
        path: Path,
        *,
        user: str | User | None = None,
    ) -> io.IOBase:
        normalized = await self._validate_path_access(path)
        payload = await self._core.read_bytes(
            sandbox_path_str(normalized),
            user=self._user_name(user),
        )
        return io.BytesIO(payload)

    async def write(
        self,
        path: Path,
        data: io.IOBase,
        *,
        user: str | User | None = None,
    ) -> None:
        normalized = await self._validate_path_access(path, for_write=True)
        payload = read_bounded_stream(
            data,
            max_bytes=min(
                self.state.max_workspace_bytes,
                self.state.max_stream_bytes,
            ),
            allow_text=True,
            description="sandbox write",
        )
        await self._core.write_bytes(
            sandbox_path_str(normalized),
            payload,
            user=self._user_name(user),
        )

    async def running(self) -> bool:
        return await self._core.is_running()

    async def persist_workspace(self) -> io.IOBase:
        skip_paths = tuple(
            path.as_posix() for path in sorted(self._persist_workspace_skip_relpaths())
        )
        payload = await self._core.export_workspace_tar(skip_paths=skip_paths)
        return io.BytesIO(payload)

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        payload = read_bounded_stream(
            data,
            max_bytes=self.state.max_stream_bytes,
            allow_text=False,
            description="workspace archive",
        )
        await self._core.import_workspace_tar(payload)

    # Recommended for a native virtual filesystem. The inherited implementations
    # invoke POSIX ls, mkdir, and rm through _exec_internal().
    async def ls(
        self,
        path: Path | str,
        *,
        user: str | User | None = None,
    ) -> list[FileEntry]:
        normalized = await self._validate_path_access(path)
        entries = await self._core.list_entries(
            sandbox_path_str(normalized),
            user=self._user_name(user),
        )
        return [self._to_sdk_file_entry(entry) for entry in entries]

    async def mkdir(
        self,
        path: Path | str,
        *,
        parents: bool = False,
        user: str | User | None = None,
    ) -> None:
        normalized = await self._validate_path_access(path, for_write=True)
        await self._core.make_directory(
            sandbox_path_str(normalized),
            parents=parents,
            user=self._user_name(user),
        )

    async def rm(
        self,
        path: Path | str,
        *,
        recursive: bool = False,
        user: str | User | None = None,
    ) -> None:
        normalized = await self._validate_path_access(path, for_write=True)
        await self._core.remove(
            sandbox_path_str(normalized),
            recursive=recursive,
            user=self._user_name(user),
        )

    async def _validate_path_access(
        self,
        path: Path | str,
        *,
        for_write: bool = False,
    ) -> Path:
        normalized = self.normalize_path(path, for_write=for_write)
        await self._core.assert_path_allowed(
            sandbox_path_str(normalized),
            for_write=for_write,
        )
        return normalized

    # The SDK's default fingerprint implementation installs and runs a POSIX helper
    # script. Disable it until the virtual core supplies a native equivalent.
    def _should_compute_snapshot_fingerprint_on_persist(self) -> bool:
        return False

    @staticmethod
    def _user_name(user: str | User | None) -> str | None:
        return user.name if isinstance(user, User) else user

    @staticmethod
    def _to_sdk_file_entry(entry: CoreFileEntry) -> FileEntry:
        kind = EntryKind(entry.kind)
        mode = entry.mode
        if kind == EntryKind.DIRECTORY:
            mode |= stat.S_IFDIR
        return FileEntry(
            path=entry.path,
            permissions=Permissions.from_mode(mode),
            owner=entry.owner,
            group=entry.group,
            size=entry.size,
            kind=kind,
        )


class InMemorySandboxClient(BaseSandboxClient[InMemorySandboxClientOptions | None]):
    backend_id = "in_memory"
    supports_default_options = True

    def __init__(self, store: SandboxCoreStore) -> None:
        self._store = store

    async def create(
        self,
        *,
        snapshot: SnapshotSpec | SnapshotBase | None = None,
        manifest: Manifest | None = None,
        options: InMemorySandboxClientOptions | None = None,
    ) -> SandboxSession:
        resolved_options = options or InMemorySandboxClientOptions()
        resolved_manifest = manifest or Manifest()
        self._validate_manifest_for_create(resolved_manifest)

        session_id = uuid.uuid4()
        workspace_id = session_id.hex
        resolved_snapshot = resolve_snapshot(snapshot, str(session_id))
        state = InMemorySandboxSessionState(
            session_id=session_id,
            workspace_id=workspace_id,
            max_workspace_bytes=resolved_options.max_workspace_bytes,
            max_stream_bytes=resolved_options.max_stream_bytes,
            snapshot=resolved_snapshot,
            manifest=resolved_manifest,
            exposed_ports=resolved_options.exposed_ports,
        )
        core = await self._store.create(
            workspace_id,
            max_workspace_bytes=resolved_options.max_workspace_bytes,
        )
        try:
            inner = InMemorySandboxSession(state=state, core=core)
            return self._wrap_session(inner)
        except Exception:
            await asyncio.shield(self._store.delete(workspace_id))
            raise

    async def resume(
        self,
        state: SandboxSessionState,
    ) -> SandboxSession:
        if not isinstance(state, InMemorySandboxSessionState):
            raise TypeError("InMemorySandboxClient.resume expects InMemorySandboxSessionState")
        state.assert_path_grants_rebound()

        core = await self._store.attach(state.workspace_id)
        preserved = core is not None
        if core is None:
            core = await self._store.create(
                state.workspace_id,
                max_workspace_bytes=state.max_workspace_bytes,
            )
            state = state.model_copy(update={"workspace_root_ready": False})

        inner = InMemorySandboxSession(state=state, core=core)
        inner._set_start_state_preserved(preserved)
        return self._wrap_session(inner)

    async def delete(self, session: SandboxSession) -> SandboxSession:
        state = session.state
        if not isinstance(state, InMemorySandboxSessionState):
            raise TypeError("InMemorySandboxClient.delete expects an in-memory session")
        await self._store.delete(state.workspace_id)
        return session

    def deserialize_session_state(
        self,
        payload: dict[str, object],
    ) -> SandboxSessionState:
        return self._deserialize_session_state_payload(
            payload,
            InMemorySandboxSessionState,
        )
```

Manifest, snapshot, option, and state validation occurs before the core workspace is
allocated. There are no await points between successful allocation and ownership transfer
to the wrapped session. If synchronous construction or wrapping fails, shielded cleanup
deletes the newly allocated workspace before the original failure is propagated.

The adapter input limit bounds compressed or raw bytes before buffering. The core archive
decoder must independently enforce decompressed workspace bytes, entry count, path
limits, and atomic restore.

This is the adapter boundary, not the implementation of the virtual filesystem or shell.
The project core should own path semantics, quotas, command policy, snapshots, and
concurrency. The OpenAI adapter should only translate SDK calls into that core.

## 5. The ABC minimum is not the usable minimum

Implementing the six abstract session methods makes the class instantiable, but inherited
SDK behavior assumes a POSIX-like environment.

| Inherited feature | Commands or behavior assumed |
|---|---|
| `exec(..., shell=True)` | Prefixes the request with `sh -lc` |
| `user=` execution | Prefixes the request with `sudo -u <user> --` |
| Default `ls`, `mkdir`, and `rm` | Executes those POSIX utilities |
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

- override `ls`, `mkdir`, `rm`, and `_validate_path_access`
- disable the SDK's POSIX fingerprint helper or replace it with a native hash
- either support logical `chmod`/`chgrp` in the command dispatcher or override
  `_apply_manifest` and `_apply_entry_batch`
- reject manifest users, groups, mounts, `GitRepo`, exposed ports, and PTY unless the core
  explicitly supports them
- do not enable the default `Shell` capability unless the virtual command language accepts
  the `sh -lc` form used by the SDK

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
core/
  session.py
  workspace.py
  executor.py
  snapshots.py

adapters/
  openai_agents/
    client.py
    session.py
    state.py
    capabilities.py
    tests/
```

Pin the supported OpenAI SDK minor version and run adapter contract tests against it. The
Sandbox Agents API is explicitly beta, and methods that are concrete today may become
abstract or change semantics before general availability.

## Official references

- [Sandbox clients](https://openai.github.io/openai-agents-python/sandbox/clients/)
- [Sandbox guide](https://openai.github.io/openai-agents-python/sandbox/guide/)
- [`BaseSandboxClient`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/sandbox_client.py)
- [`BaseSandboxSession`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/base_sandbox_session.py)
- [`SandboxSessionState`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/session/sandbox_session_state.py)
- [`SnapshotBase`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/sandbox/snapshot.py)
- [`SandboxRunConfig`](https://github.com/openai/openai-agents-python/blob/89c02c828ee8510fe9a84ee6675608193aa13b02/src/agents/run_config.py)
