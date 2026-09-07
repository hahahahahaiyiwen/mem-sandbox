"""OpenAI Agents SDK sandbox adapter over the public MemSandbox boundary."""

from __future__ import annotations

import asyncio
import io
import shlex
import sys
import uuid
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Literal, cast

from agents.run_config import SandboxArchiveLimits
from agents.sandbox import Manifest
from agents.sandbox.entries import BaseEntry, Dir, File
from agents.sandbox.errors import (
    InvalidManifestPathError,
    SnapshotNotRestorableError,
    WorkspaceArchiveReadError,
    WorkspaceWriteTypeError,
)
from agents.sandbox.files import EntryKind, FileEntry
from agents.sandbox.manifest import DEFAULT_REMOTE_MOUNT_COMMAND_ALLOWLIST
from agents.sandbox.materialization import MaterializationResult, MaterializedFile
from agents.sandbox.session import (
    BaseSandboxClient,
    BaseSandboxClientOptions,
    BaseSandboxSession,
    Dependencies,
    SandboxSessionState,
)
from agents.sandbox.session import SandboxSession as OpenAISandboxSession
from agents.sandbox.snapshot import (
    NoopSnapshot,
    SnapshotBase,
    SnapshotSpec,
    resolve_snapshot,
)
from agents.sandbox.types import ExecResult, Permissions, User
from pydantic import ConfigDict, Field, field_validator, model_validator

from mem_sandbox.command_executor import CommandLimits
from mem_sandbox.core import Clock, OperationLimits, Revision, SessionId
from mem_sandbox.integrations.openai_agents.snapshot import (
    InMemorySandboxSnapshot,
    WorkspaceArchiveStream,
    configure_snapshot_dependencies,
)
from mem_sandbox.service import (
    CreateSandboxRequest,
    FactorySnapshotStore,
    OwnerId,
    SandboxHandle,
    SandboxNotFound,
    SandboxOptions,
    SandboxService,
    WorkspaceSeedFile,
)
from mem_sandbox.session import (
    CreateDirectoryRequest,
    ListEntriesRequest,
    ReadBytesRequest,
    RemoveEntryRequest,
    SandboxSession,
    SessionClosed,
    SessionExecuteRequest,
    WriteBytesRequest,
)
from mem_sandbox.session import SandboxSessionState as CoreSandboxSessionState
from mem_sandbox.workspace import (
    ContentHash,
    FileSizeLimitExceededError,
    InvalidPathError,
    NodeKind,
    NodeLimitExceededError,
    PathOutsideWorkspaceError,
    SandboxPath,
    WorkspaceArchiveData,
    WorkspaceLimits,
    WorkspaceSizeLimitExceededError,
)

_PROVIDER_TYPE = "mem_sandbox"
_DEFAULT_OWNER_ID = "openai-agents"
_READ_CHUNK_BYTES = 64 * 1024


