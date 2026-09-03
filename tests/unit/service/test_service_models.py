from dataclasses import FrozenInstanceError
from typing import cast
from uuid import UUID

import pytest

from mem_sandbox.core import SessionId
from mem_sandbox.service import (
    CreateSandboxRequest,
    FactorySnapshotStore,
    OwnerId,
    SandboxHandle,
    SandboxOptions,
    SessionFactoryRequest,
    WorkspaceSeedFile,
)
from mem_sandbox.snapshots import SessionSnapshotState


def test_owner_id_preserves_exact_text_and_enforces_utf8_bound() -> None:
    exact = "é" * 128

    assert OwnerId(exact).value == exact
    with pytest.raises(ValueError):
        OwnerId("")
    with pytest.raises(ValueError):
        OwnerId("é" * 128 + "a")
    with pytest.raises(ValueError):
        OwnerId("\ud800")


def test_sandbox_handle_requires_non_nil_uuid_and_has_canonical_text() -> None:
    value = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    assert str(SandboxHandle(value)) == str(value)
    with pytest.raises(ValueError):
        SandboxHandle(UUID(int=0))
    with pytest.raises(TypeError):
        SandboxHandle("not-a-uuid")  # type: ignore[arg-type]


def test_request_seed_tuple_is_immutable() -> None:
    request = CreateSandboxRequest(
        owner_id=OwnerId("owner"),
        initial_files=(WorkspaceSeedFile("/workspace/a.txt", b"a"),),
    )

    assert request.initial_files == (WorkspaceSeedFile("/workspace/a.txt", b"a"),)
    with pytest.raises(FrozenInstanceError):
        request.initial_files = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        CreateSandboxRequest(
            owner_id=OwnerId("owner"),
            initial_files=[],  # type: ignore[arg-type]
        )


def test_factory_request_rejects_seed_and_restore_combination(
    empty_snapshot_state: SessionSnapshotState,
) -> None:
    with pytest.raises(ValueError):
        SessionFactoryRequest(
            session_id=SessionId(UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")),
            options=SandboxOptions(),
            snapshot_store=cast(FactorySnapshotStore, _SnapshotStore()),
            initial_files=(WorkspaceSeedFile("/workspace/a.txt", b"a"),),
            restored_state=empty_snapshot_state,
        )


class _SnapshotStore:
    process_local = True
