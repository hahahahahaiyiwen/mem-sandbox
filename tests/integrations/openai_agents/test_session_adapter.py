from __future__ import annotations

import asyncio
import io
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import UUID

import pytest
from agents.sandbox import Manifest, User
from agents.sandbox.entries import Dir, File, LocalFile
from agents.sandbox.errors import (
    InvalidManifestPathError,
    SnapshotNotRestorableError,
    SnapshotPersistError,
    WorkspaceArchiveReadError,
    WorkspaceWriteTypeError,
)
from agents.sandbox.files import EntryKind
from agents.sandbox.session import BaseSandboxSession, Dependencies
from agents.sandbox.session import SandboxSession as OpenAISandboxSession
from agents.sandbox.snapshot import NoopSnapshot, SnapshotBase
from pydantic import ValidationError

from mem_sandbox.core import OperationLimits, Revision, SnapshotId, SystemClock
from mem_sandbox.service import SandboxHandle, SandboxNotFound
from mem_sandbox.session import (
    ReadBytesRequest,
    SessionClosed,
    SessionExecuteRequest,
    SessionOperationCancelled,
    SessionOperationTimeout,
)
from mem_sandbox.session import SandboxSession as CoreSandboxSession
from mem_sandbox.snapshots import InMemorySnapshotStore, SnapshotRef, SnapshotStoreLimits
from mem_sandbox.workspace import (
    ContentHash,
    SnapshotCorruptError,
    WorkspaceArchiveData,
    WorkspaceLimits,
)
from mem_sandbox_openai_agents import (
    InMemorySandboxClient,
    InMemorySandboxClientOptions,
    InMemorySandboxSession,
    InMemorySandboxSessionState,
    InMemorySandboxSnapshot,
    InMemorySandboxSnapshotSpec,
)

from .support import RecordingService, create_service_bundle


class MemorySnapshot(SnapshotBase):
    type: str = "mem-sandbox-adapter-test"
    _payloads: ClassVar[dict[str, bytes]] = {}

    @classmethod
    def expire(cls, snapshot_id: str) -> None:
        del cls._payloads[snapshot_id]

    async def persist(
        self,
        data: io.IOBase,
        *,
        dependencies: Dependencies | None = None,
    ) -> None:
        _ = dependencies
        payload = data.read()
        if not isinstance(payload, bytes):
            raise TypeError("snapshot payload must be bytes")
        self._payloads[self.id] = payload

    async def restore(
        self,
        *,
        dependencies: Dependencies | None = None,
    ) -> io.IOBase:
        _ = dependencies
        payload = self._payloads.get(self.id)
        if payload is None:
            raise RuntimeError("snapshot has not been persisted")
        return io.BytesIO(payload)

    async def restorable(
        self,
        *,
        dependencies: Dependencies | None = None,
    ) -> bool:
        _ = dependencies
        return self.id in self._payloads


class FailOnceMemorySnapshot(MemorySnapshot):
    type: str = "mem-sandbox-adapter-fail-once-test"
    _fail_next: ClassVar[bool] = False

    @classmethod
    def fail_next_persist(cls) -> None:
        cls._fail_next = True

    async def persist(
        self,
        data: io.IOBase,
        *,
        dependencies: Dependencies | None = None,
    ) -> None:
        if self._fail_next:
            type(self)._fail_next = False
            payload = data.read()
            if not isinstance(payload, bytes):
                raise TypeError("snapshot payload must be bytes")
            self._payloads[self.id] = payload
            raise RuntimeError("snapshot persistence failed")
        await super().persist(data, dependencies=dependencies)


class DependencyMemorySnapshot(MemorySnapshot):
    type: str = "mem-sandbox-adapter-dependency-test"
    dependency_key: ClassVar[str] = "snapshot.owned-resource"
    fail_restore: bool = False

    async def restorable(self, *, dependencies: Dependencies | None = None) -> bool:
        if dependencies is None:
            raise RuntimeError("snapshot dependencies are required")
        await dependencies.require(self.dependency_key, consumer=type(self).__name__)
        return await super().restorable(dependencies=dependencies)

    async def restore(self, *, dependencies: Dependencies | None = None) -> io.IOBase:
        if dependencies is None:
            raise RuntimeError("snapshot dependencies are required")
        await dependencies.require(self.dependency_key, consumer=type(self).__name__)
        if self.fail_restore:
            raise RuntimeError("snapshot restore failed")
        return await super().restore(dependencies=dependencies)


class CountingMemorySnapshot(MemorySnapshot):
    type: str = "mem-sandbox-adapter-counting-test"
    persist_counts: ClassVar[dict[str, int]] = {}

    async def persist(
        self,
        data: io.IOBase,
        *,
        dependencies: Dependencies | None = None,
    ) -> None:
        self.persist_counts[self.id] = self.persist_counts.get(self.id, 0) + 1
        await super().persist(data, dependencies=dependencies)


class DependencyPersistMemorySnapshot(MemorySnapshot):
    type: str = "mem-sandbox-adapter-dependency-persist-test"
    dependency_key: ClassVar[str] = "snapshot.persist-owned-resource"
    persist_count: ClassVar[int] = 0

    async def persist(
        self,
        data: io.IOBase,
        *,
        dependencies: Dependencies | None = None,
    ) -> None:
        type(self).persist_count += 1
        if dependencies is None:
            raise RuntimeError("snapshot dependencies are required")
        await dependencies.require(self.dependency_key, consumer=type(self).__name__)
        await super().persist(data, dependencies=dependencies)


class OwnedDependency:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class ChunkedBinaryStream(io.RawIOBase):
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = chunks
        self.read_sizes: list[int] = []

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> object:
        self.read_sizes.append(size)
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class DeleteAfterCleanupFailureService(RecordingService):
    async def delete(self, handle: SandboxHandle) -> None:
        await super().delete(handle)
        raise RuntimeError("secondary delete failure")


class HangingDeleteService(RecordingService):
    async def delete(self, handle: SandboxHandle) -> None:
        self.deleted_handles.append(handle)
        await asyncio.Future[None]()


class DeleteAfterLookupService(RecordingService):
    delete_after_lookup = False

    async def get_session(self, handle: SandboxHandle) -> CoreSandboxSession:
        session = await super().get_session(handle)
        if self.delete_after_lookup:
            self.delete_after_lookup = False
            await self.delegate.delete(handle)
        return session


def _inner(session: OpenAISandboxSession) -> InMemorySandboxSession:
    inner = cast(object, cast(Any, session)._inner)
    assert isinstance(inner, InMemorySandboxSession)
    return inner


@pytest.mark.asyncio
async def test_client_returns_sdk_wrapper_and_binary_operations_use_public_session() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    sdk_session = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)

    assert isinstance(sdk_session, OpenAISandboxSession)
    await sdk_session.start()
    assert await sdk_session.running()
    await sdk_session.write(Path("/workspace/payload.bin"), io.BytesIO(b"\x00\xffdata"))

    stream = await sdk_session.read(Path("/workspace/payload.bin"))
    assert stream.read() == b"\x00\xffdata"
    core = await bundle.service.get_session(SandboxHandle(UUID(inner.state.sandbox_handle)))
    assert (await core.read_bytes(ReadBytesRequest(path="payload.bin"))).content == b"\x00\xffdata"

    await client.delete(sdk_session)
    await bundle.service.close()


