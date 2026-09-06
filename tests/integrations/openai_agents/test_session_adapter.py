from __future__ import annotations

import io
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import UUID

import pytest
from agents.sandbox import Manifest, User
from agents.sandbox.entries import Dir, File, LocalFile
from agents.sandbox.errors import (
    InvalidManifestPathError,
    WorkspaceArchiveReadError,
    WorkspaceWriteTypeError,
)
from agents.sandbox.files import EntryKind
from agents.sandbox.session import Dependencies
from agents.sandbox.session import SandboxSession as OpenAISandboxSession
from agents.sandbox.snapshot import NoopSnapshot, SnapshotBase

from mem_sandbox.core import Revision
from mem_sandbox.integrations.openai_agents import (
    InMemorySandboxClient,
    InMemorySandboxClientOptions,
    InMemorySandboxSession,
    InMemorySandboxSessionState,
)
from mem_sandbox.service import SandboxHandle
from mem_sandbox.session import (
    ReadBytesRequest,
    SessionExecuteRequest,
    SessionOperationCancelled,
    SessionOperationTimeout,
)
from mem_sandbox.session import SandboxSession as CoreSandboxSession
from mem_sandbox.workspace import (
    ContentHash,
    SnapshotCorruptError,
    WorkspaceArchiveData,
    WorkspaceLimits,
)

from .support import RecordingService, create_service_bundle


class MemorySnapshot(SnapshotBase):
    type: str = "mem-sandbox-adapter-test"
    _payloads: ClassVar[dict[str, bytes]] = {}

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
    await client.delete(source)

    resumed = await client.resume(restored_state)
    resumed_inner = _inner(resumed)
    await resumed.start()

    assert resumed_inner.state.sandbox_handle != original_handle
    assert (await resumed.read(Path("/workspace/restored.bin"))).read() == b"restored"

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
        owner_id="openai-agents",
        workspace_limits=WorkspaceLimits(),
        max_stream_bytes=1024,
        snapshot=NoopSnapshot(id="snapshot"),
        manifest=Manifest(),
    )

    payload = state.model_dump(mode="json")

    assert payload["type"] == "mem_sandbox"
    assert payload["sandbox_handle"] == "11111111-1111-1111-1111-111111111111"
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
