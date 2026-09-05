from __future__ import annotations

import asyncio
import io
import tarfile
from dataclasses import replace
from typing import cast

import pytest

from mem_sandbox.core.identifiers import Revision
from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    MakeDirectoryRequest,
    MemoryWorkspace,
    NodeKind,
    PathNotFoundError,
    PortableWorkspaceArchiveCodec,
    PreparedRestoreInvalid,
    SandboxPath,
    SnapshotCorruptError,
    SnapshotIncompatibleError,
    WorkspaceLimits,
    WorkspaceWriteRequest,
)


@pytest.mark.asyncio
async def test_portable_archive_restore_is_prepared_before_atomic_publication() -> None:
    source = MemoryWorkspace()
    src = source.resolve_path("/workspace/src")
    data = src.join("data.bin")
    empty = source.resolve_path("/workspace/empty")
    await source.mkdir(MakeDirectoryRequest(src))
    await source.mkdir(MakeDirectoryRequest(empty))
    await source.write(WorkspaceWriteRequest(data, b"\x00\xffarchive", AnyCurrentState()))
    archive = await source.export_portable_archive()
    expected_stats = await source.stats()

    assert archive.format_version == 1
    assert archive.workspace_revision == expected_stats.revision
    assert archive.root_hash == expected_stats.root_hash

    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    candidate = await target.prepare_archive_restore(
        archive,
        required_directory=SandboxPath.root(),
    )

    assert (await target.read_bytes(live)).content == b"live"

    await target.commit_restore(candidate)

    assert await target.stats() == expected_stats
    assert (await target.read_bytes(data)).content == b"\x00\xffarchive"
    assert (await target.list(empty)) == ()
    with pytest.raises(PathNotFoundError):
        await target.stat(live)


@pytest.mark.asyncio
async def test_equivalent_trees_have_identical_tar_bytes_at_different_revisions() -> None:
    path = SandboxPath.resolve("/workspace/file.txt")
    first = MemoryWorkspace()
    second = MemoryWorkspace()
    await first.write(WorkspaceWriteRequest(path, b"same", AnyCurrentState()))
    await second.write(WorkspaceWriteRequest(path, b"older", AnyCurrentState()))
    await second.write(WorkspaceWriteRequest(path, b"same", AnyCurrentState()))

    first_archive = await first.export_portable_archive()
    second_archive = await second.export_portable_archive()

    assert first_archive.encoded == second_archive.encoded
    assert first_archive.root_hash == second_archive.root_hash
    assert first_archive.workspace_revision != second_archive.workspace_revision


@pytest.mark.asyncio
async def test_invalid_portable_archive_leaves_complete_live_state_unchanged() -> None:
    source = MemoryWorkspace()
    archive = await source.export_portable_archive()
    invalid = replace(archive, encoded=_tar_bytes("../escape.txt", b"escape"))

    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    before = await target.stats()

    with pytest.raises(SnapshotCorruptError):
        await target.prepare_archive_restore(
            invalid,
            required_directory=SandboxPath.root(),
        )

    assert await target.stats() == before
    assert (await target.read_bytes(live)).content == b"live"


@pytest.mark.asyncio
async def test_archive_metadata_mismatch_does_not_publish_candidate() -> None:
    source = MemoryWorkspace()
    await source.write(
        WorkspaceWriteRequest(
            source.resolve_path("/workspace/file.txt"),
            b"archive",
            AnyCurrentState(),
        )
    )
    archive = await source.export_portable_archive()
    mismatched = replace(archive, root_hash=ContentHash.from_bytes(b"different tree"))

    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    before = await target.stats()

    with pytest.raises(SnapshotCorruptError):
        await target.prepare_archive_restore(
            mismatched,
            required_directory=SandboxPath.root(),
        )

    assert await target.stats() == before
    assert (await target.read_bytes(live)).content == b"live"


@pytest.mark.asyncio
async def test_unsupported_archive_format_version_does_not_publish_candidate() -> None:
    source = MemoryWorkspace()
    archive = replace(await source.export_portable_archive(), format_version=999)
    target = MemoryWorkspace()
    before = await target.stats()

    with pytest.raises(SnapshotIncompatibleError):
        await target.prepare_archive_restore(
            archive,
            required_directory=SandboxPath.root(),
        )

    assert await target.stats() == before