@pytest.mark.asyncio
async def test_client_create_uses_default_options_when_sdk_omits_them() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)

    sdk_session = await client.create(manifest=Manifest())
    inner = _inner(sdk_session)

    assert inner.state.owner_id == InMemorySandboxClientOptions().owner_id
    assert inner.state.workspace_limits == InMemorySandboxClientOptions().workspace_limits
    await client.delete(sdk_session)
    await bundle.service.close()


def test_client_options_are_immutable() -> None:
    options = InMemorySandboxClientOptions()

    with pytest.raises(ValidationError):
        options.owner_id = "changed"


def test_mem_sandbox_snapshot_rejects_non_uuid_identifier() -> None:
    with pytest.raises(ValidationError, match="valid UUID"):
        InMemorySandboxSnapshot(id="not-a-snapshot-id")


@pytest.mark.asyncio
async def test_snapshot_preflight_closes_owned_dependency_resources() -> None:
    bundle = create_service_bundle()
    resources: list[OwnedDependency] = []
    dependencies = Dependencies()

    def create_resource(_: Dependencies) -> OwnedDependency:
        resource = OwnedDependency()
        resources.append(resource)
        return resource

    dependencies.bind_factory(
        DependencyMemorySnapshot.dependency_key,
        create_resource,
        owns_result=True,
    )
    client = InMemorySandboxClient(bundle.service, dependencies=dependencies)
    snapshot = DependencyMemorySnapshot(id="dependency-preflight")
    source = await client.create(snapshot=snapshot, manifest=Manifest())

    assert len(resources) == 1
    assert resources[0].closed

    await source.start()
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"value"))
    await source.stop()
    state = _inner(source).state.model_copy(deep=True)
    await client.delete(source)
    resumed = await client.resume(state)

    assert len(resources) == 2
    assert all(resource.closed for resource in resources)

    await resumed.start()
    assert len(resources) == 3
    assert not resources[-1].closed
    await resumed.aclose()
    assert resources[-1].closed
    await client.delete(resumed)
    await bundle.service.close()


@pytest.mark.asyncio
async def test_repeated_aclose_skips_closed_snapshot_dependencies() -> None:
    bundle = create_service_bundle()
    resources: list[OwnedDependency] = []
    DependencyPersistMemorySnapshot.persist_count = 0
    dependencies = Dependencies()

    def create_resource(_: Dependencies) -> OwnedDependency:
        resource = OwnedDependency()
        resources.append(resource)
        return resource

    dependencies.bind_factory(
        DependencyPersistMemorySnapshot.dependency_key,
        create_resource,
        owns_result=True,
    )
    client = InMemorySandboxClient(bundle.service, dependencies=dependencies)
    session = await client.create(
        snapshot=DependencyPersistMemorySnapshot(id="repeat-aclose"),
        manifest=Manifest(),
    )
    await session.start()

    await session.aclose()
    await session.aclose()

    assert len(resources) == 1
    assert resources[0].closed
    assert DependencyPersistMemorySnapshot.persist_count == 1
    await client.delete(session)
    await bundle.service.close()


@pytest.mark.asyncio
async def test_concurrent_stop_waits_for_aclose_shutdown_before_dependencies_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    resources: list[OwnedDependency] = []
    dependencies = Dependencies()
    shutdown_entered = asyncio.Event()
    release_shutdown = asyncio.Event()
    DependencyPersistMemorySnapshot.persist_count = 0

    def create_resource(_: Dependencies) -> OwnedDependency:
        resource = OwnedDependency()
        resources.append(resource)
        return resource

    dependencies.bind_factory(
        DependencyPersistMemorySnapshot.dependency_key,
        create_resource,
        owns_result=True,
    )
    client = InMemorySandboxClient(bundle.service, dependencies=dependencies)
    session = await client.create(
        snapshot=DependencyPersistMemorySnapshot(id="concurrent-close"),
        manifest=Manifest(),
    )
    await session.start()

    async def block_shutdown(_: BaseSandboxSession) -> None:
        shutdown_entered.set()
        await release_shutdown.wait()

    monkeypatch.setattr(BaseSandboxSession, "shutdown", block_shutdown)
    closing = asyncio.create_task(session.aclose())
    await shutdown_entered.wait()
    concurrent_stop = asyncio.create_task(session.stop())
    release_shutdown.set()
    await asyncio.gather(closing, concurrent_stop)

    assert DependencyPersistMemorySnapshot.persist_count == 1
    assert len(resources) == 1
    assert resources[0].closed
    await client.delete(session)
    await bundle.service.close()


@pytest.mark.asyncio
async def test_concurrent_start_fails_after_aclose_shutdown_begins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    session = await client.create(manifest=Manifest())
    await session.start()
    shutdown_entered = asyncio.Event()
    release_shutdown = asyncio.Event()

    async def block_shutdown(_: BaseSandboxSession) -> None:
        shutdown_entered.set()
        await release_shutdown.wait()

    monkeypatch.setattr(BaseSandboxSession, "shutdown", block_shutdown)
    closing = asyncio.create_task(session.aclose())
    await shutdown_entered.wait()
    starting = asyncio.create_task(session.start())
    release_shutdown.set()
    await closing

    with pytest.raises(SessionClosed, match="backend is closed"):
        await starting

    await client.delete(session)
    await bundle.service.close()


@pytest.mark.asyncio
async def test_failed_replacement_start_closes_session_owned_dependencies() -> None:
    bundle = create_service_bundle()
    resources: list[OwnedDependency] = []
    dependencies = Dependencies()

    def create_resource(_: Dependencies) -> OwnedDependency:
        resource = OwnedDependency()
        resources.append(resource)
        return resource

    dependencies.bind_factory(
        DependencyMemorySnapshot.dependency_key,
        create_resource,
        owns_result=True,
    )
    client = InMemorySandboxClient(bundle.service, dependencies=dependencies)
    source = await client.create(
        snapshot=DependencyMemorySnapshot(id="failed-start-dependency"),
        manifest=Manifest(),
    )
    await source.start()
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"value"))
    await source.stop()
    source_state = _inner(source).state
    failing_snapshot = cast(DependencyMemorySnapshot, source_state.snapshot).model_copy(
        update={"fail_restore": True}
    )
    failing_state = source_state.model_copy(
        deep=True,
        update={"snapshot": failing_snapshot},
    )
    await client.delete(source)
    resumed = await client.resume(failing_state)

    with pytest.raises(RuntimeError, match="snapshot restore failed"):
        await resumed.start()

    assert len(resources) == 3
    assert all(resource.closed for resource in resources)
    await bundle.service.close()


