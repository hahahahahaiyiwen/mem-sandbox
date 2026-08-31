from __future__ import annotations

import hashlib
import json
from typing import cast

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    MakeDirectoryRequest,
    MemoryWorkspace,
    PathNotFoundError,
    SandboxPath,
    SnapshotCorruptError,
    SnapshotIncompatibleError,
    SnapshotTooLargeError,
    WorkspaceLimits,
    WorkspaceSnapshotData,
    WorkspaceWriteRequest,
)


@pytest.mark.asyncio
async def test_identical_states_encode_to_identical_snapshot_bytes() -> None:
    first = MemoryWorkspace()
    second = MemoryWorkspace()
    paths = (
        SandboxPath.resolve("/workspace/a.txt"),
        SandboxPath.resolve("/workspace/b.txt"),
    )

    for path in paths:
        await first.write(WorkspaceWriteRequest(path, path.name.encode(), AnyCurrentState()))
    for path in reversed(paths):
        await second.write(WorkspaceWriteRequest(path, path.name.encode(), AnyCurrentState()))

    first_snapshot = await first.export()
    second_snapshot = await second.export()

    assert first_snapshot.encoded == second_snapshot.encoded
    assert first_snapshot.integrity_hash == second_snapshot.integrity_hash
    assert first_snapshot.root_hash == second_snapshot.root_hash


@pytest.mark.asyncio
async def test_binary_snapshot_round_trip_restores_revision_and_independent_nodes() -> None:
    source = MemoryWorkspace()
    directory = SandboxPath.resolve("/workspace/src")
    binary = directory.join("data.bin")
    await source.mkdir(MakeDirectoryRequest(directory))
    await source.write(WorkspaceWriteRequest(binary, b"\x00\xff", AnyCurrentState()))
    expected_stats = await source.stats()
    snapshot = await source.export()

    restored = MemoryWorkspace()
    await restored.restore(snapshot)

    assert await restored.stats() == expected_stats
    assert (await restored.read_bytes(binary)).content == b"\x00\xff"

    await source.write(WorkspaceWriteRequest(binary, b"source", AnyCurrentState()))
    assert (await restored.read_bytes(binary)).content == b"\x00\xff"

    await restored.write(WorkspaceWriteRequest(binary, b"restored", AnyCurrentState()))
    assert (await source.read_bytes(binary)).content == b"source"


@pytest.mark.asyncio
async def test_restore_replaces_live_state_atomically_with_snapshot_revision() -> None:
    workspace = MemoryWorkspace()
    original = SandboxPath.resolve("/workspace/original.txt")
    later = SandboxPath.resolve("/workspace/later.txt")
    await workspace.write(WorkspaceWriteRequest(original, b"original", AnyCurrentState()))
    snapshot = await workspace.export()
    snapshot_stats = await workspace.stats()
    await workspace.write(WorkspaceWriteRequest(later, b"later", AnyCurrentState()))

    await workspace.restore(snapshot)

    assert await workspace.stats() == snapshot_stats
    assert (await workspace.read_text(original)).content == "original"
    with pytest.raises(PathNotFoundError):
        await workspace.stat(later)


@pytest.mark.asyncio
async def test_corrupt_snapshot_does_not_change_live_workspace() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/file.txt")
    await workspace.write(WorkspaceWriteRequest(path, b"content", AnyCurrentState()))
    snapshot = await workspace.export()
    before = await workspace.stats()
    corrupt = WorkspaceSnapshotData(
        encoded=snapshot.encoded.replace(b"Y29udGVudA==", b"Y29ycnVwdA=="),
        schema_version=snapshot.schema_version,
        integrity_hash=snapshot.integrity_hash,
        workspace_revision=snapshot.workspace_revision,
        root_hash=snapshot.root_hash,
    )

    with pytest.raises(SnapshotCorruptError):
        await workspace.restore(corrupt)

    assert await workspace.stats() == before
    assert (await workspace.read_text(path)).content == "content"


