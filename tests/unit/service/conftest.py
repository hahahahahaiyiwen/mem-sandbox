from datetime import UTC, datetime
from uuid import UUID

import pytest

from mem_sandbox.command_executor import CommandEnvironment
from mem_sandbox.core import Revision
from mem_sandbox.snapshots import SessionSnapshotState
from mem_sandbox.workspace import ContentHash, SandboxPath, WorkspaceSnapshotData


@pytest.fixture
def empty_snapshot_state() -> SessionSnapshotState:
    encoded = (
        b'{"entries":[{"kind":"directory","path":"/workspace"}],'
        b'"limits":{"max_file_bytes":4194304,"max_nodes":10000,'
        b'"max_path_bytes":4096,"max_segment_bytes":255,'
        b'"max_snapshot_bytes":33554432,"max_total_bytes":16777216},'
        b'"revision":0,"root_hash":"' + b"0" * 64 + b'","schema_version":1}'
    )
    return SessionSnapshotState(
        workspace=WorkspaceSnapshotData(
            encoded=encoded,
            schema_version=1,
            integrity_hash=ContentHash.from_bytes(encoded),
            workspace_revision=Revision.initial(),
            root_hash=ContentHash("0" * 64),
        ),
        cwd=SandboxPath.root(),
        approved_environment=CommandEnvironment(),
    )


@pytest.fixture
def fixed_time() -> datetime:
    return datetime(2026, 9, 3, 12, tzinfo=UTC)


@pytest.fixture
def fixed_session_id() -> UUID:
    return UUID("11111111-1111-1111-1111-111111111111")