@pytest.mark.asyncio
async def test_session_rejects_users_and_shell_wrapping_before_core_execution() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    await sdk_session.start()

    with pytest.raises(ValueError, match="users are not supported"):
        await sdk_session.read(Path("/workspace/file.txt"), user=User(name="agent"))
    with pytest.raises(ValueError, match="shell execution is not supported"):
        await sdk_session.exec("pwd")
    with pytest.raises(ValueError, match="shell execution is not supported"):
        await sdk_session.exec("pwd", shell=["bash", "-lc"])

    result = await sdk_session.exec("pwd", shell=False)
    assert result.exit_code == 0
    assert result.stdout == b"/workspace\n"
    quoted = await sdk_session.exec("echo", "hello world", shell=False)
    assert quoted.exit_code == 0
    assert quoted.stdout == b"hello world\n"
    separator = await sdk_session.exec(
        "echo",
        "safe; touch /workspace/injected",
        shell=False,
    )
    assert separator.stdout == b"safe; touch /workspace/injected\n"
    assert await sdk_session.ls("/workspace") == []
    special = await sdk_session.exec(
        "echo",
        'quote"',
        "",
        "$HOME",
        "$(pwd)",
        "*",
        shell=False,
    )
    assert special.stdout == b'quote"  $HOME $(pwd) *\n'
    with pytest.raises(ValueError, match="NUL"):
        await sdk_session.exec("echo", "invalid\x00argument", shell=False)
    missing = await sdk_session.exec("cat", "missing.txt", shell=False)
    assert missing.exit_code != 0

    await bundle.service.close()


@pytest.mark.asyncio
async def test_native_list_mkdir_remove_and_path_confinement_avoid_sdk_posix_helpers() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    await sdk_session.start()
    await sdk_session.mkdir("/workspace/nested/empty", parents=True)
    await sdk_session.write(Path("/workspace/nested/file.bin"), io.BytesIO(b"abc"))
    entries = await sdk_session.ls("/workspace/nested")

    assert [(entry.path, entry.kind, entry.size) for entry in entries] == [
        ("/workspace/nested/empty", EntryKind.DIRECTORY, 0),
        ("/workspace/nested/file.bin", EntryKind.FILE, 3),
    ]
    await sdk_session.rm("/workspace/nested", recursive=True)
    assert await sdk_session.ls("/workspace") == []

    with pytest.raises(InvalidManifestPathError):
        await sdk_session.read(Path("/outside.txt"))
    with pytest.raises(InvalidManifestPathError):
        await sdk_session.write(Path("../escape.txt"), io.BytesIO(b"escape"))

    await bundle.service.close()


@pytest.mark.asyncio
async def test_exec_forwards_timeout_and_preserves_core_failure_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    await sdk_session.start()
    requests: list[SessionExecuteRequest] = []
    timeout_error = SessionOperationTimeout("core timeout")
    cancellation_error = SessionOperationCancelled("core cancellation")
    failures = iter([timeout_error, cancellation_error])

    async def fail_execute(
        self: CoreSandboxSession,
        request: SessionExecuteRequest,
    ) -> object:
        _ = self
        requests.append(request)
        raise next(failures)

    monkeypatch.setattr(CoreSandboxSession, "execute", fail_execute)

    with pytest.raises(SessionOperationTimeout) as timeout_raised:
        await sdk_session.exec("pwd", timeout=0.25, shell=False)
    assert timeout_raised.value is timeout_error
    assert requests[0].command_limits.timeout_seconds == 0.25
    assert requests[0].limits.timeout_seconds == 0.25

    with pytest.raises(SessionOperationCancelled) as cancellation_raised:
        await sdk_session.exec("pwd", shell=False)
    assert cancellation_raised.value is cancellation_error
    assert await sdk_session.ls("/workspace") == []

    await bundle.service.close()


@pytest.mark.asyncio
async def test_write_stream_is_incremental_bounded_and_binary_only() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(
            workspace_limits=WorkspaceLimits(max_file_bytes=4, max_total_bytes=4),
            max_stream_bytes=4,
        ),
    )
    await sdk_session.start()
    oversized = ChunkedBinaryStream([b"abc", b"de"])

    with pytest.raises(ValueError, match="exceeds the 4-byte input limit"):
        await sdk_session.write(Path("/workspace/too-large.bin"), oversized)
    assert oversized.read_sizes
    assert max(oversized.read_sizes) <= 5

    text = ChunkedBinaryStream(["text"])
    with pytest.raises(WorkspaceWriteTypeError):
        await sdk_session.write(Path("/workspace/text.bin"), text)

    await bundle.service.close()


