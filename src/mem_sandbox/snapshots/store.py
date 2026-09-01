"""Atomic process-local immutable snapshot store."""

import asyncio

from mem_sandbox.snapshots.errors import SnapshotIdentifierConflict, SnapshotNotFound
from mem_sandbox.snapshots.models import SandboxSnapshot, SnapshotRef


class InMemorySnapshotStore:
    """Store immutable snapshots by opaque identifier without authorization."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._snapshots: dict[SnapshotRef, SandboxSnapshot] = {}

    @property
    def process_local(self) -> bool:
        return True

    async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef:
        snapshot_ref = SnapshotRef(snapshot.snapshot_id)
        async with self._lock:
            if snapshot_ref in self._snapshots:
                raise SnapshotIdentifierConflict(
                    f"snapshot identifier {snapshot.snapshot_id} already exists"
                )
            self._snapshots[snapshot_ref] = snapshot
        return snapshot_ref

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        async with self._lock:
            snapshot = self._snapshots.get(snapshot_ref)
        if snapshot is None:
            raise SnapshotNotFound(f"snapshot {snapshot_ref.snapshot_id} does not exist")
        return snapshot

    async def delete(self, snapshot_ref: SnapshotRef) -> None:
        async with self._lock:
            self._snapshots.pop(snapshot_ref, None)