@pytest.mark.asyncio
async def test_failed_portable_archive_required_directory_check_does_not_publish_candidate() -> (
    None
):
    source = MemoryWorkspace()
    await source.write(
        WorkspaceWriteRequest(
            source.resolve_path("/workspace/file.txt"),
            b"archive",
            AnyCurrentState(),
        )
    )
    archive = await source.export_portable_archive()

    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    before = await target.stats()

    with pytest.raises(PathNotFoundError):
        await target.prepare_archive_restore(
            archive,
            required_directory=target.resolve_path("/workspace/missing"),
        )

    assert await target.stats() == before
    assert (await target.read_bytes(live)).content == b"live"


@pytest.mark.asyncio
async def test_cancelled_archive_commit_waiter_never_publishes_candidate() -> None:
    source = MemoryWorkspace()
    await source.write(
        WorkspaceWriteRequest(
            source.resolve_path("/workspace/archive.txt"),
            b"archive",
            AnyCurrentState(),
        )
    )
    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    before = await target.stats()
    candidate = await target.prepare_archive_restore(
        await source.export_portable_archive(),
        required_directory=SandboxPath.root(),
    )
    lock = cast(asyncio.Lock, object.__getattribute__(target, "_state_lock"))
    await lock.acquire()
    task = asyncio.create_task(target.commit_restore(candidate))
    await asyncio.sleep(0)
    task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        lock.release()

    assert await target.stats() == before
    assert (await target.read_bytes(live)).content == b"live"


@pytest.mark.asyncio
async def test_timed_out_archive_commit_waiter_never_publishes_candidate() -> None:
    source = MemoryWorkspace()
    await source.write(
        WorkspaceWriteRequest(
            source.resolve_path("/workspace/archive.txt"),
            b"archive",
            AnyCurrentState(),
        )
    )
    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    before = await target.stats()
    candidate = await target.prepare_archive_restore(
        await source.export_portable_archive(),
        required_directory=SandboxPath.root(),
    )
    lock = cast(asyncio.Lock, object.__getattribute__(target, "_state_lock"))
    await lock.acquire()

    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await target.commit_restore(candidate)
    finally:
        lock.release()

    assert await target.stats() == before
    assert (await target.read_bytes(live)).content == b"live"


@pytest.mark.asyncio
async def test_expired_uncontended_archive_commit_never_publishes_candidate() -> None:
    source = MemoryWorkspace()
    archived = source.resolve_path("/workspace/archive.txt")
    await source.write(WorkspaceWriteRequest(archived, b"archive", AnyCurrentState()))
    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    before = await target.stats()
    candidate = await target.prepare_archive_restore(
        await source.export_portable_archive(),
        required_directory=SandboxPath.root(),
    )

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0):
            await target.commit_restore(candidate)

    assert await target.stats() == before
    assert (await target.read_bytes(live)).content == b"live"


@pytest.mark.asyncio
async def test_deep_portable_archive_remains_usable_after_atomic_restore() -> None:
    directory_count = 1_100
    deep_name = "/".join((*("a",) * directory_count, "file"))
    encoded = _tar_bytes(deep_name, b"content")
    decoded = PortableWorkspaceArchiveCodec().decode(encoded, WorkspaceLimits())
    metadata = await MemoryWorkspace().export_portable_archive()
    archive = replace(
        metadata,
        encoded=encoded,
        root_hash=decoded.tree_stats.root_hash,
    )
    target = MemoryWorkspace()
    leaf = SandboxPath.resolve(f"/workspace/{deep_name}")

    candidate = await target.prepare_archive_restore(
        archive,
        required_directory=SandboxPath.root(),
    )
    await target.commit_restore(candidate)

    assert (await target.stat(SandboxPath.resolve("/workspace/a"))).kind is NodeKind.DIRECTORY
    assert (await target.read_bytes(leaf)).content == b"content"
    await target.write(WorkspaceWriteRequest(leaf, b"updated", AnyCurrentState()))
    exported = await target.export_portable_archive()
    assert exported.root_hash == (await target.stats()).root_hash
    assert (await target.read_bytes(leaf)).content == b"updated"