@pytest.mark.asyncio
async def test_extract_is_rejected_without_reading_or_using_host_temporary_storage() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )

    class UnreadableArchive(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError(f"archive stream was read with size {size}")

    with pytest.raises(ValueError, match="archive extraction is not supported"):
        await sdk_session.extract(
            Path("/workspace"),
            UnreadableArchive(b"archive"),
            compression_scheme="tar",
        )

    await bundle.service.close()


@pytest.mark.asyncio
async def test_direct_persist_hydrate_round_trip_does_not_publish_resume_metadata() -> None:
    source_bundle = create_service_bundle()
    source_client = InMemorySandboxClient(source_bundle.service)
    source = await source_client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    source_inner = _inner(source)
    await source.start()
    await source.write(Path("/workspace/data.bin"), io.BytesIO(b"\x00archive"))

    stream = await source.persist_workspace()
    assert source_inner.state.workspace_archive_format_version is None
    assert source_inner.state.workspace_archive_revision is None
    assert source_inner.state.workspace_archive_root_hash is None

    target_bundle = create_service_bundle()
    target = await InMemorySandboxClient(target_bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    await target.start()
    await target.hydrate_workspace(stream)

    assert (await target.read(Path("/workspace/data.bin"))).read() == b"\x00archive"
    serialized = source_client.serialize_session_state(source_inner.state)
    assert isinstance(serialized["sandbox_handle"], str)
    assert serialized["workspace_archive_format_version"] is None
    assert serialized["workspace_archive_revision"] is None
    assert serialized["workspace_archive_root_hash"] is None

    await source_bundle.service.close()
    await target_bundle.service.close()


@pytest.mark.asyncio
async def test_resume_reattaches_to_a_live_handle_without_allocating_replacement() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    create_count = service.create_calls
    source_inner = _inner(source)
    await source.start()
    await source.write(Path("/workspace/live.bin"), io.BytesIO(b"live"))

    resumed = await client.resume(source_inner.state)
    resumed_inner = _inner(resumed)
    await resumed.start()

    assert resumed_inner.state.sandbox_handle == source_inner.state.sandbox_handle
    assert (await resumed.read(Path("/workspace/live.bin"))).read() == b"live"
    assert service.create_calls == create_count

    await service.close()


@pytest.mark.asyncio
async def test_resume_allocates_replacement_and_hydrates_sdk_snapshot_when_handle_is_gone() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    snapshot = MemorySnapshot(id="replacement")
    source = await client.create(
        snapshot=snapshot,
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    source_inner = _inner(source)
    await source.start()
    await source.write(Path("/workspace/restored.bin"), io.BytesIO(b"restored"))
    await source_inner.core_session.execute(
        SessionExecuteRequest(command="mkdir project; cd project; export MODE=restored")
    )
    await source.stop()
    original_handle = source_inner.state.sandbox_handle
    serialized = client.serialize_session_state(source_inner.state)
    restored_state = client.deserialize_session_state(serialized.copy())
    assert restored_state.workspace_archive_format_version is not None
    assert (
        restored_state.workspace_archive_format_version
        == source_inner.state.workspace_archive_format_version
    )
    assert (
        restored_state.workspace_archive_revision == source_inner.state.workspace_archive_revision
    )
    assert (
        restored_state.workspace_archive_root_hash == source_inner.state.workspace_archive_root_hash
    )
    assert restored_state.cwd == "/workspace/project"
    assert [(item.name, item.value) for item in restored_state.approved_environment] == [
        ("MODE", "restored")
    ]
    await client.delete(source)

    resumed = await client.resume(restored_state)
    resumed_inner = _inner(resumed)
    await resumed.start()

    assert resumed_inner.state.sandbox_handle != original_handle
    assert (await resumed.read(Path("/workspace/restored.bin"))).read() == b"restored"
    assert resumed_inner.core_session.cwd.value == "/workspace/project"
    assert resumed_inner.core_session.environment.get("MODE") == "restored"

    await bundle.service.close()


@pytest.mark.asyncio
async def test_sdk_close_rejects_later_manifest_application() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(entries={"configured.txt": File(content=b"configured")}),
    )
    await sdk_session.start()
    await sdk_session.aclose()

    with pytest.raises(SessionClosed):
        await sdk_session.apply_manifest()
    with pytest.raises(SessionClosed):
        await sdk_session.provision_manifest_accounts()

    await bundle.service.close()


def test_provider_state_v1_defaults_execution_context_for_older_payloads() -> None:
    state = InMemorySandboxSessionState(
        sandbox_handle="33333333-3333-3333-3333-333333333333",
        core_session_id="44444444-4444-4444-4444-444444444444",
        snapshot=NoopSnapshot(id="legacy-v1"),
        manifest=Manifest(),
    )
    client = InMemorySandboxClient(create_service_bundle().service)
    payload = client.serialize_session_state(state)
    del payload["cwd"]
    del payload["approved_environment"]

    restored = client.deserialize_session_state(payload)

    assert restored.provider_state_version == 1
    assert restored.cwd == "/workspace"
    assert restored.approved_environment == ()


@pytest.mark.asyncio
async def test_unstarted_replacement_close_preserves_original_durable_snapshot() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    source = await client.create(
        snapshot=MemorySnapshot(id="unstarted-replacement"),
        manifest=Manifest(),
    )
    await source.start()
    await source.write(Path("/workspace/original.txt"), io.BytesIO(b"original"))
    await source.stop()
    durable_state = _inner(source).state.model_copy(deep=True)
    await client.delete(source)

    replacement = await client.resume(durable_state)
    replacement_state = _inner(replacement).state
    original_snapshot_id = replacement_state.snapshot.id
    await replacement.aclose()

    assert replacement_state.snapshot.id == original_snapshot_id
    assert (
        replacement_state.workspace_archive_root_hash == durable_state.workspace_archive_root_hash
    )

    await client.delete(replacement)
    resumed = await client.resume(replacement_state)
    await resumed.start()
    assert (await resumed.read(Path("/workspace/original.txt"))).read() == b"original"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_resume_propagates_non_not_found_service_failure_without_allocating() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    create_count = service.create_calls
    service.get_session_error = RuntimeError("sentinel service failure")

    with pytest.raises(RuntimeError, match="sentinel service failure"):
        await client.resume(_inner(source).state)

    assert service.create_calls == create_count
    await service.close()


@pytest.mark.asyncio
async def test_resume_revalidates_manifest_before_lookup_or_allocation() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    state = _inner(source).state
    state.manifest = Manifest(
        entries={
            "nested": Dir(
                children={
                    "host.bin": LocalFile(src=Path("host.bin")),
                }
            )
        }
    )
    create_count = service.create_calls
    get_count = service.get_session_calls

    with pytest.raises(ValueError, match="local_file"):
        await client.resume(state)

    assert service.create_calls == create_count
    assert service.get_session_calls == get_count
    await service.close()


@pytest.mark.asyncio
async def test_hydrate_requires_metadata_and_invalid_archive_is_atomic() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)
    await sdk_session.start()
    await sdk_session.write(Path("/workspace/keep.bin"), io.BytesIO(b"keep"))

    with pytest.raises(WorkspaceArchiveReadError) as metadata_error:
        await sdk_session.hydrate_workspace(io.BytesIO(b"invalid"))
    assert isinstance(metadata_error.value.__cause__, ValueError)
    assert (await sdk_session.read(Path("/workspace/keep.bin"))).read() == b"keep"

    inner.state.workspace_archive_format_version = 1
    inner.state.workspace_archive_revision = 1
    inner.state.workspace_archive_root_hash = ContentHash.from_bytes(b"wrong").value
    with pytest.raises(WorkspaceArchiveReadError) as raised:
        await sdk_session.hydrate_workspace(io.BytesIO(b"not-a-valid-archive"))
    assert isinstance(raised.value.__cause__, SnapshotCorruptError)
    assert (await sdk_session.read(Path("/workspace/keep.bin"))).read() == b"keep"

    await bundle.service.close()


@pytest.mark.asyncio
async def test_hydrate_stream_is_incremental_bounded_binary_and_atomic() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(max_stream_bytes=4),
    )
    inner = _inner(sdk_session)
    await sdk_session.start()
    await sdk_session.write(Path("/workspace/keep.bin"), io.BytesIO(b"keep"))
    inner.state.workspace_archive_format_version = 1
    inner.state.workspace_archive_revision = 1
    inner.state.workspace_archive_root_hash = ContentHash.from_bytes(b"root").value

    oversized = ChunkedBinaryStream([b"abc", b"de"])
    with pytest.raises(WorkspaceArchiveReadError) as oversized_error:
        await sdk_session.hydrate_workspace(oversized)
    assert isinstance(oversized_error.value.__cause__, ValueError)
    assert oversized.read_sizes
    assert max(oversized.read_sizes) <= 5
    assert (await sdk_session.read(Path("/workspace/keep.bin"))).read() == b"keep"

    text = ChunkedBinaryStream(["text"])
    with pytest.raises(WorkspaceArchiveReadError) as type_error:
        await sdk_session.hydrate_workspace(text)
    assert isinstance(type_error.value.__cause__, TypeError)
    assert (await sdk_session.read(Path("/workspace/keep.bin"))).read() == b"keep"

    await bundle.service.close()


def test_state_is_json_safe_and_contains_no_live_core_objects() -> None:
    state = InMemorySandboxSessionState(
        sandbox_handle="11111111-1111-1111-1111-111111111111",
        core_session_id="22222222-2222-2222-2222-222222222222",
        owner_id="openai-agents",
        workspace_limits=WorkspaceLimits(),
        max_stream_bytes=1024,
        snapshot=NoopSnapshot(id="snapshot"),
        manifest=Manifest(),
    )

    payload = state.model_dump(mode="json")

    assert payload["type"] == "mem_sandbox"
    assert payload["provider_state_version"] == 1
    assert payload["sandbox_handle"] == "11111111-1111-1111-1111-111111111111"
    assert payload["core_session_id"] == "22222222-2222-2222-2222-222222222222"
    assert "service" not in payload
    assert "session" not in payload
    assert "archive_port" not in payload


@pytest.mark.asyncio
async def test_snapshot_overrides_disable_preclear_and_posix_fingerprinting() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)

    await sdk_session.start()
    assert inner._should_compute_snapshot_fingerprint_on_persist() is False  # pyright: ignore[reportPrivateUsage]
    await sdk_session.write(Path("/workspace/keep.bin"), io.BytesIO(b"keep"))
    await inner._clear_workspace_root_on_resume()  # pyright: ignore[reportPrivateUsage]
    assert (await sdk_session.read(Path("/workspace/keep.bin"))).read() == b"keep"

    await bundle.service.close()