class InMemorySandboxClientOptions(BaseSandboxClientOptions):
    """Immutable provider options serialized by the OpenAI SDK."""

    type: Literal["mem_sandbox"] = _PROVIDER_TYPE  # pyright: ignore[reportIncompatibleVariableOverride]
    owner_id: str = _DEFAULT_OWNER_ID
    workspace_limits: WorkspaceLimits = Field(default_factory=WorkspaceLimits)
    lifecycle_limits: OperationLimits = Field(default_factory=OperationLimits)
    max_stream_bytes: int = 32 * 1024 * 1024
    manifest_profile_version: Literal[1] = 1
    exposed_ports: tuple[int, ...] = ()

    @field_validator("max_stream_bytes")
    @classmethod
    def _validate_max_stream_bytes(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("max_stream_bytes must be a positive integer")
        return value


class InMemorySandboxSessionState(SandboxSessionState):
    """JSON-safe provider state plus portable archive metadata."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    type: Literal["mem_sandbox"] = _PROVIDER_TYPE  # pyright: ignore[reportIncompatibleVariableOverride]
    provider_state_version: Literal[1] = 1
    sandbox_handle: str
    core_session_id: str
    owner_id: str = _DEFAULT_OWNER_ID
    workspace_limits: WorkspaceLimits = Field(default_factory=WorkspaceLimits)
    lifecycle_limits: OperationLimits = Field(default_factory=OperationLimits)
    max_stream_bytes: int = 32 * 1024 * 1024
    manifest_profile_version: Literal[1] = 1
    workspace_archive_format_version: int | None = None
    workspace_archive_revision: int | None = None
    workspace_archive_root_hash: str | None = None

    @field_validator("max_stream_bytes")
    @classmethod
    def _validate_max_stream_bytes(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("max_stream_bytes must be a positive integer")
        return value

    @field_validator("sandbox_handle")
    @classmethod
    def _validate_sandbox_handle(cls, value: str) -> str:
        _parse_handle(value)
        return value

    @field_validator("core_session_id")
    @classmethod
    def _validate_core_session_id(cls, value: str) -> str:
        SessionId.parse(value)
        return value

    @model_validator(mode="after")
    def _validate_archive_metadata(self) -> InMemorySandboxSessionState:
        _validate_archive_metadata_values(
            self.workspace_archive_format_version,
            self.workspace_archive_revision,
            self.workspace_archive_root_hash,
        )
        return self


@dataclass(frozen=True, slots=True)
class _ManifestPlan:
    files: tuple[WorkspaceSeedFile, ...]
    directories: tuple[str, ...]


class _BinaryStreamTypeError(TypeError):
    def __init__(self, actual_type: str) -> None:
        super().__init__(f"binary stream returned {actual_type}")
        self.actual_type = actual_type


class InMemorySandboxSession(BaseSandboxSession):
    """OpenAI sandbox session backed by one public MemSandbox session."""

    state: InMemorySandboxSessionState  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        *,
        state: InMemorySandboxSessionState,
        service: SandboxService,
        session: SandboxSession,
        live_reattached: bool = False,
        replacement_resume: bool = False,
        cleanup_on_start_failure: bool = False,
    ) -> None:
        self.state = state  # pyright: ignore[reportIncompatibleVariableOverride]
        self._service = service
        self._session = session
        self._live_reattached = live_reattached
        self._replacement_resume = replacement_resume
        self._cleanup_on_start_failure = cleanup_on_start_failure
        self._cleanup_timeout_seconds = state.lifecycle_limits.timeout_seconds
        self._start_completed = False
        self._backend_deleted = False
        self._sdk_closing_or_closed = False
        self._lifecycle_lock = asyncio.Lock()
        self._set_start_state_preserved(live_reattached, system=live_reattached)

    @property
    def handle(self) -> SandboxHandle:
        return SandboxHandle(uuid.UUID(self.state.sandbox_handle))

    async def start(self) -> None:
        async with self._lifecycle_lock:
            self._require_backend_available()
            if self._start_completed:
                return
            try:
                await super().start()
            except BaseException as error:
                if self._cleanup_on_start_failure:
                    self._backend_deleted = await _cleanup_handle_after_failure(
                        self._service,
                        self.handle,
                        error,
                        self._cleanup_timeout_seconds,
                    )
                    await self._cleanup_dependencies_after_failure(error)
                raise
            self._cleanup_on_start_failure = False
            self._start_completed = True

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self._backend_deleted or self._dependencies_closed or self._sdk_closing_or_closed:
                return
            if self._replacement_resume and not self._start_completed:
                return
            if self._session.state in {
                CoreSandboxSessionState.CLOSING,
                CoreSandboxSessionState.CLOSED,
            }:
                return
            await super().stop()

    async def shutdown(self) -> None:
        async with self._lifecycle_lock:
            if self._sdk_closing_or_closed:
                return
            self._sdk_closing_or_closed = True
            await super().shutdown()

    async def delete_backend(self) -> None:
        async with self._lifecycle_lock:
            if self._backend_deleted:
                return
            try:
                await self._service.delete(self.handle)
            except SandboxNotFound:
                pass
            self._backend_deleted = True

    async def exec(
        self,
        *command: str | Path,
        timeout: float | None = None,
        shell: bool | list[str] = True,
        user: str | User | None = None,
    ) -> ExecResult:
        _reject_user(user)
        if shell is not False:
            raise ValueError("shell execution is not supported")
        return await self._exec_internal(*command, timeout=timeout)

    async def _exec_internal(
        self,
        *command: str | Path,
        timeout: float | None = None,
    ) -> ExecResult:
        if not command:
            raise ValueError("command must contain at least one argv element")
        argv = tuple(_command_part(part) for part in command)
        command_text = " ".join(shlex.quote(part) for part in argv)
        command_limits = (
            CommandLimits() if timeout is None else CommandLimits(timeout_seconds=timeout)
        )
        operation_limits = (
            OperationLimits()
            if timeout is None
            else OperationLimits(
                timeout_seconds=timeout,
                terminal_event_reserve_seconds=min(1.0, timeout / 2),
            )
        )
        result = await self._session.execute(
            SessionExecuteRequest(
                command=command_text,
                command_limits=command_limits,
                limits=operation_limits,
            )
        )
        return ExecResult(
            stdout=result.stdout.encode("utf-8"),
            stderr=result.stderr.encode("utf-8"),
            exit_code=result.exit_code,
        )

    async def read(self, path: Path, *, user: str | User | None = None) -> io.IOBase:
        _reject_user(user)
        workspace_path = self._core_path(path)
        result = await self._session.read_bytes(ReadBytesRequest(path=workspace_path.value))
        return io.BytesIO(result.content)

    async def write(
        self,
        path: Path,
        data: io.IOBase,
        *,
        user: str | User | None = None,
    ) -> None:
        _reject_user(user)
        workspace_path = self._core_path(path)
        try:
            payload = _read_bounded_binary(data, self.state.max_stream_bytes)
        except TypeError as error:
            raise WorkspaceWriteTypeError(
                path=Path(workspace_path.value),
                actual_type=_stream_value_type(error),
                cause=error,
            ) from error
        await self._session.write_bytes(
            WriteBytesRequest(
                path=workspace_path.value,
                content=payload,
                create_parents=True,
            )
        )

    async def running(self) -> bool:
        return self._session.state is CoreSandboxSessionState.RUNNING

    async def persist_workspace(self) -> io.IOBase:
        archive = await self._session.export_portable_archive()
        _validate_archive_stream_size(archive, self.state.max_stream_bytes)
        return WorkspaceArchiveStream(
            archive,
            source_session_id=self._session.session_id,
            owner_id=self.state.owner_id,
        )

    async def _persist_snapshot(self) -> None:
        snapshot = self.state.snapshot
        if isinstance(snapshot, NoopSnapshot):
            return
        archive = await self._session.export_portable_archive()
        _validate_archive_stream_size(archive, self.state.max_stream_bytes)
        if (
            isinstance(snapshot, InMemorySandboxSnapshot)
            and self.state.workspace_archive_format_version == archive.format_version
            and self.state.workspace_archive_revision == archive.workspace_revision.value
            and self.state.workspace_archive_root_hash == archive.root_hash.value
            and await snapshot.restorable(dependencies=self.dependencies)
        ):
            return
        candidate_snapshot = snapshot.model_copy(update={"id": uuid.uuid4().hex})
        stream = WorkspaceArchiveStream(
            archive,
            source_session_id=self._session.session_id,
            owner_id=self.state.owner_id,
        )
        try:
            await candidate_snapshot.persist(stream, dependencies=self.dependencies)
        finally:
            stream.close()
        self.state = self.state.model_copy(  # pyright: ignore[reportIncompatibleVariableOverride]
            update={
                "snapshot": candidate_snapshot,
                "workspace_archive_format_version": archive.format_version,
                "workspace_archive_revision": archive.workspace_revision.value,
                "workspace_archive_root_hash": archive.root_hash.value,
                "snapshot_fingerprint": None,
                "snapshot_fingerprint_version": None,
            }
        )

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        root = Path(self.state.manifest.root)
        try:
            if isinstance(data, WorkspaceArchiveStream):
                format_version = data.archive.format_version
                revision = data.archive.workspace_revision.value
                root_hash = data.archive.root_hash.value
                serialized_metadata = (
                    self.state.workspace_archive_format_version,
                    self.state.workspace_archive_revision,
                    self.state.workspace_archive_root_hash,
                )
                stream_metadata = (format_version, revision, root_hash)
                if any(value is not None for value in serialized_metadata) and (
                    serialized_metadata != stream_metadata
                ):
                    raise ValueError("workspace archive metadata does not match persisted snapshot")
            else:
                format_version = self.state.workspace_archive_format_version
                revision = self.state.workspace_archive_revision
                root_hash = self.state.workspace_archive_root_hash
            if format_version is None or revision is None or root_hash is None:
                raise ValueError("workspace archive metadata is required")
            payload = _read_bounded_binary(data, self.state.max_stream_bytes)
            archive = WorkspaceArchiveData(
                encoded=payload,
                format_version=format_version,
                workspace_revision=Revision(revision),
                root_hash=ContentHash(root_hash),
            )
            await self._session.restore_portable_archive(archive)
        except Exception as error:
            raise WorkspaceArchiveReadError(path=root, cause=error) from error

    async def ls(
        self,
        path: Path | str,
        *,
        user: str | User | None = None,
    ) -> list[FileEntry]:
        _reject_user(user)
        workspace_path = self._core_path(path)
        result = await self._session.list_entries(ListEntriesRequest(path=workspace_path.value))
        return [_file_entry(entry) for entry in result.entries]

    async def mkdir(
        self,
        path: Path | str,
        *,
        parents: bool = False,
        user: str | User | None = None,
    ) -> None:
        _reject_user(user)
        workspace_path = self._core_path(path)
        await self._session.create_directory(
            CreateDirectoryRequest(
                path=workspace_path.value,
                create_parents=parents,
                exist_ok=parents,
            )
        )

    async def rm(
        self,
        path: Path | str,
        *,
        recursive: bool = False,
        user: str | User | None = None,
    ) -> None:
        _reject_user(user)
        workspace_path = self._core_path(path)
        await self._session.remove_path(
            RemoveEntryRequest(
                path=workspace_path.value,
                recursive=recursive,
                missing_ok=recursive,
            )
        )

    async def extract(
        self,
        path: Path | str,
        data: io.IOBase,
        *,
        compression_scheme: Literal["tar", "zip"] | None = None,
        archive_limits: SandboxArchiveLimits | None = None,
    ) -> None:
        _ = (path, data, compression_scheme, archive_limits)
        raise ValueError("archive extraction is not supported")

    async def _validate_path_access(
        self,
        path: Path | str,
        *,
        for_write: bool = False,
    ) -> Path:
        _ = for_write
        return Path(self._core_path(path).value)

    async def _probe_workspace_root_for_preserved_resume(self) -> bool:
        if self._live_reattached:
            self._mark_workspace_root_ready_from_probe()
            return True
        return False

    async def _start_workspace(self) -> None:
        if self._live_reattached:
            self._require_backend_available()
            return
        if self._replacement_resume:
            _require_archive_metadata(self.state)
            if not await self.state.snapshot.restorable(dependencies=self.dependencies):
                raise SnapshotNotRestorableError(
                    snapshot_id=self.state.snapshot.id,
                    path=Path(f"<snapshot:{self.state.snapshot.id}>"),
                )
            await self._restore_snapshot_into_workspace_on_resume()

    async def _clear_workspace_root_on_resume(self) -> None:
        return

    def _should_compute_snapshot_fingerprint_on_persist(self) -> bool:
        return False

    async def _can_skip_snapshot_restore_on_resume(self, *, is_running: bool) -> bool:
        return self._live_reattached and is_running

    def _set_start_state_preserved(
        self,
        workspace: bool,
        *,
        system: bool | None = None,
    ) -> None:
        super()._set_start_state_preserved(workspace, system=system)

    async def _validate_manifest_application(
        self,
        *,
        only_ephemeral: bool = False,
        manifest: Manifest | None = None,
        session_running: bool | None = None,
    ) -> None:
        _ = (only_ephemeral, session_running)
        _manifest_plan(manifest or self.state.manifest, self.state.workspace_limits)

    async def _apply_manifest(
        self,
        *,
        only_ephemeral: bool = False,
        provision_accounts: bool = True,
    ) -> MaterializationResult:
        _ = provision_accounts
        if only_ephemeral:
            await self._validate_manifest_application(only_ephemeral=True)
            return MaterializationResult(files=[])
        plan = _manifest_plan(self.state.manifest, self.state.workspace_limits)
        await self._apply_plan_atomically(plan)
        return MaterializationResult(files=[])

    async def _apply_entry_batch(
        self,
        entries: Sequence[tuple[Path, BaseEntry]],
        *,
        base_dir: Path,
    ) -> list[MaterializedFile]:
        _ = base_dir
        plan = _entry_batch_plan(entries, self.state.workspace_limits)
        await self._apply_plan_atomically(plan)
        return []

    async def provision_manifest_accounts(self) -> None:
        _manifest_plan(self.state.manifest, self.state.workspace_limits)

    async def _apply_plan_atomically(self, plan: _ManifestPlan) -> None:
        if not plan.files and not plan.directories:
            return
        current = await self._session.export_portable_archive()
        staging_handle = await self._service.create(
            CreateSandboxRequest(
                owner_id=OwnerId(self.state.owner_id),
                options=SandboxOptions(
                    workspace_limits=self.state.workspace_limits,
                    lifecycle_limits=self.state.lifecycle_limits,
                ),
            )
        )
        try:
            staging_session = await self._service.get_session(staging_handle)
            await staging_session.restore_portable_archive(current)
            await _materialize_plan(staging_session, plan)
            updated = await staging_session.export_portable_archive()
        finally:
            primary = sys.exception()
            try:
                await self._service.delete(staging_handle)
            except BaseException as cleanup_error:
                if primary is None:
                    raise
                primary.add_note(f"secondary staging sandbox cleanup failure: {cleanup_error}")
        await self._session.restore_portable_archive(
            updated,
            expected_current_revision=current.workspace_revision,
            expected_current_root_hash=current.root_hash,
        )

    def _core_path(self, path: Path | str) -> SandboxPath:
        raw = path.as_posix() if isinstance(path, Path) else path
        try:
            return SandboxPath.resolve(
                raw,
                cwd=SandboxPath.root(),
                max_path_bytes=self.state.workspace_limits.max_path_bytes,
                max_segment_bytes=self.state.workspace_limits.max_segment_bytes,
            )
        except (InvalidPathError, PathOutsideWorkspaceError) as error:
            reason: Literal["absolute", "escape_root"] = (
                "absolute"
                if raw.startswith("/") or PureWindowsPath(raw).is_absolute()
                else "escape_root"
            )
            raise InvalidManifestPathError(
                rel=raw,
                reason=reason,
                cause=error,
            ) from error

    def _require_backend_available(self) -> None:
        if (
            self._backend_deleted
            or self._dependencies_closed
            or self._sdk_closing_or_closed
            or self._session.state
            in {
                CoreSandboxSessionState.CLOSING,
                CoreSandboxSessionState.CLOSED,
            }
        ):
            raise SessionClosed("sandbox backend is closed")

    async def _cleanup_dependencies_after_failure(self, primary: BaseException) -> None:
        try:
            await _await_despite_native_cancellation(
                self._aclose_dependencies(),
                timeout_seconds=self._cleanup_timeout_seconds,
            )
        except BaseException as cleanup_error:
            primary.add_note(f"secondary dependency cleanup failure: {cleanup_error}")


class InMemorySandboxClient(BaseSandboxClient[InMemorySandboxClientOptions]):
    """OpenAI sandbox client over one injected MemSandbox service."""

    backend_id = _PROVIDER_TYPE
    supports_default_options = True

    def __init__(
        self,
        service: SandboxService,
        *,
        snapshot_store: FactorySnapshotStore | None = None,
        clock: Clock | None = None,
        dependencies: Dependencies | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._dependencies = configure_snapshot_dependencies(
            dependencies,
            snapshot_store=snapshot_store,
            clock=clock,
        )

    async def create(
        self,
        *,
        snapshot: SnapshotSpec | SnapshotBase | None = None,
        manifest: Manifest | None = None,
        options: InMemorySandboxClientOptions | None = None,
    ) -> OpenAISandboxSession:
        selected_options = options or InMemorySandboxClientOptions()
        selected_manifest = manifest or Manifest()
        plan = _manifest_plan(selected_manifest, selected_options.workspace_limits)
        _validate_provider_options(selected_options)
        snapshot_id = uuid.uuid4().hex
        selected_snapshot = resolve_snapshot(snapshot, snapshot_id)
        if isinstance(selected_snapshot, InMemorySandboxSnapshot):
            selected_snapshot = selected_snapshot.model_copy(
                update={"owner_id": selected_options.owner_id}
            )
        if await self._snapshot_is_restorable(selected_snapshot):
            raise ValueError("restorable snapshots require session state")

        handle, session = await self._allocate(selected_options, plan)
        try:
            state = InMemorySandboxSessionState(
                sandbox_handle=str(handle),
                core_session_id=str(session.session_id),
                owner_id=selected_options.owner_id,
                workspace_limits=selected_options.workspace_limits,
                lifecycle_limits=selected_options.lifecycle_limits,
                max_stream_bytes=selected_options.max_stream_bytes,
                manifest_profile_version=selected_options.manifest_profile_version,
                snapshot=selected_snapshot,
                manifest=selected_manifest,
                exposed_ports=selected_options.exposed_ports,
                workspace_root_ready=True,
            )
            return self._wrap_provider_session(
                InMemorySandboxSession(
                    state=state,
                    service=self._service,
                    session=session,
                    cleanup_on_start_failure=True,
                )
            )
        except BaseException as error:
            await _cleanup_handle_after_failure(
                self._service,
                handle,
                error,
                selected_options.lifecycle_limits.timeout_seconds,
            )
            raise

    async def delete(self, session: OpenAISandboxSession) -> OpenAISandboxSession:
        inner = _provider_inner(session)
        await inner.delete_backend()
        return session

    async def resume(self, state: SandboxSessionState) -> OpenAISandboxSession:
        if not isinstance(state, InMemorySandboxSessionState):
            raise TypeError("state must be an InMemorySandboxSessionState")
        if state.type != _PROVIDER_TYPE:
            raise ValueError(f"state type must be {_PROVIDER_TYPE}")
        if state.provider_state_version != 1:
            raise ValueError("provider_state_version must be 1")
        _parse_handle(state.sandbox_handle)
        SessionId.parse(state.core_session_id)
        if (
            isinstance(state.snapshot, InMemorySandboxSnapshot)
            and state.snapshot.owner_id != state.owner_id
        ):
            raise ValueError("snapshot owner does not match provider state")
        _validate_archive_metadata_values(
            state.workspace_archive_format_version,
            state.workspace_archive_revision,
            state.workspace_archive_root_hash,
        )
        validated_state = state.model_copy(deep=True)
        options = _options_from_state(validated_state)
        _validate_provider_options(options)
        plan = _manifest_plan(validated_state.manifest, validated_state.workspace_limits)
        handle = _parse_handle(validated_state.sandbox_handle)
        expected_session_id = SessionId.parse(validated_state.core_session_id)

        try:
            session = await self._service.get_session(handle)
        except SandboxNotFound:
            session = None

        if session is not None and session.session_id == expected_session_id:
            return self._wrap_provider_session(
                InMemorySandboxSession(
                    state=validated_state,
                    service=self._service,
                    session=session,
                    live_reattached=True,
                )
            )

        if not await self._snapshot_is_restorable(validated_state.snapshot):
            raise SnapshotNotRestorableError(
                snapshot_id=validated_state.snapshot.id,
                path=Path(f"<snapshot:{validated_state.snapshot.id}>"),
            )
        _require_archive_metadata(validated_state)

        new_handle, replacement = await self._allocate(options, plan)
        try:
            resumed_state = validated_state.model_copy(
                deep=True,
                update={
                    "sandbox_handle": str(new_handle),
                    "core_session_id": str(replacement.session_id),
                    "workspace_root_ready": False,
                },
            )
            return self._wrap_provider_session(
                InMemorySandboxSession(
                    state=resumed_state,
                    service=self._service,
                    session=replacement,
                    replacement_resume=True,
                    cleanup_on_start_failure=True,
                )
            )
        except BaseException as error:
            await _cleanup_handle_after_failure(
                self._service,
                new_handle,
                error,
                options.lifecycle_limits.timeout_seconds,
            )
            raise

    def deserialize_session_state(
        self,
        payload: dict[str, object],
    ) -> InMemorySandboxSessionState:
        state = self._deserialize_session_state_payload(
            payload,
            InMemorySandboxSessionState,
        )
        if not isinstance(state, InMemorySandboxSessionState):
            raise TypeError("session state payload did not produce provider state")
        return state

    async def _allocate(
        self,
        options: InMemorySandboxClientOptions,
        plan: _ManifestPlan,
    ) -> tuple[SandboxHandle, SandboxSession]:
        handle = await self._service.create(
            CreateSandboxRequest(
                owner_id=OwnerId(options.owner_id),
                options=SandboxOptions(
                    workspace_limits=options.workspace_limits,
                    lifecycle_limits=options.lifecycle_limits,
                ),
                initial_files=plan.files,
            )
        )
        try:
            session = await self._service.get_session(handle)
            await _materialize_directories(session, plan.directories)
            return handle, session
        except BaseException as error:
            await _cleanup_handle_after_failure(
                self._service,
                handle,
                error,
                options.lifecycle_limits.timeout_seconds,
            )
            raise

    def _wrap_provider_session(
        self,
        inner: InMemorySandboxSession,
    ) -> OpenAISandboxSession:
        wrapped = self._wrap_session(inner)
        wrapped.apply_manifest = inner.apply_manifest  # type: ignore[method-assign]
        wrapped.extract = inner.extract  # type: ignore[method-assign]
        wrapped._apply_entry_batch = inner._apply_entry_batch  # type: ignore[method-assign]
        return wrapped

    async def _snapshot_is_restorable(self, snapshot: SnapshotBase) -> bool:
        dependencies = self._resolve_dependencies()
        try:
            return await snapshot.restorable(dependencies=dependencies)
        finally:
            if dependencies is not None:
                await dependencies.aclose()


def _provider_inner(session: OpenAISandboxSession) -> InMemorySandboxSession:
    inner = cast(object, getattr(session, "_inner", None))
    if not isinstance(inner, InMemorySandboxSession):
        raise TypeError("session was not created by InMemorySandboxClient")
    return inner


def _options_from_state(
    state: InMemorySandboxSessionState,
) -> InMemorySandboxClientOptions:
    return InMemorySandboxClientOptions(
        owner_id=state.owner_id,
        workspace_limits=state.workspace_limits,
        lifecycle_limits=state.lifecycle_limits,
        max_stream_bytes=state.max_stream_bytes,
        manifest_profile_version=state.manifest_profile_version,
        exposed_ports=state.exposed_ports,
    )


def _validate_provider_options(options: InMemorySandboxClientOptions) -> None:
    if options.manifest_profile_version != 1:
        raise ValueError("manifest_profile_version must be 1")
    if options.exposed_ports:
        raise ValueError("exposed ports are not supported")
    OwnerId(options.owner_id)


def _manifest_plan(manifest: Manifest, limits: WorkspaceLimits) -> _ManifestPlan:
    if manifest.version != 1:
        raise ValueError("manifest version must be 1")
    if manifest.root != SandboxPath.ROOT:
        raise ValueError("manifest root must be /workspace")
    if manifest.environment.value:
        raise ValueError("manifest environment is not supported")
    if manifest.users:
        raise ValueError("manifest users are not supported")
    if manifest.groups:
        raise ValueError("manifest groups are not supported")
    if manifest.extra_path_grants:
        raise ValueError("manifest extra_path_grants are not supported")
    if manifest.remote_mount_command_allowlist != DEFAULT_REMOTE_MOUNT_COMMAND_ALLOWLIST:
        raise ValueError("custom remote_mount_command_allowlist is not supported")

    planned: list[tuple[str, BaseEntry]] = []
    seen: set[str] = set()
    for rel_path, entry in manifest.iter_entries():
        relative = rel_path.as_posix()
        normalized = "/".join(part for part in relative.split("/") if part not in ("", "."))
        if not normalized:
            raise InvalidManifestPathError(rel=relative, reason="escape_root")
        if normalized in seen:
            raise InvalidManifestPathError(
                rel=relative,
                reason="escape_root",
                context={"reason_detail": "duplicate_normalized_path"},
            )
        seen.add(normalized)
        _validate_entry(entry)
        try:
            SandboxPath.resolve(
                f"{SandboxPath.ROOT}/{normalized}",
                cwd=SandboxPath.root(),
                max_path_bytes=limits.max_path_bytes,
                max_segment_bytes=limits.max_segment_bytes,
            )
        except (InvalidPathError, PathOutsideWorkspaceError) as error:
            raise InvalidManifestPathError(
                rel=relative,
                reason="escape_root",
                cause=error,
            ) from error
        planned.append((normalized, entry))

    file_paths = {path for path, entry in planned if type(entry) is File}
    for path, _entry in planned:
        parts = path.split("/")
        for end in range(1, len(parts)):
            if "/".join(parts[:end]) in file_paths:
                raise InvalidManifestPathError(
                    rel=path,
                    reason="escape_root",
                    context={"reason_detail": "file_as_parent"},
                )

    _validate_manifest_limits(planned, limits)
    files = tuple(
        WorkspaceSeedFile(path, entry.content) for path, entry in planned if type(entry) is File
    )
    directories = tuple(
        sorted(
            (path for path, entry in planned if type(entry) is Dir),
            key=lambda value: (value.count("/"), value),
        )
    )
    return _ManifestPlan(files, directories)


def _entry_batch_plan(
    entries: Sequence[tuple[Path, BaseEntry]],
    limits: WorkspaceLimits,
) -> _ManifestPlan:
    manifest_entries: dict[str | Path, BaseEntry] = {}
    for destination, entry in entries:
        raw = destination.as_posix()
        try:
            path = SandboxPath.resolve(
                raw,
                cwd=SandboxPath.root(),
                max_path_bytes=limits.max_path_bytes,
                max_segment_bytes=limits.max_segment_bytes,
            )
        except (InvalidPathError, PathOutsideWorkspaceError) as error:
            raise InvalidManifestPathError(
                rel=raw,
                reason="escape_root",
                cause=error,
            ) from error
        if path.is_root:
            raise InvalidManifestPathError(rel=raw, reason="escape_root")
        relative = "/".join(path.parts)
        if relative in manifest_entries:
            raise InvalidManifestPathError(
                rel=raw,
                reason="escape_root",
                context={"reason_detail": "duplicate_normalized_path"},
            )
        manifest_entries[relative] = entry
    return _manifest_plan(Manifest(entries=manifest_entries), limits)


def _validate_manifest_limits(
    planned: Sequence[tuple[str, BaseEntry]],
    limits: WorkspaceLimits,
) -> None:
    total_bytes = 0
    node_paths: set[str] = set()
    for path, entry in planned:
        parts = path.split("/")
        for end in range(1, len(parts) + 1):
            node_paths.add("/".join(parts[:end]))
        if type(entry) is File:
            file_bytes = len(entry.content)
            if file_bytes > limits.max_file_bytes:
                raise FileSizeLimitExceededError(
                    f"manifest file {path} contains {file_bytes} bytes; limit is "
                    f"{limits.max_file_bytes} bytes"
                )
            total_bytes += file_bytes
    if total_bytes > limits.max_total_bytes:
        raise WorkspaceSizeLimitExceededError(
            f"manifest contains {total_bytes} bytes; limit is {limits.max_total_bytes} bytes"
        )
    node_count = len(node_paths) + 1
    if node_count > limits.max_nodes:
        raise NodeLimitExceededError(
            f"manifest contains {node_count} nodes; limit is {limits.max_nodes} nodes"
        )


def _validate_entry(entry: BaseEntry) -> None:
    if type(entry) is File:
        expected_permissions = File(content=b"").permissions
    elif type(entry) is Dir:
        expected_permissions = Dir().permissions
    else:
        raise ValueError(f"manifest entry type {entry.type} is not supported")
    if entry.ephemeral:
        raise ValueError(f"manifest entry {entry.type} ephemeral is not supported")
    if entry.group is not None:
        raise ValueError(f"manifest entry {entry.type} group is not supported")
    if entry.permissions != expected_permissions:
        raise ValueError(f"manifest entry {entry.type} permissions are not supported")


async def _materialize_plan(session: SandboxSession, plan: _ManifestPlan) -> None:
    await _materialize_directories(session, plan.directories)
    for seed in plan.files:
        await session.write_bytes(
            WriteBytesRequest(
                path=seed.path,
                content=seed.content,
                create_parents=True,
            )
        )


async def _materialize_directories(
    session: SandboxSession,
    directories: tuple[str, ...],
) -> None:
    for path in directories:
        await session.create_directory(
            CreateDirectoryRequest(
                path=path,
                create_parents=True,
                exist_ok=True,
            )
        )


def _parse_handle(value: str) -> SandboxHandle:
    try:
        return SandboxHandle(uuid.UUID(value))
    except (TypeError, ValueError) as error:
        raise ValueError("sandbox_handle must be a valid non-nil UUID") from error


def _validate_archive_metadata_values(
    format_version: int | None,
    revision: int | None,
    root_hash: str | None,
) -> None:
    metadata = (format_version, revision, root_hash)
    if any(value is not None for value in metadata) and any(value is None for value in metadata):
        raise ValueError("workspace archive metadata must be complete")
    if format_version is not None:
        if format_version <= 0:
            raise ValueError("workspace archive format version must be positive")
        Revision(cast(int, revision))
        ContentHash(cast(str, root_hash))


def _require_archive_metadata(
    state: InMemorySandboxSessionState,
) -> tuple[int, Revision, ContentHash]:
    format_version = state.workspace_archive_format_version
    revision = state.workspace_archive_revision
    root_hash = state.workspace_archive_root_hash
    if format_version is None or revision is None or root_hash is None:
        raise ValueError("workspace archive metadata must be complete")
    return format_version, Revision(revision), ContentHash(root_hash)


async def _cleanup_handle_after_failure(
    service: SandboxService,
    handle: SandboxHandle,
    primary: BaseException,
    timeout_seconds: float,
) -> bool:
    try:
        await _await_despite_native_cancellation(
            service.delete(handle),
            timeout_seconds=timeout_seconds,
        )
    except SandboxNotFound:
        return True
    except BaseException as cleanup_error:
        primary.add_note(f"secondary sandbox cleanup failure: {cleanup_error}")
        return False
    return True


async def _await_despite_native_cancellation[T](
    awaitable: Awaitable[T],
    *,
    timeout_seconds: float,
) -> T:
    task = asyncio.ensure_future(awaitable)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while not task.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            task.cancel()
            task.add_done_callback(_consume_background_task_result)
            raise TimeoutError("sandbox cleanup timed out")
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            continue
        if not done:
            task.cancel()
            task.add_done_callback(_consume_background_task_result)
            raise TimeoutError("sandbox cleanup timed out")
    return task.result()


def _consume_background_task_result(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()


def _reject_user(user: str | User | None) -> None:
    if user is not None:
        raise ValueError("sandbox users are not supported")


def _command_part(part: str | Path) -> str:
    value = part.as_posix() if isinstance(part, Path) else part
    if "\x00" in value:
        raise ValueError("command argv must not contain NUL")
    return value


def _read_bounded_binary(data: io.IOBase, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = data.read(min(_READ_CHUNK_BYTES, limit - total + 1))
        if not isinstance(chunk, bytes):
            raise _BinaryStreamTypeError(type(chunk).__name__)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise ValueError(f"stream exceeds the {limit}-byte input limit")
        chunks.append(chunk)


def _validate_archive_stream_size(
    archive: WorkspaceArchiveData,
    limit: int,
) -> None:
    size = len(archive.encoded)
    if size > limit:
        raise ValueError(f"stream exceeds the {limit}-byte input limit")


def _stream_value_type(error: TypeError) -> str:
    return error.actual_type if isinstance(error, _BinaryStreamTypeError) else "unknown"


def _file_entry(entry: object) -> FileEntry:
    from mem_sandbox.workspace import WorkspaceEntry

    if not isinstance(entry, WorkspaceEntry):
        raise TypeError("core listing returned an invalid workspace entry")
    if entry.kind is NodeKind.DIRECTORY:
        kind = EntryKind.DIRECTORY
        permissions = Dir().permissions
    elif entry.kind is NodeKind.FILE:
        kind = EntryKind.FILE
        permissions = File(content=b"").permissions
    else:
        kind = EntryKind.OTHER
        permissions = Permissions()
    return FileEntry(
        path=entry.path.value,
        permissions=permissions,
        owner="",
        group="",
        size=entry.size_bytes,
        kind=kind,
    )
