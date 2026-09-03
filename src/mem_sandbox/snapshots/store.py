"""Bounded process-local immutable snapshot store."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

from mem_sandbox.core import Clock
from mem_sandbox.snapshots.errors import (
    SnapshotIdentifierConflict,
    SnapshotLoadFailed,
    SnapshotNotFound,
    SnapshotSaveFailed,
    SnapshotStoreFull,
)
from mem_sandbox.snapshots.models import (
    SandboxSnapshot,
    SandboxSnapshotDraft,
    SnapshotPurgeResult,
    SnapshotRef,
    SnapshotStoreLimits,
    SnapshotStoreStats,
)


class InMemorySnapshotStore:
    """Store finite immutable snapshots with lazy absolute expiration."""

    def __init__(
        self,
        *,
        default_ttl: timedelta,
        limits: SnapshotStoreLimits,
        clock: Clock,
    ) -> None:
        ttl = cast(object, default_ttl)
        if not isinstance(ttl, timedelta):
            raise TypeError("default_ttl must be a timedelta")
        if ttl <= timedelta(0):
            raise ValueError("default_ttl must be positive")
        if not isinstance(cast(object, limits), SnapshotStoreLimits):
            raise TypeError("limits must be SnapshotStoreLimits")
        self._default_ttl = ttl
        self._limits = limits
        self._clock = clock
        self._lock = asyncio.Lock()
        self._snapshots: dict[SnapshotRef, SandboxSnapshot] = {}
        self._payload_bytes = 0

    @property
    def process_local(self) -> bool:
        return True

    async def save(self, draft: SandboxSnapshotDraft) -> SnapshotRef:
        snapshot_ref = SnapshotRef(draft.snapshot_id)
        async with self._lock:
            now = self._now(SnapshotSaveFailed)
            self._purge_expired_locked(now)
            if snapshot_ref in self._snapshots:
                raise SnapshotIdentifierConflict(
                    f"snapshot identifier {draft.snapshot_id} already exists"
                )
            payload_bytes = len(draft.payload)
            if (
                len(self._snapshots) + 1 > self._limits.max_snapshots
                or self._payload_bytes + payload_bytes > self._limits.max_total_payload_bytes
            ):
                raise SnapshotStoreFull(
                    "accepting the snapshot would exceed the configured store limits"
                )
            try:
                expires_at = now + self._default_ttl
            except OverflowError as error:
                raise SnapshotSaveFailed(
                    "snapshot expiration exceeds the supported datetime range"
                ) from error
            snapshot = SandboxSnapshot(
                snapshot_id=draft.snapshot_id,
                schema_version=draft.schema_version,
                created_at=draft.created_at,
                expires_at=expires_at,
                source_session_id=draft.source_session_id,
                workspace_revision=draft.workspace_revision,
                content_hash=draft.content_hash,
                payload=draft.payload,
                metadata=replace(draft.metadata, process_local=True),
                created_by=draft.created_by,
            )
            self._snapshots[snapshot_ref] = snapshot
            self._payload_bytes += payload_bytes
        return snapshot_ref

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        async with self._lock:
            now = self._now(SnapshotLoadFailed)
            snapshot = self._snapshots.get(snapshot_ref)
            if snapshot is None:
                raise SnapshotNotFound(f"snapshot {snapshot_ref.snapshot_id} does not exist")
            if snapshot.expires_at <= now:
                self._remove_locked(snapshot_ref)
                raise SnapshotNotFound(f"snapshot {snapshot_ref.snapshot_id} does not exist")
            return snapshot

    async def delete(self, snapshot_ref: SnapshotRef) -> None:
        async with self._lock:
            self._remove_locked(snapshot_ref)

    async def purge_expired(self) -> SnapshotPurgeResult:
        async with self._lock:
            removed = self._purge_expired_locked(self._now(SnapshotLoadFailed))
        return SnapshotPurgeResult(
            removed_refs=tuple(
                sorted(
                    (snapshot_ref for snapshot_ref, _ in removed),
                    key=lambda item: str(item.snapshot_id),
                )
            ),
            removed_payload_bytes=sum(len(snapshot.payload) for _, snapshot in removed),
        )

    async def stats(self) -> SnapshotStoreStats:
        async with self._lock:
            self._purge_expired_locked(self._now(SnapshotLoadFailed))
            return SnapshotStoreStats(len(self._snapshots), self._payload_bytes)

    def _purge_expired_locked(
        self,
        now: datetime,
    ) -> tuple[tuple[SnapshotRef, SandboxSnapshot], ...]:
        expired = tuple(
            (snapshot_ref, snapshot)
            for snapshot_ref, snapshot in self._snapshots.items()
            if snapshot.expires_at <= now
        )
        for snapshot_ref, _ in expired:
            self._remove_locked(snapshot_ref)
        return expired

    def _remove_locked(self, snapshot_ref: SnapshotRef) -> None:
        snapshot = self._snapshots.pop(snapshot_ref, None)
        if snapshot is not None:
            self._payload_bytes -= len(snapshot.payload)

    def _now(
        self,
        error_type: type[SnapshotSaveFailed] | type[SnapshotLoadFailed],
    ) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise error_type("snapshot store clock must return an aware datetime")
        if now.utcoffset() != timedelta(0):
            raise error_type("snapshot store clock must return UTC")
        return now