def test_archive_metadata_types_are_reconstructable_from_json_state() -> None:
    archive = WorkspaceArchiveData(
        encoded=b"archive",
        format_version=1,
        workspace_revision=Revision(7),
        root_hash=ContentHash.from_bytes(b"root"),
    )

    assert Revision(archive.workspace_revision.value) == archive.workspace_revision
    assert ContentHash(archive.root_hash.value) == archive.root_hash


@pytest.mark.asyncio
async def test_live_reattach_preserves_changes_newer_than_the_last_snapshot() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    snapshot = MemorySnapshot(id="live")
    source = await client.create(
        snapshot=snapshot,
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    await source.start()
    await source.write(Path("/workspace/value.bin"), io.BytesIO(b"snapshot"))
    await source.stop()
    await source.write(Path("/workspace/value.bin"), io.BytesIO(b"newer"))
    source.state.workspace_root_ready = False

    resumed = await client.resume(source.state)
    await resumed.start()

    assert (await resumed.read(Path("/workspace/value.bin"))).read() == b"newer"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_sdk_live_entry_batch_delegates_to_native_manifest_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)
    await sdk_session.start()

    async def forbidden_exec(*args: object, **kwargs: object) -> object:
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(inner, "_exec_internal", forbidden_exec)
    await sdk_session._apply_entry_batch(  # pyright: ignore[reportPrivateUsage]
        [(Path("/workspace/live.bin"), File(content=b"\x00live"))],
        base_dir=Path.cwd(),
    )

    assert (await sdk_session.read(Path("/workspace/live.bin"))).read() == b"\x00live"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_failed_snapshot_overwrite_preserves_previous_metadata_and_payload() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    snapshot = FailOnceMemorySnapshot(id="failed-overwrite")
    source = await client.create(
        snapshot=snapshot,
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    source_inner = _inner(source)
    await source.start()
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"first"))
    await source.stop()
    previous_metadata = (
        source_inner.state.workspace_archive_format_version,
        source_inner.state.workspace_archive_revision,
        source_inner.state.workspace_archive_root_hash,
    )
    previous_snapshot_id = source_inner.state.snapshot.id

    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"second"))
    FailOnceMemorySnapshot.fail_next_persist()
    with pytest.raises(RuntimeError, match="snapshot persistence failed"):
        await source.stop()

    assert (
        source_inner.state.workspace_archive_format_version,
        source_inner.state.workspace_archive_revision,
        source_inner.state.workspace_archive_root_hash,
    ) == previous_metadata
    assert source_inner.state.snapshot.id == previous_snapshot_id

    restored_state = client.deserialize_session_state(
        client.serialize_session_state(source_inner.state)
    )
    await client.delete(source)
    resumed = await client.resume(restored_state)
    await resumed.start()

    assert (await resumed.read(Path("/workspace/value.txt"))).read() == b"first"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_direct_workspace_export_preserves_last_durable_resume_pair() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    source = await client.create(
        snapshot=MemorySnapshot(id="direct-export"),
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    source_inner = _inner(source)
    await source.start()
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"first"))
    await source.stop()
    durable_state = client.serialize_session_state(source_inner.state)

    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"second"))
    direct_stream = await source.persist_workspace()

    assert client.serialize_session_state(source_inner.state) == durable_state

    direct_target = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(),
    )
    await direct_target.start()
    await direct_target.hydrate_workspace(direct_stream)
    assert (await direct_target.read(Path("/workspace/value.txt"))).read() == b"second"

    await client.delete(source)
    resumed = await client.resume(client.deserialize_session_state(durable_state.copy()))
    await resumed.start()
    assert (await resumed.read(Path("/workspace/value.txt"))).read() == b"first"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_snapshot_persist_rejects_archive_larger_than_hydration_stream_limit() -> None:
    bundle = create_service_bundle()
    snapshot = MemorySnapshot(id="bounded-persist")
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        snapshot=snapshot,
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(max_stream_bytes=4),
    )
    inner = _inner(sdk_session)
    await sdk_session.start()

    with pytest.raises(ValueError, match="exceeds the 4-byte input limit"):
        await sdk_session.stop()

    assert await snapshot.restorable(dependencies=inner.dependencies) is False
    assert inner.state.workspace_archive_format_version is None
    assert inner.state.workspace_archive_revision is None
    assert inner.state.workspace_archive_root_hash is None
    await bundle.service.close()


@pytest.mark.asyncio
async def test_create_rejects_an_already_restorable_snapshot_before_allocation() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    snapshot = MemorySnapshot(id="existing")
    await snapshot.persist(io.BytesIO(b"existing"))

    with pytest.raises(ValueError, match="restorable snapshots require session state"):
        await client.create(
            snapshot=snapshot,
            manifest=Manifest(),
            options=InMemorySandboxClientOptions(),
        )

    assert service.create_calls == 0
    await service.close()


@pytest.mark.asyncio
async def test_provider_state_serialization_round_trip_preserves_resume_configuration() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    state = InMemorySandboxSessionState(
        sandbox_handle="11111111-1111-1111-1111-111111111111",
        core_session_id="22222222-2222-2222-2222-222222222222",
        owner_id="openai-agents",
        workspace_limits=WorkspaceLimits(
            max_file_bytes=512,
            max_total_bytes=1024,
        ),
        max_stream_bytes=2048,
        snapshot=NoopSnapshot(id="snapshot"),
        manifest=Manifest(),
    )

    payload = client.serialize_session_state(state)
    restored = client.deserialize_session_state(payload.copy())

    assert restored == state
    assert isinstance(restored, InMemorySandboxSessionState)
    assert restored.workspace_limits == WorkspaceLimits(
        max_file_bytes=512,
        max_total_bytes=1024,
    )
    await bundle.service.close()


def test_provider_state_rejects_unknown_fields_and_versions() -> None:
    state = InMemorySandboxSessionState(
        sandbox_handle="11111111-1111-1111-1111-111111111111",
        core_session_id="22222222-2222-2222-2222-222222222222",
        snapshot=NoopSnapshot(id="snapshot"),
        manifest=Manifest(),
    )
    client = InMemorySandboxClient(create_service_bundle().service)
    payload = client.serialize_session_state(state)

    unsupported = payload.copy()
    unsupported["provider_state_version"] = 2
    with pytest.raises(ValueError, match="session state payload is invalid"):
        client.deserialize_session_state(unsupported)

    unknown = payload.copy()
    unknown["future_provider_field"] = "unsupported"
    with pytest.raises(ValueError, match="session state payload is invalid"):
        client.deserialize_session_state(unknown)


@pytest.mark.asyncio
async def test_resume_rejects_missing_snapshot_before_replacement_allocation() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(manifest=Manifest())
    source_state = _inner(source).state.model_copy(deep=True)
    await client.delete(source)
    create_count = service.create_calls

    with pytest.raises(SnapshotNotRestorableError):
        await client.resume(source_state)

    assert service.create_calls == create_count
    await service.close()


