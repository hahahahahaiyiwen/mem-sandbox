from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.property.async_machine import AsyncRunner
from tests.property.strategies import environment_maps, workspace_file_maps

from mem_sandbox.command_executor import CommandEnvironment, EnvironmentValue
from mem_sandbox.core import SessionId, SnapshotId
from mem_sandbox.snapshots import (
    JsonSessionSnapshotCodec,
    SandboxSnapshot,
    SessionSnapshotState,
    SnapshotCorrupt,
    SnapshotIncompatible,
    SnapshotMetadata,
    SnapshotTooLarge,
)
from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    MemoryWorkspace,
    SandboxPath,
    WorkspaceWriteRequest,
)

SESSION_ID = SessionId(UUID("12345678-1234-5678-1234-567812345678"))
CREATED_AT = datetime(2026, 9, 3, tzinfo=UTC)


async def _state(
    files: dict[str, bytes],
    environment: dict[str, str],
    *,
    reverse_environment: bool = False,
) -> SessionSnapshotState:
    workspace = MemoryWorkspace()
    for name, content in files.items():
        await workspace.write(
            WorkspaceWriteRequest(
                SandboxPath.resolve(f"/workspace/{name}"),
                content,
                AnyCurrentState(),
            )
        )
    items = tuple(environment.items())
    items = tuple(sorted(items))
    if reverse_environment:
        items = tuple(reversed(items))
    return SessionSnapshotState(
        workspace=await workspace.export(),
        cwd=SandboxPath.root(),
        approved_environment=CommandEnvironment(
            tuple(EnvironmentValue(name, value) for name, value in items)
        ),
    )


def _snapshot(
    state: SessionSnapshotState,
    *,
    snapshot_id: int = 1,
    created_offset_seconds: int = 0,
) -> SandboxSnapshot:
    payload = JsonSessionSnapshotCodec().encode(state)
    created_at = CREATED_AT + timedelta(seconds=created_offset_seconds)
    return SandboxSnapshot(
        snapshot_id=SnapshotId(UUID(int=snapshot_id)),
        schema_version=payload.schema_version,
        created_at=created_at,
        expires_at=created_at + timedelta(days=1),
        source_session_id=SESSION_ID,
        workspace_revision=payload.workspace_revision,
        content_hash=payload.content_hash,
        payload=payload.payload,
        metadata=SnapshotMetadata(payload.format_name, len(payload.payload), True),
        created_by=None,
    )


@given(
    files=workspace_file_maps(),
    environment=environment_maps(),
    first_id=st.integers(min_value=1, max_value=2**128 - 1),
    second_id=st.integers(min_value=1, max_value=2**128 - 1),
    created_offset=st.integers(min_value=0, max_value=86_400),
)
def test_session_codec_round_trip_is_deterministic_and_ignores_outer_identity(
    files: dict[str, bytes],
    environment: dict[str, str],
    first_id: int,
    second_id: int,
    created_offset: int,
) -> None:
    async def exercise() -> None:
        state = await _state(files, environment)
        reordered = await _state(files, environment, reverse_environment=True)
        codec = JsonSessionSnapshotCodec()

        first_payload = codec.encode(state)
        assert codec.encode(state) == first_payload
        assert codec.encode(reordered) == first_payload

        first = _snapshot(state, snapshot_id=first_id)
        second = _snapshot(
            state,
            snapshot_id=second_id,
            created_offset_seconds=created_offset,
        )
        assert first.content_hash == second.content_hash
        decoded = codec.decode(first)
        assert decoded == state
        assert codec.encode(decoded).payload == first.payload
        assert codec.decode(second) == state

    with AsyncRunner() as runner:
        runner.run(exercise())