@pytest.mark.asyncio
async def test_archive_restore_honors_configured_path_limits_above_defaults() -> None:
    long_segment = "x" * 300
    relative_path = "/".join((*((long_segment,) * 14), "file"))
    absolute_path = f"{SandboxPath.ROOT}/{relative_path}"
    limits = WorkspaceLimits(
        max_path_bytes=len(absolute_path.encode("utf-8")),
        max_segment_bytes=len(long_segment.encode("utf-8")),
    )
    encoded = _tar_bytes(relative_path, b"content")
    decoded = PortableWorkspaceArchiveCodec().decode(encoded, limits)
    metadata = await MemoryWorkspace(limits).export_portable_archive()
    archive = replace(
        metadata,
        encoded=encoded,
        root_hash=decoded.tree_stats.root_hash,
    )
    target = MemoryWorkspace(limits)
    leaf = target.resolve_path(absolute_path)
    parent = leaf.parent

    candidate = await target.prepare_archive_restore(
        archive,
        required_directory=SandboxPath.root(),
    )
    await target.commit_restore(candidate)

    assert (await target.list(parent))[0].path == leaf
    await target.write(WorkspaceWriteRequest(leaf, b"updated", AnyCurrentState()))
    assert (await target.export_portable_archive()).root_hash == (await target.stats()).root_hash


@pytest.mark.asyncio
async def test_prepared_restore_state_cannot_alias_or_mutate_live_state() -> None:
    source = MemoryWorkspace()
    archived = source.resolve_path("/workspace/archive.txt")
    await source.write(WorkspaceWriteRequest(archived, b"archive", AnyCurrentState()))
    archive = await source.export_portable_archive()

    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    before = await target.stats()
    candidate = await target.prepare_archive_restore(
        archive,
        required_directory=SandboxPath.root(),
    )
    _candidate_root_children(target, candidate).clear()

    with pytest.raises(PreparedRestoreInvalid):
        await target.commit_restore(candidate)

    assert await target.stats() == before
    assert (await target.read_bytes(live)).content == b"live"

    candidate = await target.prepare_archive_restore(
        archive,
        required_directory=SandboxPath.root(),
    )
    leaked_children = _candidate_root_children(target, candidate)
    await target.commit_restore(candidate)
    leaked_children.clear()

    assert (await target.read_bytes(archived)).content == b"archive"
    assert (await target.stats()).root_hash == archive.root_hash
    with pytest.raises(PreparedRestoreInvalid):
        await target.commit_restore(candidate)


@pytest.mark.asyncio
async def test_prepared_restore_rejects_a_cyclic_candidate_graph() -> None:
    workspace = MemoryWorkspace()
    candidate = await workspace.prepare_archive_restore(
        await workspace.export_portable_archive(),
        required_directory=SandboxPath.root(),
    )
    root = _candidate_root(workspace, candidate)
    children = cast(dict[str, object], object.__getattribute__(root, "children"))
    children["loop"] = root

    with pytest.raises(PreparedRestoreInvalid):
        await workspace.commit_restore(candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revision",
    (
        "bad",
        Revision(999),
    ),
)
async def test_prepared_restore_rejects_candidate_revision_tampering(
    revision: object,
) -> None:
    workspace = MemoryWorkspace()
    candidate = await workspace.prepare_archive_restore(
        await workspace.export_portable_archive(),
        required_directory=SandboxPath.root(),
    )
    state = _candidate_state(workspace, candidate)
    stats = object.__getattribute__(state, "stats")
    object.__setattr__(stats, "revision", revision)

    with pytest.raises(PreparedRestoreInvalid):
        await workspace.commit_restore(candidate)