@pytest.mark.asyncio
async def test_resume_rejects_invalid_identity_before_service_lookup() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(manifest=Manifest())
    invalid_state = _inner(source).state.model_copy(
        deep=True,
        update={"core_session_id": "not-a-session-id"},
    )
    get_count = service.get_session_calls

    with pytest.raises(ValueError, match="valid UUID"):
        await client.resume(invalid_state)

    assert service.get_session_calls == get_count
    await service.close()


@pytest.mark.asyncio
async def test_resume_rejects_incomplete_archive_metadata_before_service_lookup() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(manifest=Manifest())
    invalid_state = _inner(source).state.model_copy(
        deep=True,
        update={"workspace_archive_format_version": 1},
    )
    get_count = service.get_session_calls

    with pytest.raises(ValueError, match="archive metadata must be complete"):
        await client.resume(invalid_state)

    assert service.get_session_calls == get_count
    await service.close()


@pytest.mark.asyncio
async def test_resume_rejects_restorable_snapshot_without_archive_metadata() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(
        snapshot=MemorySnapshot(id="missing-metadata"), manifest=Manifest()
    )
    await source.start()
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"value"))
    await source.stop()
    invalid_state = _inner(source).state.model_copy(
        deep=True,
        update={
            "workspace_archive_format_version": None,
            "workspace_archive_revision": None,
            "workspace_archive_root_hash": None,
        },
    )
    await client.delete(source)
    create_count = service.create_calls

    with pytest.raises(ValueError, match="archive metadata must be complete"):
        await client.resume(invalid_state)

    assert service.create_calls == create_count
    await service.close()


@pytest.mark.asyncio
async def test_resume_rejects_handle_collision_and_restores_an_independent_replacement() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(
        snapshot=MemorySnapshot(id="identity-guard"),
        manifest=Manifest(),
    )
    source_inner = _inner(source)
    await source.start()
    await source.write(Path("/workspace/source.txt"), io.BytesIO(b"source"))
    await source.stop()
    resumable_state = source_inner.state.model_copy(deep=True)
    await client.delete(source)

    unrelated = await client.create(manifest=Manifest())
    unrelated_inner = _inner(unrelated)
    await unrelated.start()
    await unrelated.write(Path("/workspace/unrelated.txt"), io.BytesIO(b"unrelated"))
    colliding_state = resumable_state.model_copy(
        deep=True,
        update={"sandbox_handle": unrelated_inner.state.sandbox_handle},
    )

    resumed = await client.resume(colliding_state)
    resumed_inner = _inner(resumed)
    await resumed.start()

    assert resumed_inner.state.sandbox_handle != unrelated_inner.state.sandbox_handle
    assert resumed_inner.state.core_session_id != unrelated_inner.state.core_session_id
    assert (await resumed.read(Path("/workspace/source.txt"))).read() == b"source"
    assert (await unrelated.read(Path("/workspace/unrelated.txt"))).read() == b"unrelated"

    await service.close()


@pytest.mark.asyncio
async def test_repeated_missing_handle_resume_creates_independent_snapshot_forks() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    source = await client.create(
        snapshot=MemorySnapshot(id="fork-source"),
        manifest=Manifest(),
    )
    source_inner = _inner(source)
    await source.start()
    await source.write(Path("/workspace/shared.txt"), io.BytesIO(b"snapshot"))
    await source.stop()
    persisted_state = source_inner.state.model_copy(deep=True)
    original_payload = client.serialize_session_state(persisted_state)
    await client.delete(source)

    first, second = await asyncio.gather(
        client.resume(persisted_state),
        client.resume(persisted_state),
    )
    await asyncio.gather(first.start(), second.start())
    first_inner = _inner(first)
    second_inner = _inner(second)

    assert first_inner.state.sandbox_handle != second_inner.state.sandbox_handle
    assert first_inner.state.core_session_id != second_inner.state.core_session_id
    assert (await first.read(Path("/workspace/shared.txt"))).read() == b"snapshot"
    assert (await second.read(Path("/workspace/shared.txt"))).read() == b"snapshot"
    await first.write(Path("/workspace/first.txt"), io.BytesIO(b"first"))
    assert [entry.path for entry in await second.ls("/workspace")] == ["/workspace/shared.txt"]
    assert client.serialize_session_state(persisted_state) == original_payload

    await bundle.service.close()


@pytest.mark.asyncio
async def test_delete_is_idempotent_for_concurrent_and_repeated_calls() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    session = await client.create(manifest=Manifest())

    deleted = await asyncio.gather(client.delete(session), client.delete(session))
    repeated = await client.delete(session)

    assert deleted == [session, session]
    assert repeated is session
    await bundle.service.close()


@pytest.mark.asyncio
async def test_start_after_backend_delete_fails() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    session = await client.create(manifest=Manifest())
    await client.delete(session)

    with pytest.raises(SessionClosed, match="backend is closed"):
        await session.start()

    await bundle.service.close()


@pytest.mark.asyncio
async def test_live_resume_start_fails_when_backend_is_deleted_after_lookup() -> None:
    bundle = create_service_bundle()
    service = DeleteAfterLookupService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(manifest=Manifest())
    service.delete_after_lookup = True

    alias = await client.resume(_inner(source).state)

    with pytest.raises(SessionClosed, match="backend is closed"):
        await alias.start()

    await bundle.service.close()


@pytest.mark.asyncio
async def test_live_resume_aliases_share_one_handle_and_idempotent_deletion() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    source = await client.create(manifest=Manifest())
    source_inner = _inner(source)
    first, second = await asyncio.gather(
        client.resume(source_inner.state),
        client.resume(source_inner.state),
    )

    assert _inner(first).state.sandbox_handle == source_inner.state.sandbox_handle
    assert _inner(second).state.core_session_id == source_inner.state.core_session_id
    await first.start()
    await first.write(Path("/workspace/shared.txt"), io.BytesIO(b"shared"))
    await second.start()
    assert (await second.read(Path("/workspace/shared.txt"))).read() == b"shared"

    await asyncio.gather(client.delete(first), client.delete(second))
    assert not await source.running()
    assert await client.delete(source) is source
    await bundle.service.close()


@pytest.mark.asyncio
async def test_snapshot_alias_close_after_shared_backend_deletion_is_safe() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    source = await client.create(
        snapshot=MemorySnapshot(id="live-alias-close"),
        manifest=Manifest(),
    )
    alias = await client.resume(_inner(source).state)
    await asyncio.gather(source.start(), alias.start())

    await client.delete(source)
    await alias.aclose()

    assert not await alias.running()
    await bundle.service.close()


@pytest.mark.asyncio
async def test_create_wrapper_failure_deletes_allocated_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)

    def fail_wrapper(inner: InMemorySandboxSession) -> OpenAISandboxSession:
        _ = inner
        raise RuntimeError("wrapper construction failed")

    monkeypatch.setattr(client, "_wrap_provider_session", fail_wrapper)

    with pytest.raises(RuntimeError, match="wrapper construction failed"):
        await client.create(manifest=Manifest())

    assert service.deleted_handles == service.created_handles
    await service.close()


