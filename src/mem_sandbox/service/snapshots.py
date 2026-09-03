"""Service snapshot gateway and provenance-decorating session view."""

from dataclasses import replace

from mem_sandbox.service.models import OwnerId
from mem_sandbox.service.ports import FactorySnapshotStore
from mem_sandbox.snapshots import SandboxSnapshot, SandboxSnapshotDraft, SnapshotRef


class InMemoryServiceSnapshotGateway:
    """Use one shared store for lifecycle load and session save/load."""

    def __init__(self, store: FactorySnapshotStore) -> None:
        self._store = store

    def session_store(self, owner_id: OwnerId) -> FactorySnapshotStore:
        return _ProvenanceSnapshotStore(self._store, owner_id)

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        return await self._store.load(snapshot_ref)


class _ProvenanceSnapshotStore:
    def __init__(self, store: FactorySnapshotStore, owner_id: OwnerId) -> None:
        self._store = store
        self._owner_id = owner_id

    @property
    def process_local(self) -> bool:
        return self._store.process_local

    async def save(self, draft: SandboxSnapshotDraft) -> SnapshotRef:
        return await self._store.save(replace(draft, created_by=self._owner_id.value))

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        return await self._store.load(snapshot_ref)