@pytest.mark.asyncio
async def test_copied_prepared_restore_candidate_cannot_be_replayed() -> None:
    source = MemoryWorkspace()
    archived = source.resolve_path("/workspace/archive.txt")
    await source.write(WorkspaceWriteRequest(archived, b"archive", AnyCurrentState()))
    target = MemoryWorkspace()
    candidate = await target.prepare_archive_restore(
        await source.export_portable_archive(),
        required_directory=SandboxPath.root(),
    )
    copied_candidate = replace(candidate)

    await target.commit_restore(candidate)
    later = target.resolve_path("/workspace/later.txt")
    await target.write(WorkspaceWriteRequest(later, b"later", AnyCurrentState()))

    with pytest.raises(PreparedRestoreInvalid):
        await target.commit_restore(copied_candidate)
    with pytest.raises(PreparedRestoreInvalid):
        await target.commit_restore(replace(candidate, _capability=object()))

    assert (await target.read_bytes(later)).content == b"later"


@pytest.mark.asyncio
async def test_commit_detaches_revision_before_final_cancellation_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = MemoryWorkspace()
    candidate = await workspace.prepare_archive_restore(
        await workspace.export_portable_archive(),
        required_directory=SandboxPath.root(),
    )
    expected_stats = _candidate_expected_stats(workspace, candidate)
    exposed_revision = object.__getattribute__(expected_stats, "revision")
    original_sleep = asyncio.sleep
    checkpoint_count = 0

    async def mutate_at_final_checkpoint(delay: float) -> None:
        nonlocal checkpoint_count
        checkpoint_count += 1
        if checkpoint_count == 2:
            object.__setattr__(exposed_revision, "value", -1)
        await original_sleep(delay)

    monkeypatch.setattr("mem_sandbox.workspace.memory.asyncio.sleep", mutate_at_final_checkpoint)

    await workspace.commit_restore(candidate)

    assert (await workspace.stats()).revision == Revision.initial()


@pytest.mark.asyncio
async def test_prepared_restore_rejects_a_file_root_as_invalid_candidate() -> None:
    source = MemoryWorkspace()
    archived = source.resolve_path("/workspace/archive.txt")
    await source.write(WorkspaceWriteRequest(archived, b"archive", AnyCurrentState()))
    target = MemoryWorkspace()
    candidate = await target.prepare_archive_restore(
        await source.export_portable_archive(),
        required_directory=SandboxPath.root(),
    )
    state = _candidate_state(target, candidate)
    root = object.__getattribute__(state, "root")
    children = cast(dict[str, object], object.__getattribute__(root, "children"))
    object.__setattr__(state, "root", children["archive.txt"])

    with pytest.raises(PreparedRestoreInvalid):
        await target.commit_restore(candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path_value",
    (
        "/workspace/a\\b",
        "/workspace/dir/C:",
    ),
)
async def test_portable_export_rejects_nonportable_live_workspace_paths(
    path_value: str,
) -> None:
    workspace = MemoryWorkspace()
    path = workspace.resolve_path(path_value)
    await workspace.write(
        WorkspaceWriteRequest(
            path,
            b"content",
            AnyCurrentState(),
            create_parents=True,
        )
    )

    with pytest.raises(SnapshotCorruptError):
        await workspace.export_portable_archive()


def _candidate_payload(workspace: MemoryWorkspace, candidate: object) -> object:
    registry = object.__getattribute__(workspace, "_restore_candidates")
    capability = object.__getattribute__(candidate, "_capability")
    return registry[capability]


def _candidate_state(workspace: MemoryWorkspace, candidate: object) -> object:
    return object.__getattribute__(_candidate_payload(workspace, candidate), "state")


def _candidate_expected_stats(workspace: MemoryWorkspace, candidate: object) -> object:
    return object.__getattribute__(_candidate_payload(workspace, candidate), "expected_stats")


def _candidate_root(workspace: MemoryWorkspace, candidate: object) -> object:
    return object.__getattribute__(_candidate_state(workspace, candidate), "root")


def _candidate_root_children(
    workspace: MemoryWorkspace,
    candidate: object,
) -> dict[str, object]:
    return cast(
        dict[str, object],
        object.__getattribute__(_candidate_root(workspace, candidate), "children"),
    )


def _tar_bytes(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer,
        mode="w",
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="strict",
    ) as archive:
        info = tarfile.TarInfo(name)
        info.type = tarfile.REGTYPE
        info.mode = 0o644
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        info.size = len(content)
        info.pax_headers = {}
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()