@pytest.mark.asyncio
async def test_replacement_resume_wrapper_failure_deletes_allocated_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(
        snapshot=MemorySnapshot(id="resume-wrapper-failure"),
        manifest=Manifest(),
    )
    await source.start()
    await source.stop()
    resumable_state = _inner(source).state.model_copy(deep=True)
    await client.delete(source)
    deleted_before_resume = len(service.deleted_handles)

    def fail_wrapper(inner: InMemorySandboxSession) -> OpenAISandboxSession:
        _ = inner
        raise RuntimeError("resume wrapper construction failed")

    monkeypatch.setattr(client, "_wrap_provider_session", fail_wrapper)

    with pytest.raises(RuntimeError, match="resume wrapper construction failed"):
        await client.resume(resumable_state)

    assert len(service.deleted_handles) == deleted_before_resume + 1
    assert service.deleted_handles[-1] == service.created_handles[-1]
    await service.close()


@pytest.mark.asyncio
async def test_new_handle_start_failure_deletes_backend_and_preserves_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    session = await client.create(manifest=Manifest())
    inner = _inner(session)
    handle = inner.handle

    async def fail_start_workspace() -> None:
        raise RuntimeError("provider start failed")

    monkeypatch.setattr(inner, "_start_workspace", fail_start_workspace)

    with pytest.raises(RuntimeError, match="provider start failed"):
        await session.start()

    assert service.deleted_handles == [handle]
    with pytest.raises(SandboxNotFound):
        await service.get_session(handle)
    await service.close()


@pytest.mark.asyncio
async def test_start_failure_preserves_primary_error_when_cleanup_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    service = DeleteAfterCleanupFailureService(bundle.service)
    client = InMemorySandboxClient(service)
    session = await client.create(manifest=Manifest())
    inner = _inner(session)

    async def fail_start_workspace() -> None:
        raise RuntimeError("primary provider start failure")

    monkeypatch.setattr(inner, "_start_workspace", fail_start_workspace)

    with pytest.raises(RuntimeError, match="primary provider start failure") as raised:
        await session.start()

    assert any(
        "secondary sandbox cleanup failure: secondary delete failure" in note
        for note in raised.value.__notes__
    )
    await bundle.service.close()


@pytest.mark.asyncio
async def test_start_failure_cleanup_timeout_preserves_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    service = HangingDeleteService(bundle.service)
    client = InMemorySandboxClient(service)
    session = await client.create(
        manifest=Manifest(),
        options=InMemorySandboxClientOptions(
            lifecycle_limits=OperationLimits(
                timeout_seconds=0.1,
                terminal_event_reserve_seconds=0.01,
            )
        ),
    )
    inner = _inner(session)

    async def fail_start_workspace() -> None:
        raise RuntimeError("primary provider start failure")

    monkeypatch.setattr(inner, "_start_workspace", fail_start_workspace)

    with pytest.raises(RuntimeError, match="primary provider start failure") as raised:
        await asyncio.wait_for(session.start(), timeout=1)

    assert any(
        "secondary sandbox cleanup failure: sandbox cleanup timed out" in note
        for note in raised.value.__notes__
    )
    await bundle.service.close()


@pytest.mark.asyncio
async def test_cancelled_start_waits_for_new_handle_cleanup() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    session = await client.create(manifest=Manifest())
    inner = _inner(session)
    handle = inner.handle
    entered = asyncio.Event()

    async def block_start_workspace() -> None:
        entered.set()
        await asyncio.Future[None]()

    inner._start_workspace = block_start_workspace  # pyright: ignore[reportPrivateUsage]
    starting = asyncio.create_task(session.start())
    await entered.wait()
    starting.cancel()

    with pytest.raises(asyncio.CancelledError):
        await starting

    assert service.deleted_handles == [handle]
    with pytest.raises(SandboxNotFound):
        await service.get_session(handle)
    await service.close()


@pytest.mark.asyncio
async def test_live_reattachment_start_failure_preserves_existing_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(manifest=Manifest())
    source_inner = _inner(source)
    resumed = await client.resume(source_inner.state)
    resumed_inner = _inner(resumed)

    async def fail_probe() -> bool:
        raise RuntimeError("reattachment start failed")

    monkeypatch.setattr(resumed_inner, "_probe_workspace_root_for_preserved_resume", fail_probe)

    with pytest.raises(RuntimeError, match="reattachment start failed"):
        await resumed.start()

    assert service.deleted_handles == []
    assert await service.get_session(source_inner.handle) is not None
    await service.close()


@pytest.mark.asyncio
async def test_replacement_resume_start_failure_deletes_new_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(
        snapshot=MemorySnapshot(id="resume-start-failure"),
        manifest=Manifest(),
    )
    await source.start()
    await source.stop()
    resumable_state = _inner(source).state.model_copy(deep=True)
    await client.delete(source)
    resumed = await client.resume(resumable_state)
    resumed_inner = _inner(resumed)
    replacement_handle = resumed_inner.handle

    async def fail_start_workspace() -> None:
        raise RuntimeError("replacement start failed")

    monkeypatch.setattr(resumed_inner, "_start_workspace", fail_start_workspace)

    with pytest.raises(RuntimeError, match="replacement start failed"):
        await resumed.start()

    assert service.deleted_handles[-1] == replacement_handle
    with pytest.raises(SandboxNotFound):
        await service.get_session(replacement_handle)
    await service.close()


@pytest.mark.asyncio
async def test_replacement_resume_fails_if_snapshot_disappears_before_start() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    source = await client.create(
        snapshot=MemorySnapshot(id="expiring-resume"),
        manifest=Manifest(),
    )
    await source.start()
    await source.stop()
    resumable_state = _inner(source).state.model_copy(deep=True)
    await client.delete(source)
    resumed = await client.resume(resumable_state)
    replacement_handle = _inner(resumed).handle
    MemorySnapshot.expire(resumable_state.snapshot.id)

    with pytest.raises(SnapshotNotRestorableError):
        await resumed.start()

    assert service.deleted_handles[-1] == replacement_handle
    with pytest.raises(SandboxNotFound):
        await service.get_session(replacement_handle)
    await service.close()


@pytest.mark.asyncio
async def test_aclose_after_delete_does_not_replace_caller_failure() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    session = await client.create(
        snapshot=MemorySnapshot(id="delete-before-close"),
        manifest=Manifest(),
    )
    await session.start()
    await client.delete(session)

    await session.aclose()
    await bundle.service.close()


@pytest.mark.asyncio
async def test_mem_sandbox_snapshot_bridge_uses_existing_store_without_serializing_it() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(
        bundle.service,
        snapshot_store=bundle.snapshot_store,
        clock=bundle.clock,
    )
    source = await client.create(
        snapshot=InMemorySandboxSnapshotSpec(),
        manifest=Manifest(),
    )
    source_inner = _inner(source)
    await source.start()
    await source.write(Path("/workspace/stored.txt"), io.BytesIO(b"stored"))
    await source.stop()
    payload = client.serialize_session_state(source_inner.state)

    assert payload["snapshot"] == {
        "type": "mem_sandbox_store",
        "id": source_inner.state.snapshot.id,
        "store_dependency_key": "mem_sandbox.snapshot_store",
        "owner_id": "openai-agents",
    }
    assert "service" not in payload
    assert "clock" not in payload
    stored = await bundle.snapshot_store.load(
        SnapshotRef(SnapshotId.parse(source_inner.state.snapshot.id))
    )
    assert stored.metadata.format_name == "openai-portable-workspace"
    assert stored.source_session_id.value == UUID(source_inner.state.core_session_id)

    await client.delete(source)
    replacement_bundle = create_service_bundle(
        snapshot_store=bundle.snapshot_store,
        clock=bundle.clock,
    )
    replacement_client = InMemorySandboxClient(
        replacement_bundle.service,
        snapshot_store=replacement_bundle.snapshot_store,
        clock=replacement_bundle.clock,
    )
    resumed = await replacement_client.resume(
        replacement_client.deserialize_session_state(payload.copy())
    )
    await resumed.start()

    assert (await resumed.read(Path("/workspace/stored.txt"))).read() == b"stored"
    await bundle.service.close()
    await replacement_bundle.service.close()