def test_session_codec_rejects_noncanonical_environment_order() -> None:
    async def exercise() -> None:
        state = await _state({}, {"A": "first", "B": "second"})
        snapshot = _snapshot(state)
        value = json.loads(snapshot.payload)
        assert isinstance(value, dict)
        payload_object = cast(dict[str, object], value)
        environment = payload_object["approved_environment"]
        assert isinstance(environment, list)
        environment_entries = cast(list[object], environment)
        payload_object["approved_environment"] = list(reversed(environment_entries))
        payload = json.dumps(
            payload_object,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        noncanonical = replace(
            snapshot,
            payload=payload,
            content_hash=ContentHash.from_bytes(payload),
            metadata=SnapshotMetadata("json", len(payload), True),
        )

        with pytest.raises(SnapshotCorrupt, match="sorted"):
            JsonSessionSnapshotCodec().decode(noncanonical)

    with AsyncRunner() as runner:
        runner.run(exercise())


def test_session_codec_rejects_noncanonical_workspace_base64() -> None:
    async def exercise() -> None:
        state = await _state({"file.txt": b"content"}, {})
        snapshot = _snapshot(state)
        value = json.loads(snapshot.payload)
        assert isinstance(value, dict)
        payload_object = cast(dict[str, object], value)
        workspace = payload_object["workspace"]
        assert isinstance(workspace, dict)
        workspace_object = cast(dict[str, object], workspace)
        encoded = workspace_object["encoded_base64"]
        assert isinstance(encoded, str)
        padding = len(encoded) - len(encoded.rstrip("="))
        assert padding in (1, 2)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        final_index = len(encoded) - padding - 1
        canonical_value = alphabet.index(encoded[final_index])
        pad_mask = 0b11 if padding == 1 else 0b1111
        replacement = alphabet[canonical_value | 1 & pad_mask]
        workspace_object["encoded_base64"] = (
            encoded[:final_index] + replacement + encoded[final_index + 1 :]
        )
        payload = json.dumps(
            payload_object,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        noncanonical = replace(
            snapshot,
            payload=payload,
            content_hash=ContentHash.from_bytes(payload),
            metadata=SnapshotMetadata("json", len(payload), True),
        )

        with pytest.raises(SnapshotCorrupt, match="canonical base64"):
            JsonSessionSnapshotCodec().decode(noncanonical)

    with AsyncRunner() as runner:
        runner.run(exercise())


@given(
    files=workspace_file_maps(),
    environment=environment_maps(),
    data=st.data(),
)
def test_session_codec_rejects_one_byte_corruption(
    files: dict[str, bytes],
    environment: dict[str, str],
    data: st.DataObject,
) -> None:
    async def exercise() -> None:
        state = await _state(files, environment)
        snapshot = _snapshot(state)
        index = data.draw(
            st.integers(min_value=0, max_value=len(snapshot.payload) - 1),
            label="corrupt-byte-index",
        )
        corrupt_payload = (
            snapshot.payload[:index]
            + bytes((snapshot.payload[index] ^ 1,))
            + snapshot.payload[index + 1 :]
        )
        with pytest.raises(SnapshotCorrupt):
            JsonSessionSnapshotCodec().decode(replace(snapshot, payload=corrupt_payload))

    with AsyncRunner() as runner:
        runner.run(exercise())


@given(
    files=workspace_file_maps(),
    environment=environment_maps(),
    invalid_part=st.sampled_from(("cwd", "environment")),
)
def test_session_codec_rejects_invalid_embedded_state_with_valid_outer_hash(
    files: dict[str, bytes],
    environment: dict[str, str],
    invalid_part: str,
) -> None:
    async def exercise() -> None:
        state = await _state(files, environment)
        snapshot = _snapshot(state)
        value = json.loads(snapshot.payload)
        assert isinstance(value, dict)
        payload_object = cast(dict[str, object], value)
        if invalid_part == "cwd":
            payload_object["cwd"] = "/workspace/../escape"
        else:
            payload_object["approved_environment"] = [
                {"name": "DUPLICATE", "value": "first"},
                {"name": "DUPLICATE", "value": "second"},
            ]
        payload = json.dumps(
            payload_object,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        valid_outer = replace(
            snapshot,
            payload=payload,
            content_hash=ContentHash.from_bytes(payload),
            metadata=SnapshotMetadata("json", len(payload), True),
        )
        with pytest.raises(SnapshotCorrupt):
            JsonSessionSnapshotCodec().decode(valid_outer)

    with AsyncRunner() as runner:
        runner.run(exercise())


@given(files=workspace_file_maps(), environment=environment_maps())
def test_session_codec_exact_size_boundary_and_incompatible_version(
    files: dict[str, bytes],
    environment: dict[str, str],
) -> None:
    async def exercise() -> None:
        state = await _state(files, environment)
        payload = JsonSessionSnapshotCodec().encode(state)

        assert JsonSessionSnapshotCodec(len(payload.payload)).encode(state) == payload
        with pytest.raises(SnapshotTooLarge):
            JsonSessionSnapshotCodec(len(payload.payload) - 1).encode(state)
        with pytest.raises(SnapshotIncompatible):
            JsonSessionSnapshotCodec().encode(replace(state, schema_version=2))

    with AsyncRunner() as runner:
        runner.run(exercise())
