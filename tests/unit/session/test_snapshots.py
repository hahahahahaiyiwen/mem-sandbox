from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from mem_sandbox.command_executor import CommandEnvironment, EnvironmentValue
from mem_sandbox.core import Revision, SessionId, SnapshotId
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SandboxSnapshot,
    SessionSnapshotState,
    SnapshotCorrupt,
    SnapshotIdentifierConflict,
    SnapshotIncompatible,
    SnapshotMetadata,
    SnapshotNotFound,
    SnapshotRef,
    SnapshotTooLarge,
)
from mem_sandbox.workspace import AnyCurrentState, MemoryWorkspace, WorkspaceWriteRequest

SESSION_ID = SessionId(UUID("12345678-1234-5678-1234-567812345678"))
SNAPSHOT_ID = SnapshotId(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))


async def _state() -> SessionSnapshotState:
    workspace = MemoryWorkspace()
    await workspace.write(
        WorkspaceWriteRequest(
            workspace.resolve_path("/workspace/file.txt"),
            b"content",
            AnyCurrentState(),
        )
    )
    return SessionSnapshotState(
        workspace=await workspace.export(),
        cwd=workspace.resolve_path("/workspace"),
        approved_environment=CommandEnvironment((EnvironmentValue("NAME", "value"),)),
    )


@pytest.mark.asyncio
async def test_session_snapshot_codec_is_deterministic_and_verifies_hash() -> None:
    state = await _state()
    codec = JsonSessionSnapshotCodec()

    first = codec.encode(state)
    second = codec.encode(state)

    assert first == second
    assert first.format_name == "json"
    snapshot = SandboxSnapshot(
        snapshot_id=SNAPSHOT_ID,
        schema_version=1,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        source_session_id=SESSION_ID,
        workspace_revision=state.workspace.workspace_revision,
        content_hash=first.content_hash,
        payload=first.payload,
        metadata=SnapshotMetadata("json", len(first.payload), True),
    )
    assert codec.decode(snapshot) == state

    corrupt = SandboxSnapshot(
        snapshot_id=snapshot.snapshot_id,
        schema_version=snapshot.schema_version,
        created_at=snapshot.created_at,
        source_session_id=snapshot.source_session_id,
        workspace_revision=snapshot.workspace_revision,
        content_hash=snapshot.content_hash,
        payload=snapshot.payload[:-1] + bytes([snapshot.payload[-1] ^ 1]),
        metadata=snapshot.metadata,
    )
    with pytest.raises(SnapshotCorrupt):
        codec.decode(corrupt)


@pytest.mark.asyncio
async def test_session_snapshot_codec_enforces_complete_payload_limit() -> None:
    state = await _state()
    payload = JsonSessionSnapshotCodec().encode(state)

    assert JsonSessionSnapshotCodec(max_payload_bytes=len(payload.payload)).encode(state)
    with pytest.raises(SnapshotTooLarge):
        JsonSessionSnapshotCodec(max_payload_bytes=len(payload.payload) - 1).encode(state)


@pytest.mark.asyncio
async def test_in_memory_store_preserves_identity_and_rejects_duplicates() -> None:
    state = await _state()
    payload = JsonSessionSnapshotCodec().encode(state)
    snapshot = SandboxSnapshot(
        snapshot_id=SNAPSHOT_ID,
        schema_version=1,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        source_session_id=SESSION_ID,
        workspace_revision=Revision(1),
        content_hash=payload.content_hash,
        payload=payload.payload,
        metadata=SnapshotMetadata("json", len(payload.payload), True),
    )
    store = InMemorySnapshotStore()
    assert store.process_local is True

    snapshot_ref = await store.save(snapshot)

    assert snapshot_ref == SnapshotRef(SNAPSHOT_ID)
    assert await store.load(snapshot_ref) == snapshot
    assert (await store.load(snapshot_ref)).source_session_id == SESSION_ID
    with pytest.raises(SnapshotIdentifierConflict):
        await store.save(snapshot)
    await store.delete(snapshot_ref)
    await store.delete(snapshot_ref)
    with pytest.raises(SnapshotNotFound):
        await store.load(snapshot_ref)


@pytest.mark.asyncio
async def test_snapshot_compatibility_and_outer_identity_do_not_change_state_hash() -> None:
    state = await _state()
    codec = JsonSessionSnapshotCodec()
    payload = codec.encode(state)
    other_id = SnapshotId(UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
    first = SandboxSnapshot(
        SNAPSHOT_ID,
        1,
        datetime(2026, 9, 1, tzinfo=UTC),
        SESSION_ID,
        state.workspace.workspace_revision,
        payload.content_hash,
        payload.payload,
        SnapshotMetadata("json", len(payload.payload), True),
    )
    second = SandboxSnapshot(
        other_id,
        1,
        datetime(2026, 9, 2, tzinfo=UTC),
        SESSION_ID,
        state.workspace.workspace_revision,
        payload.content_hash,
        payload.payload,
        SnapshotMetadata("json", len(payload.payload), True),
    )

    assert first.content_hash == second.content_hash
    assert codec.decode(first) == codec.decode(second)
    with pytest.raises(SnapshotIncompatible):
        codec.encode(
            SessionSnapshotState(
                state.workspace,
                state.cwd,
                state.approved_environment,
                schema_version=2,
            )
        )
    with pytest.raises(SnapshotIncompatible):
        codec.decode(replace(first, metadata=SnapshotMetadata("other", len(payload.payload), True)))