@pytest.mark.asyncio
async def test_mem_sandbox_snapshot_bridge_skips_unchanged_repeat_persistence() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(
        bundle.service,
        snapshot_store=bundle.snapshot_store,
        clock=bundle.clock,
    )
    session = await client.create(
        snapshot=InMemorySandboxSnapshotSpec(),
        manifest=Manifest(),
    )
    await session.start()
    await session.write(Path("/workspace/value.txt"), io.BytesIO(b"value"))

    await session.stop()
    first_snapshot_id = session.state.snapshot.id
    await session.stop()

    assert session.state.snapshot.id == first_snapshot_id
    assert (await bundle.snapshot_store.stats()).snapshot_count == 1
    await bundle.service.close()


@pytest.mark.asyncio
async def test_generic_snapshot_is_persisted_again_when_workspace_is_unchanged() -> None:
    bundle = create_service_bundle()
    CountingMemorySnapshot.persist_counts.clear()
    client = InMemorySandboxClient(bundle.service)
    session = await client.create(
        snapshot=CountingMemorySnapshot(id="generic-repeat-persistence"),
        manifest=Manifest(),
    )
    await session.start()

    await session.stop()
    await session.stop()

    assert sum(CountingMemorySnapshot.persist_counts.values()) == 2
    await bundle.service.close()


@pytest.mark.asyncio
async def test_mem_sandbox_snapshot_bridge_preserves_immutable_snapshot_history() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(
        bundle.service,
        snapshot_store=bundle.snapshot_store,
        clock=bundle.clock,
    )
    source = await client.create(
        snapshot=InMemorySandboxSnapshotSpec(),
        manifest=Manifest(),
    )
    await source.start()
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"first"))
    await source.stop()
    first_state = source.state.model_copy(deep=True)
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"second"))
    await source.stop()
    second_state = source.state.model_copy(deep=True)

    assert first_state.snapshot.id != second_state.snapshot.id
    assert (await bundle.snapshot_store.stats()).snapshot_count == 2
    await client.delete(source)

    first_resume, second_resume = await asyncio.gather(
        client.resume(first_state),
        client.resume(second_state),
    )
    await asyncio.gather(first_resume.start(), second_resume.start())

    assert (await first_resume.read(Path("/workspace/value.txt"))).read() == b"first"
    assert (await second_resume.read(Path("/workspace/value.txt"))).read() == b"second"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_snapshot_quota_failure_preserves_last_durable_state() -> None:
    clock = SystemClock()
    store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=1,
            max_total_payload_bytes=64 * 1024 * 1024,
        ),
        clock=clock,
    )
    bundle = create_service_bundle(snapshot_store=store, clock=clock)
    client = InMemorySandboxClient(
        bundle.service,
        snapshot_store=store,
        clock=clock,
    )
    source = await client.create(
        snapshot=InMemorySandboxSnapshotSpec(),
        manifest=Manifest(),
    )
    await source.start()
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"durable"))
    await source.stop()
    durable_state = source.state.model_copy(deep=True)
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"not persisted"))

    with pytest.raises(SnapshotPersistError):
        await source.stop()

    assert source.state.snapshot.id == durable_state.snapshot.id
    assert (await store.stats()).snapshot_count == 1
    await client.delete(source)
    resumed = await client.resume(durable_state)
    await resumed.start()
    assert (await resumed.read(Path("/workspace/value.txt"))).read() == b"durable"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_mem_sandbox_snapshot_bridge_rejects_state_metadata_mismatch() -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(
        bundle.service,
        snapshot_store=bundle.snapshot_store,
        clock=bundle.clock,
    )
    source = await client.create(
        snapshot=InMemorySandboxSnapshotSpec(),
        manifest=Manifest(),
    )
    await source.start()
    await source.write(Path("/workspace/value.txt"), io.BytesIO(b"value"))
    await source.stop()
    payload = client.serialize_session_state(source.state)
    payload["workspace_archive_revision"] = cast(int, payload["workspace_archive_revision"]) + 1
    await client.delete(source)

    replacement_bundle = create_service_bundle(
        snapshot_store=bundle.snapshot_store,
        clock=bundle.clock,
    )
    replacement_client = InMemorySandboxClient(
        replacement_bundle.service,
        snapshot_store=replacement_bundle.snapshot_store,
        clock=replacement_bundle.clock,
    )
    resumed = await replacement_client.resume(
        replacement_client.deserialize_session_state(payload.copy())
    )
    replacement_handle = _inner(resumed).handle

    with pytest.raises(WorkspaceArchiveReadError):
        await resumed.start()

    with pytest.raises(SandboxNotFound):
        await replacement_bundle.service.get_session(replacement_handle)
    await bundle.service.close()
    await replacement_bundle.service.close()


@pytest.mark.asyncio
async def test_mem_sandbox_snapshot_bridge_rejects_owner_mismatch_before_lookup() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(
        service,
        snapshot_store=bundle.snapshot_store,
        clock=bundle.clock,
    )
    source = await client.create(
        snapshot=InMemorySandboxSnapshotSpec(),
        manifest=Manifest(),
    )
    await source.start()
    await source.stop()
    mismatched_state = source.state.model_copy(deep=True, update={"owner_id": "other-owner"})
    get_count = service.get_session_calls

    with pytest.raises(ValueError, match="snapshot owner"):
        await client.resume(mismatched_state)

    assert service.get_session_calls == get_count
    await service.close()


@pytest.mark.asyncio
async def test_mem_sandbox_snapshot_bridge_requires_configured_store_before_allocation() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)

    with pytest.raises(RuntimeError, match="requires dependency"):
        await client.create(
            snapshot=InMemorySandboxSnapshotSpec(),
            manifest=Manifest(),
        )

    assert service.create_calls == 0
    await service.close()


@pytest.mark.parametrize(
    "method_name",
    [
        "exec",
        "_probe_workspace_root_for_preserved_resume",
        "_start_workspace",
        "_persist_snapshot",
        "_clear_workspace_root_on_resume",
        "_should_compute_snapshot_fingerprint_on_persist",
        "_can_skip_snapshot_restore_on_resume",
        "_set_start_state_preserved",
        "_validate_path_access",
        "ls",
        "mkdir",
        "rm",
        "extract",
        "_validate_manifest_application",
        "_apply_manifest",
        "_apply_entry_batch",
        "provision_manifest_accounts",
    ],
)
def test_provider_overrides_required_sdk_hooks(method_name: str) -> None:
    assert method_name in InMemorySandboxSession.__dict__