@pytest.mark.asyncio
async def test_snapshot_decoder_rejects_unsupported_version_with_valid_integrity() -> None:
    workspace = MemoryWorkspace()
    snapshot = await workspace.export()
    envelope = json.loads(snapshot.encoded)
    payload = envelope["payload"]
    payload["schema_version"] = 999
    incompatible = _reencode(snapshot, envelope)

    with pytest.raises(SnapshotIncompatibleError):
        await workspace.restore(incompatible)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoded",
    (
        b"not json",
        b'{"payload":{},"payload":{},"integrity":{}}',
        b'{"integrity":{"algorithm":"sha256","digest":"bad"},"payload":{}}',
    ),
)
async def test_snapshot_decoder_rejects_malformed_json_and_duplicate_fields(
    encoded: bytes,
) -> None:
    workspace = MemoryWorkspace()
    malformed = WorkspaceSnapshotData(
        encoded=encoded,
        schema_version=1,
        integrity_hash=ContentHash.from_bytes(b""),
        workspace_revision=(await workspace.stats()).revision,
        root_hash=(await workspace.stats()).root_hash,
    )

    with pytest.raises(SnapshotCorruptError):
        await workspace.restore(malformed)


@pytest.mark.asyncio
async def test_snapshot_size_limit_is_checked_on_export_and_restore() -> None:
    tiny_limits = WorkspaceLimits(
        max_file_bytes=100,
        max_total_bytes=100,
        max_snapshot_bytes=10,
    )
    tiny = MemoryWorkspace(tiny_limits)

    with pytest.raises(SnapshotTooLargeError):
        await tiny.export()

    normal = MemoryWorkspace()
    snapshot = await normal.export()

    with pytest.raises(SnapshotTooLargeError):
        await tiny.restore(snapshot)


@pytest.mark.asyncio
async def test_snapshot_decode_stops_when_cumulative_file_bytes_exceed_limit() -> None:
    source = MemoryWorkspace()
    await source.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/a"),
            b"123",
            AnyCurrentState(),
        )
    )
    await source.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/b"),
            b"456",
            AnyCurrentState(),
        )
    )
    snapshot = await source.export()
    target = MemoryWorkspace(WorkspaceLimits(max_file_bytes=4, max_total_bytes=4))

    with pytest.raises(SnapshotTooLargeError, match="files"):
        await target.restore(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("kind", "symlink", "node kind"),
        ("content_base64", "%%%", "base64"),
    ),
)
async def test_snapshot_rejects_unsupported_nodes_and_malformed_binary_content(
    field: str,
    value: str,
    message: str,
) -> None:
    source = MemoryWorkspace()
    await source.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/file"),
            b"content",
            AnyCurrentState(),
        )
    )
    snapshot = await source.export()
    envelope = json.loads(snapshot.encoded)
    envelope["payload"]["entries"][0][field] = value
    malformed = _reencode(snapshot, envelope)

    with pytest.raises(SnapshotCorruptError, match=message):
        await source.restore(malformed)


def _reencode(
    original: WorkspaceSnapshotData,
    envelope: dict[str, object],
) -> WorkspaceSnapshotData:
    payload_value = envelope["payload"]
    assert isinstance(payload_value, dict)
    payload = cast(dict[str, object], payload_value)
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    digest = hashlib.sha256(payload_bytes).hexdigest()
    integrity_value = envelope["integrity"]
    assert isinstance(integrity_value, dict)
    integrity = cast(dict[str, object], integrity_value)
    integrity["digest"] = digest
    encoded = json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    schema_version = payload["schema_version"]
    assert isinstance(schema_version, int)
    return WorkspaceSnapshotData(
        encoded=encoded,
        schema_version=schema_version,
        integrity_hash=ContentHash(digest),
        workspace_revision=original.workspace_revision,
        root_hash=original.root_hash,
    )
