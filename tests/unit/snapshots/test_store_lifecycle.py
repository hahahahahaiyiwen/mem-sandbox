from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from mem_sandbox.core import Revision, SessionId, SnapshotId
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    SandboxSnapshotDraft,
    SnapshotIdentifierConflict,
    SnapshotLoadFailed,
    SnapshotMetadata,
    SnapshotNotFound,
    SnapshotRef,
    SnapshotSaveFailed,
    SnapshotStoreFull,
    SnapshotStoreLimits,
)
from mem_sandbox.workspace import ContentHash

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
SESSION_ID = SessionId(UUID("11111111-1111-1111-1111-111111111111"))


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def draft(
    index: int,
    payload: bytes = b"payload",
    *,
    created_by: str | None = None,
) -> SandboxSnapshotDraft:
    return SandboxSnapshotDraft(
        snapshot_id=SnapshotId(UUID(int=index)),
        schema_version=1,
        created_at=NOW - timedelta(minutes=1),
        source_session_id=SESSION_ID,
        workspace_revision=Revision(index),
        content_hash=ContentHash.from_bytes(payload),
        payload=payload,
        metadata=SnapshotMetadata("json", len(payload), True),
        created_by=created_by,
    )


def store(
    clock: MutableClock,
    *,
    max_snapshots: int = 10,
    max_total_payload_bytes: int = 10_000,
    ttl: timedelta = timedelta(hours=1),
) -> InMemorySnapshotStore:
    return InMemorySnapshotStore(
        default_ttl=ttl,
        limits=SnapshotStoreLimits(
            max_snapshots=max_snapshots,
            max_total_payload_bytes=max_total_payload_bytes,
        ),
        clock=clock,
    )


