from __future__ import annotations

import json
from typing import ClassVar

import pytest
from benchmarks.cases import (
    CaseConfig,
    DriverFactory,
    measure_burst_create,
    measure_create_first_operation,
)
from benchmarks.profiles import load_profile
from benchmarks.runner import run_suite
from benchmarks.schema import (
    SCHEMA_VERSION,
    calculate_statistics,
    consistent_correctness_checksum,
)
from benchmarks.validation import (
    ExecuteOutcome,
    PatchOutcome,
    ReadOutcome,
    ValidationCreateRequest,
    ValidationError,
    ValidationSession,
    ValidationSessionState,
    ValidationSnapshot,
    WriteCondition,
    WriteOutcome,
)


class RecordingSession:
    identity = "recording-session"

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def execute(self, command: str) -> ExecuteOutcome | ValidationError:
        raise AssertionError(command)

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ReadOutcome | ValidationError:
        raise AssertionError((path, start_line, end_line))

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        write_condition: WriteCondition,
        expected_hash: str | None = None,
        create_parents: bool = False,
    ) -> WriteOutcome | ValidationError:
        self._events.append("write")
        return WriteOutcome(1, True, True, None, "hash")

    async def apply_patch(
        self,
        patch: str,
        expected_hashes: tuple[tuple[str, str], ...],
    ) -> PatchOutcome | ValidationError:
        raise AssertionError((patch, expected_hashes))

    async def snapshot(self) -> ValidationSnapshot:
        raise AssertionError("snapshot")

    async def state(self) -> ValidationSessionState:
        raise AssertionError("state")

    async def close(self) -> None:
        self._events.append("session-close")


class RecordingDriver:
    name: ClassVar[str] = "recording"

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def create(self, request: ValidationCreateRequest) -> ValidationSession:
        self._events.append("create")
        return RecordingSession(self._events)

    async def resume(
        self,
        snapshot: ValidationSnapshot,
        owner_id: str,
    ) -> ValidationSession:
        raise AssertionError((snapshot, owner_id))

    async def delete(self, session: ValidationSession) -> bool:
        self._events.append("delete")
        return True

    async def delete_snapshot(self, snapshot: ValidationSnapshot) -> None:
        raise AssertionError(snapshot)

    async def snapshot_count(self) -> int:
        raise AssertionError("snapshot_count")

    async def close(self) -> None:
        self._events.append("driver-close")


class FailingBurstDriver(RecordingDriver):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self._create_count = 0

    async def create(self, request: ValidationCreateRequest) -> ValidationSession:
        index = self._create_count
        self._create_count += 1
        self._events.append(f"create-{index}")
        if index == 1:
            raise RuntimeError("create failed")
        return RecordingSession(self._events)


def test_statistics_withhold_unsupported_tail_quantiles() -> None:
    statistics = calculate_statistics((1, 2, 3))

    assert statistics.count == 3
    assert statistics.median_ns == 2
    assert statistics.p95_ns is None
    assert statistics.p99_ns is None


def test_correctness_checksum_is_independent_of_sample_count() -> None:
    assert consistent_correctness_checksum(("same",)) == "same"
    assert consistent_correctness_checksum(("same", "same", "same")) == "same"
    with pytest.raises(ValueError, match="different correctness"):
        consistent_correctness_checksum(("first", "second"))


@pytest.mark.asyncio
async def test_create_first_operation_timing_includes_session_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    timestamps = iter((100, 160))

    def read_clock() -> int:
        events.append("clock")
        return next(timestamps)

    monkeypatch.setattr("benchmarks.cases.time.perf_counter_ns", read_clock)
    factory = DriverFactory(
        "recording",
        lambda: RecordingDriver(events),
    )

    result = await measure_create_first_operation(
        factory,
        load_profile("empty"),
        CaseConfig(warmups=0, samples=1),
    )

    assert result.case == "create_first_operation"
    assert result.samples_ns == (60,)
    assert events == ["clock", "create", "write", "clock", "delete", "driver-close"]


@pytest.mark.asyncio
async def test_burst_create_failure_settles_and_cleans_successful_sessions() -> None:
    events: list[str] = []
    factory = DriverFactory(
        "failing-burst",
        lambda: FailingBurstDriver(events),
    )

    result = await measure_burst_create(
        factory,
        load_profile("empty"),
        CaseConfig(warmups=0, samples=1, burst_concurrency=3),
    )

    assert [(failure.category, failure.message) for failure in result.failures] == [
        ("RuntimeError", "create failed")
    ]
    assert events[:3] == ["create-0", "create-1", "create-2"]
    assert events.count("delete") == 2
    assert events[-1] == "driver-close"


@pytest.mark.asyncio
async def test_controlled_tier_requires_future_runner_integration() -> None:
    with pytest.raises(ValueError, match="controlled benchmark execution is deferred"):
        await run_suite(
            CaseConfig(warmups=0, samples=1),
            tier="controlled",
        )


@pytest.mark.asyncio
@pytest.mark.benchmark_smoke
async def test_benchmark_smoke_emits_every_required_case_without_failures() -> None:
    artifact = await run_suite(
        CaseConfig(warmups=0, samples=1, burst_concurrency=2),
        tier="smoke",
    )

    assert artifact.schema_version == SCHEMA_VERSION
    assert artifact.environment.network_involved is False
    assert artifact.environment.external_provider_involved is False
    assert {
        "core_create_ready",
        "adapter_create_ready",
        "create_first_operation",
        "cold_process_ready",
        "snapshot_create",
        "resume_ready",
        "resume_first_operation",
        "burst_create",
        "memory",
        "backend_adapter_overhead",
        "capability_overhead",
        "stateful_scenario",
    } <= {case.case for case in artifact.cases}
    assert all(case.statistics.count == 1 for case in artifact.cases)
    assert all(not case.failures for case in artifact.cases)
    assert all(case.correctness_checksum for case in artifact.cases)
    assert {
        (driver, profile)
        for driver in ("direct", "openai_sandbox", "openai_capability")
        for profile in ("empty", "small_project")
    } <= {(case.driver, case.profile) for case in artifact.cases if case.case == "memory"}
    parsed = json.loads(artifact.to_json())
    assert parsed["schema_version"] == SCHEMA_VERSION
