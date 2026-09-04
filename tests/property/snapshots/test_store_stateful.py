from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import (
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType]
)
from tests.property.async_machine import AsyncRuleBasedStateMachine, AsyncRunner
from tests.property.strategies import binary_payloads

from mem_sandbox.core import Revision, SessionId, SnapshotId
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    SandboxSnapshotDraft,
    SnapshotIdentifierConflict,
    SnapshotMetadata,
    SnapshotNotFound,
    SnapshotRef,
    SnapshotStoreFull,
    SnapshotStoreLimits,
    SnapshotStoreStats,
)
from mem_sandbox.workspace import ContentHash

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
TTL = timedelta(seconds=5)
SESSION_ID = SessionId(UUID("11111111-1111-1111-1111-111111111111"))


class LogicalClock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class StoredRecord:
    payload: bytes
    expires_at: datetime


def _ref(index: int) -> SnapshotRef:
    return SnapshotRef(SnapshotId(UUID(int=index)))


def _draft(index: int, payload: bytes) -> SandboxSnapshotDraft:
    return SandboxSnapshotDraft(
        snapshot_id=_ref(index).snapshot_id,
        schema_version=1,
        created_at=NOW,
        source_session_id=SESSION_ID,
        workspace_revision=Revision(index),
        content_hash=ContentHash.from_bytes(payload),
        payload=payload,
        metadata=SnapshotMetadata("json", len(payload), False),
    )


class SnapshotStoreStateMachine(AsyncRuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.clock = LogicalClock()
        self.store = InMemorySnapshotStore(
            default_ttl=TTL,
            limits=SnapshotStoreLimits(
                max_snapshots=3,
                max_total_payload_bytes=8,
            ),
            clock=self.clock,
        )
        self.records: dict[int, StoredRecord] = {}

    def _purge_model(self) -> tuple[tuple[SnapshotRef, ...], int]:
        expired = tuple(
            sorted(
                (
                    (index, record)
                    for index, record in self.records.items()
                    if record.expires_at <= self.clock.now()
                ),
                key=lambda item: str(_ref(item[0]).snapshot_id),
            )
        )
        for index, _ in expired:
            del self.records[index]
        return (
            tuple(_ref(index) for index, _ in expired),
            sum(len(record.payload) for _, record in expired),
        )

    def _expected_stats(self) -> SnapshotStoreStats:
        return SnapshotStoreStats(
            snapshot_count=len(self.records),
            payload_bytes=sum(len(record.payload) for record in self.records.values()),
        )

    def _assert_stats(self) -> None:
        self._purge_model()
        assert self.run_async(self.store.stats()) == self._expected_stats()

    @rule(
        index=st.integers(min_value=1, max_value=6),
        payload=binary_payloads(max_size=9),
    )
    def save(self, index: int, payload: bytes) -> None:
        self._purge_model()
        operation = self.store.save(_draft(index, payload))
        if index in self.records:
            with pytest.raises(SnapshotIdentifierConflict):
                self.run_async(operation)
            self._assert_stats()
            return

        prospective_bytes = self._expected_stats().payload_bytes + len(payload)
        if len(self.records) + 1 > 3 or prospective_bytes > 8:
            with pytest.raises(SnapshotStoreFull):
                self.run_async(operation)
            self._assert_stats()
            return

        assert self.run_async(operation) == _ref(index)
        self.records[index] = StoredRecord(payload, self.clock.now() + TTL)
        self._assert_stats()

    @rule(index=st.integers(min_value=1, max_value=6))
    def load(self, index: int) -> None:
        record = self.records.get(index)
        if record is None:
            with pytest.raises(SnapshotNotFound):
                self.run_async(self.store.load(_ref(index)))
            return
        if record.expires_at <= self.clock.now():
            del self.records[index]
            with pytest.raises(SnapshotNotFound):
                self.run_async(self.store.load(_ref(index)))
            return

        snapshot = self.run_async(self.store.load(_ref(index)))
        assert snapshot.payload == record.payload
        assert snapshot.expires_at == record.expires_at

    @rule(index=st.integers(min_value=1, max_value=6))
    def delete(self, index: int) -> None:
        self.run_async(self.store.delete(_ref(index)))
        self.records.pop(index, None)

    @rule(seconds=st.integers(min_value=0, max_value=6))
    def advance_clock(self, seconds: int) -> None:
        self.clock.advance(seconds)

    @rule()
    def observe_stats(self) -> None:
        self._assert_stats()

    @rule()
    def purge_expired(self) -> None:
        expected_refs, expected_bytes = self._purge_model()
        result = self.run_async(self.store.purge_expired())
        assert result.removed_refs == expected_refs
        assert result.removed_payload_bytes == expected_bytes
        assert self.run_async(self.store.purge_expired()).removed_refs == ()
        self._assert_stats()

    def teardown(self) -> None:
        self._assert_stats()
        super().teardown()


def test_snapshot_store_state_machine() -> None:
    run_state_machine_as_test(SnapshotStoreStateMachine)


@given(
    first_payload=st.binary(min_size=1, max_size=8),
    second_payload=st.binary(min_size=1, max_size=8),
)
def test_concurrent_saves_enforce_count_and_byte_capacity(
    first_payload: bytes,
    second_payload: bytes,
) -> None:
    async def exercise() -> None:
        count_store = InMemorySnapshotStore(
            default_ttl=TTL,
            limits=SnapshotStoreLimits(
                max_snapshots=1,
                max_total_payload_bytes=16,
            ),
            clock=LogicalClock(),
        )
        count_results = await asyncio.gather(
            count_store.save(_draft(1, first_payload)),
            count_store.save(_draft(2, second_payload)),
            return_exceptions=True,
        )
        assert sum(isinstance(result, SnapshotRef) for result in count_results) == 1
        assert sum(isinstance(result, SnapshotStoreFull) for result in count_results) == 1

        byte_limit = max(len(first_payload), len(second_payload))
        byte_store = InMemorySnapshotStore(
            default_ttl=TTL,
            limits=SnapshotStoreLimits(
                max_snapshots=2,
                max_total_payload_bytes=byte_limit,
            ),
            clock=LogicalClock(),
        )
        byte_results = await asyncio.gather(
            byte_store.save(_draft(3, first_payload)),
            byte_store.save(_draft(4, second_payload)),
            return_exceptions=True,
        )
        assert sum(isinstance(result, SnapshotRef) for result in byte_results) == 1
        assert sum(isinstance(result, SnapshotStoreFull) for result in byte_results) == 1
        stats = await byte_store.stats()
        assert stats.snapshot_count == 1
        assert stats.payload_bytes in {len(first_payload), len(second_payload)}

    with AsyncRunner() as runner:
        runner.run(exercise())