def test_snapshot_models_and_store_configuration_are_exact_and_immutable() -> None:
    value = draft(1, created_by="é" * 128)
    creator = value.created_by
    assert creator is not None
    assert len(creator.encode("utf-8")) == 256
    with pytest.raises(FrozenInstanceError):
        value.created_by = "other"  # type: ignore[misc]
    with pytest.raises(ValueError, match="256"):
        draft(1, created_by="é" * 129)
    with pytest.raises(ValueError, match="positive"):
        SnapshotStoreLimits(max_snapshots=0, max_total_payload_bytes=1)
    with pytest.raises(ValueError, match="positive"):
        SnapshotStoreLimits(max_snapshots=1, max_total_payload_bytes=0)
    with pytest.raises(TypeError, match="payload"):
        replace(value, payload="not-bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        store(MutableClock(), ttl=timedelta(0))


@pytest.mark.asyncio
async def test_save_assigns_absolute_expiry_and_preserves_provenance() -> None:
    clock = MutableClock()
    snapshots = store(clock, ttl=timedelta(minutes=30))
    source = draft(1, created_by="application-owner")

    snapshot_ref = await snapshots.save(source)
    persisted = await snapshots.load(snapshot_ref)

    assert snapshot_ref == SnapshotRef(source.snapshot_id)
    assert persisted.snapshot_id == source.snapshot_id
    assert persisted.created_at == source.created_at
    assert persisted.expires_at == NOW + timedelta(minutes=30)
    assert persisted.source_session_id == SESSION_ID
    assert persisted.created_by == "application-owner"
    assert persisted.payload == source.payload
    assert (await snapshots.stats()).snapshot_count == 1
    assert (await snapshots.stats()).payload_bytes == len(source.payload)


@pytest.mark.asyncio
async def test_count_and_total_payload_limits_are_atomic_at_exact_boundaries() -> None:
    clock = MutableClock()
    first = draft(1, b"abc")
    second = draft(2, b"de")
    snapshots = store(clock, max_snapshots=2, max_total_payload_bytes=5)

    await snapshots.save(first)
    await snapshots.save(second)
    before = await snapshots.stats()

    with pytest.raises(SnapshotStoreFull):
        await snapshots.save(draft(3, b""))

    assert await snapshots.stats() == before
    assert await snapshots.load(SnapshotRef(first.snapshot_id))
    assert await snapshots.load(SnapshotRef(second.snapshot_id))


@pytest.mark.asyncio
async def test_one_byte_overflow_does_not_advance_store_state() -> None:
    clock = MutableClock()
    snapshots = store(clock, max_snapshots=3, max_total_payload_bytes=5)
    await snapshots.save(draft(1, b"abc"))
    before = await snapshots.stats()

    with pytest.raises(SnapshotStoreFull):
        await snapshots.save(draft(2, b"xyz"))

    assert await snapshots.stats() == before
    await snapshots.save(draft(2, b"de"))
    assert (await snapshots.stats()).payload_bytes == 5


@pytest.mark.asyncio
async def test_quota_uses_actual_payload_length_not_metadata() -> None:
    clock = MutableClock()
    snapshots = store(clock, max_total_payload_bytes=3)
    source = draft(1, b"abcd")
    undercounted = SandboxSnapshotDraft(
        snapshot_id=source.snapshot_id,
        schema_version=source.schema_version,
        created_at=source.created_at,
        source_session_id=source.source_session_id,
        workspace_revision=source.workspace_revision,
        content_hash=source.content_hash,
        payload=source.payload,
        metadata=SnapshotMetadata("json", 0, True),
    )

    with pytest.raises(SnapshotStoreFull):
        await snapshots.save(undercounted)

    assert (await snapshots.stats()).snapshot_count == 0


@pytest.mark.asyncio
async def test_expiry_boundary_load_removes_record_and_releases_quota() -> None:
    clock = MutableClock()
    snapshots = store(
        clock,
        max_snapshots=1,
        max_total_payload_bytes=3,
        ttl=timedelta(minutes=5),
    )
    source = draft(1, b"abc")
    snapshot_ref = await snapshots.save(source)

    clock.advance(timedelta(minutes=5) - timedelta(microseconds=1))
    assert await snapshots.load(snapshot_ref)
    clock.advance(timedelta(microseconds=1))
    with pytest.raises(SnapshotNotFound):
        await snapshots.load(snapshot_ref)
    with pytest.raises(SnapshotNotFound):
        await snapshots.load(snapshot_ref)

    assert (await snapshots.stats()).snapshot_count == 0
    await snapshots.save(draft(2, b"abc"))


@pytest.mark.asyncio
async def test_invalid_clock_uses_operation_specific_store_failure() -> None:
    clock = MutableClock()
    snapshots = store(clock)
    snapshot_ref = await snapshots.save(draft(1))
    clock.value = datetime(2026, 9, 3, 12)

    with pytest.raises(SnapshotLoadFailed):
        await snapshots.load(snapshot_ref)
    with pytest.raises(SnapshotSaveFailed):
        await snapshots.save(draft(2))


@pytest.mark.asyncio
async def test_save_and_stats_purge_expired_records_without_background_tasks() -> None:
    clock = MutableClock()
    snapshots = store(clock, max_snapshots=1, ttl=timedelta(seconds=1))
    await snapshots.save(draft(1))
    clock.advance(timedelta(seconds=1))

    assert (await snapshots.stats()).snapshot_count == 0
    await snapshots.save(draft(2))
    assert (await snapshots.stats()).snapshot_count == 1


@pytest.mark.asyncio
async def test_explicit_purge_is_deterministic_and_idempotent() -> None:
    clock = MutableClock()
    snapshots = store(clock, ttl=timedelta(seconds=1))
    await snapshots.save(draft(3, b"333"))
    await snapshots.save(draft(1, b"1"))
    await snapshots.save(draft(2, b"22"))
    clock.advance(timedelta(seconds=1))

    purged = await snapshots.purge_expired()

    assert purged.removed_refs == (
        SnapshotRef(SnapshotId(UUID(int=1))),
        SnapshotRef(SnapshotId(UUID(int=2))),
        SnapshotRef(SnapshotId(UUID(int=3))),
    )
    assert purged.removed_payload_bytes == 6
    assert (await snapshots.stats()).snapshot_count == 0
    assert (await snapshots.purge_expired()).removed_refs == ()


@pytest.mark.asyncio
async def test_delete_is_idempotent_and_duplicate_live_id_is_rejected() -> None:
    clock = MutableClock()
    snapshots = store(clock)
    source = draft(1)
    snapshot_ref = await snapshots.save(source)

    with pytest.raises(SnapshotIdentifierConflict):
        await snapshots.save(source)

    await snapshots.delete(snapshot_ref)
    await snapshots.delete(snapshot_ref)
    assert (await snapshots.stats()).snapshot_count == 0


@pytest.mark.asyncio
async def test_concurrent_saves_for_final_capacity_have_one_winner() -> None:
    clock = MutableClock()
    snapshots = store(clock, max_snapshots=1)

    results = await asyncio.gather(
        snapshots.save(draft(1)),
        snapshots.save(draft(2)),
        return_exceptions=True,
    )

    assert sum(isinstance(result, SnapshotRef) for result in results) == 1
    assert sum(isinstance(result, SnapshotStoreFull) for result in results) == 1
    assert (await snapshots.stats()).snapshot_count == 1
