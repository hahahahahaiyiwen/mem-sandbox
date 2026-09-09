"""Provisioning, lifecycle, memory, and adapter-overhead benchmark cases."""

from __future__ import annotations

import asyncio
import gc
import json
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Protocol, cast

from benchmarks.drivers import (
    DirectValidationDriver,
    OpenAICapabilityValidationDriver,
    OpenAISandboxValidationDriver,
)
from benchmarks.profiles import WorkloadDimensions, WorkloadProfile
from benchmarks.schema import (
    BenchmarkCaseResult,
    BenchmarkFailure,
    MemoryMeasurements,
    TimingMode,
    calculate_statistics,
    consistent_correctness_checksum,
)
from benchmarks.validation import (
    ProductValidationDriver,
    ValidationError,
    ValidationFile,
    ValidationSession,
    run_stateful_reference_scenario,
)
from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    ContentHashMustEqual,
    CopyPathRequest,
    MemoryWorkspace,
    SandboxPath,
    WorkspaceBinaryResult,
    WorkspaceMutation,
    WorkspaceSnapshotData,
    WorkspaceStats,
    WorkspaceWriteRequest,
)


@dataclass(frozen=True, slots=True)
class DriverFactory:
    name: str
    constructor: Callable[[], ProductValidationDriver]

    def __call__(self) -> ProductValidationDriver:
        return self.constructor()


DIRECT_DRIVER = DriverFactory("direct", DirectValidationDriver)
OPENAI_SANDBOX_DRIVER = DriverFactory(
    "openai_sandbox",
    OpenAISandboxValidationDriver,
)
OPENAI_CAPABILITY_DRIVER = DriverFactory(
    "openai_capability",
    OpenAICapabilityValidationDriver,
)
_COLD_WORKER_TIMEOUT_SECONDS = 60


class WorkspaceBenchmarkPort(Protocol):
    def resolve_path(
        self,
        value: str,
        *,
        cwd: SandboxPath | None = None,
    ) -> SandboxPath: ...

    async def stats(self) -> WorkspaceStats: ...

    async def read_bytes(self, path: SandboxPath) -> WorkspaceBinaryResult: ...

    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation: ...

    async def copy(self, request: CopyPathRequest) -> WorkspaceMutation: ...

    async def export(self) -> WorkspaceSnapshotData: ...


@dataclass(frozen=True, slots=True)
class WorkspaceFactory:
    name: str
    constructor: Callable[[], WorkspaceBenchmarkPort]

    def __call__(self) -> WorkspaceBenchmarkPort:
        return self.constructor()


MEMORY_WORKSPACE = WorkspaceFactory("memory_workspace", MemoryWorkspace)


@dataclass(frozen=True, slots=True)
class CaseConfig:
    warmups: int
    samples: int
    burst_concurrency: int = 8
    minimum_duration_seconds: float = 0.0
    maximum_samples: int = 10_000

    def __post_init__(self) -> None:
        if self.warmups < 0:
            raise ValueError("warmups must not be negative")
        if self.samples <= 0:
            raise ValueError("samples must be positive")
        if self.burst_concurrency <= 0:
            raise ValueError("burst_concurrency must be positive")
        if self.minimum_duration_seconds < 0:
            raise ValueError("minimum_duration_seconds must not be negative")
        if self.maximum_samples < self.samples:
            raise ValueError("maximum_samples must not be less than samples")


async def measure_workspace_seed(
    factory: WorkspaceFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        workspace = factory()
        files = profile.files()
        started = time.perf_counter_ns()
        await _write_profile_files(workspace, files)
        elapsed = time.perf_counter_ns() - started
        stats = await workspace.stats()
        _require_profile_stats(profile, stats)
        return elapsed, _workspace_checksum(stats), MemoryMeasurements()

    return await _measure(
        "workspace_seed",
        factory.name,
        profile,
        "warm",
        config,
        sample,
        dimensions=_operation_dimensions(
            profile,
            file_count=profile.file_count,
            byte_count=profile.dimensions.total_bytes,
        ),
    )


async def measure_workspace_read(
    factory: WorkspaceFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    target_index = profile.file_count // 2

    async def sample() -> tuple[int, str, MemoryMeasurements]:
        workspace, _ = await _seed_workspace(factory, profile)
        expected = profile.file(target_index)
        target = workspace.resolve_path(expected.path)
        started = time.perf_counter_ns()
        result = await workspace.read_bytes(target)
        elapsed = time.perf_counter_ns() - started
        if result.content != expected.content:
            raise RuntimeError("workspace hot read returned unexpected content")
        return (
            elapsed,
            _checksum(
                (
                    result.path.value,
                    len(result.content),
                    result.content_hash.value,
                    result.revision.value,
                )
            ),
            MemoryMeasurements(),
        )

    return await _measure(
        "workspace_read_hot",
        factory.name,
        profile,
        "warm",
        config,
        sample,
        dimensions=_operation_dimensions(
            profile,
            file_count=1,
            byte_count=profile.file_bytes,
        ),
    )


async def measure_workspace_overwrite(
    factory: WorkspaceFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    target_index = profile.file_count // 2

    async def sample() -> tuple[int, str, MemoryMeasurements]:
        workspace, _ = await _seed_workspace(factory, profile)
        existing = profile.file(target_index)
        replacement = profile.file(target_index, variant="overwrite")
        started = time.perf_counter_ns()
        result = await workspace.write(
            WorkspaceWriteRequest(
                path=workspace.resolve_path(existing.path),
                content=replacement.content,
                precondition=ContentHashMustEqual(ContentHash.from_bytes(existing.content)),
            )
        )
        elapsed = time.perf_counter_ns() - started
        expected_hash = ContentHash.from_bytes(replacement.content)
        if (
            not result.changed
            or result.current_hash != expected_hash
            or result.stats.total_bytes != profile.dimensions.total_bytes
        ):
            raise RuntimeError("workspace overwrite produced unexpected state")
        return (
            elapsed,
            _checksum(
                (
                    result.stats.revision.value,
                    result.stats.root_hash.value,
                    result.previous_hash.value if result.previous_hash is not None else None,
                    result.current_hash.value if result.current_hash is not None else None,
                )
            ),
            MemoryMeasurements(),
        )

    return await _measure(
        "workspace_overwrite_hot",
        factory.name,
        profile,
        "warm",
        config,
        sample,
        dimensions=_operation_dimensions(
            profile,
            file_count=1,
            byte_count=profile.file_bytes,
        ),
    )


async def measure_workspace_copy(
    factory: WorkspaceFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    directory_index = profile.directory_count // 2
    copied_file_count = len(range(directory_index, profile.file_count, profile.directory_count))
    copied_bytes = copied_file_count * profile.file_bytes

    async def sample() -> tuple[int, str, MemoryMeasurements]:
        workspace, _ = await _seed_workspace(factory, profile)
        source = workspace.resolve_path(profile.directory_path(directory_index))
        destination = workspace.resolve_path(f"/workspace/copied-{directory_index:02d}")
        started = time.perf_counter_ns()
        result = await workspace.copy(CopyPathRequest(source, destination))
        elapsed = time.perf_counter_ns() - started
        expected_nodes = _profile_node_count(profile) + copied_file_count + 1
        if (
            not result.changed
            or result.stats.total_bytes != profile.dimensions.total_bytes + copied_bytes
            or result.stats.node_count != expected_nodes
        ):
            raise RuntimeError("workspace directory copy produced unexpected state")
        return elapsed, _workspace_checksum(result.stats), MemoryMeasurements()

    return await _measure(
        "workspace_copy_directory",
        factory.name,
        profile,
        "warm",
        config,
        sample,
        dimensions=_operation_dimensions(
            profile,
            file_count=copied_file_count,
            byte_count=copied_bytes,
        ),
    )


async def measure_workspace_snapshot(
    factory: WorkspaceFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    snapshot_sizes: list[int] = []

    async def sample() -> tuple[int, str, MemoryMeasurements]:
        workspace, _ = await _seed_workspace(factory, profile)
        started = time.perf_counter_ns()
        snapshot = await workspace.export()
        elapsed = time.perf_counter_ns() - started
        snapshot_sizes.append(len(snapshot.encoded))
        return (
            elapsed,
            _checksum(
                (
                    snapshot.schema_version,
                    snapshot.workspace_revision.value,
                    snapshot.root_hash.value,
                    snapshot.integrity_hash.value,
                    len(snapshot.encoded),
                )
            ),
            MemoryMeasurements(),
        )

    result = await _measure(
        "workspace_snapshot_encode",
        factory.name,
        profile,
        "warm",
        config,
        sample,
        dimensions=_operation_dimensions(
            profile,
            file_count=profile.file_count,
            byte_count=profile.dimensions.total_bytes,
        ),
    )
    distinct_sizes = set(snapshot_sizes)
    failures = result.failures
    snapshot_bytes: int | None = None
    if len(distinct_sizes) == 1:
        snapshot_bytes = distinct_sizes.pop()
    elif distinct_sizes:
        failures += (
            BenchmarkFailure(
                "snapshot_size_mismatch",
                "successful samples produced different encoded snapshot sizes",
            ),
        )
    return replace(
        result,
        dimensions=replace(result.dimensions, snapshot_bytes=snapshot_bytes),
        failures=failures,
    )


async def measure_workspace_memory(
    factory: WorkspaceFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        gc.collect()
        tracemalloc.start()
        try:
            baseline_current, _ = tracemalloc.get_traced_memory()
            workspace = factory()
            files = profile.files()
            started = time.perf_counter_ns()
            await _write_profile_files(workspace, files)
            elapsed = time.perf_counter_ns() - started
            del files
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            stats = await workspace.stats()
            _require_profile_stats(profile, stats)
            return (
                elapsed,
                _workspace_checksum(stats),
                MemoryMeasurements(
                    python_retained_bytes=max(0, current - baseline_current),
                    python_peak_bytes=max(0, peak - baseline_current),
                ),
            )
        finally:
            tracemalloc.stop()

    return await _measure(
        "workspace_retained_memory",
        factory.name,
        profile,
        "warm",
        config,
        sample,
        dimensions=_operation_dimensions(
            profile,
            file_count=profile.file_count,
            byte_count=profile.dimensions.total_bytes,
        ),
    )


async def measure_create_ready(
    factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
    *,
    case: str,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        driver = factory()
        request = profile.create_request("benchmark-create")
        started = time.perf_counter_ns()
        session = await driver.create(request)
        elapsed = time.perf_counter_ns() - started
        state = await session.state()
        await driver.delete(session)
        await driver.close()
        return (
            elapsed,
            _checksum((state.revision, state.cwd, state.approved_environment)),
            MemoryMeasurements(),
        )

    return await _measure(case, factory.name, profile, "warm", config, sample)


async def measure_create_first_operation(
    factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        driver = factory()
        request = profile.create_request("benchmark-first-operation")
        started = time.perf_counter_ns()
        session = await driver.create(request)
        if profile.file_count:
            outcome = await session.read_file(profile.file_path(0))
        else:
            outcome = await session.write_file(
                "first.txt",
                "first\n",
                write_condition="path_must_not_exist",
            )
        elapsed = time.perf_counter_ns() - started
        if isinstance(outcome, ValidationError):
            raise RuntimeError(f"{outcome.category}/{outcome.code}")
        await driver.delete(session)
        await driver.close()
        return elapsed, _checksum(outcome), MemoryMeasurements()

    return await _measure(
        "create_first_operation",
        factory.name,
        profile,
        "warm",
        config,
        sample,
    )


async def measure_snapshot_create(
    factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        driver = factory()
        session = await driver.create(profile.create_request("benchmark-snapshot"))
        started = time.perf_counter_ns()
        snapshot = await session.snapshot()
        elapsed = time.perf_counter_ns() - started
        await driver.delete(session)
        await driver.delete_snapshot(snapshot)
        await driver.close()
        return elapsed, snapshot.state_hash, MemoryMeasurements()

    return await _measure("snapshot_create", factory.name, profile, "warm", config, sample)


async def measure_resume(
    factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
    *,
    first_operation: bool,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        driver = factory()
        source = await driver.create(profile.create_request("benchmark-resume-source"))
        snapshot = await source.snapshot()
        await source.close()
        await driver.delete(source)
        started = time.perf_counter_ns()
        resumed = await driver.resume(snapshot, "benchmark-resumed")
        if first_operation:
            if profile.file_count:
                outcome: object = await resumed.read_file(profile.file_path(0))
            else:
                outcome = await resumed.write_file(
                    "resumed.txt",
                    "resumed\n",
                    write_condition="path_must_not_exist",
                )
            if isinstance(outcome, ValidationError):
                raise RuntimeError(f"{outcome.category}/{outcome.code}")
            elapsed = time.perf_counter_ns() - started
        else:
            elapsed = time.perf_counter_ns() - started
            outcome = await resumed.state()
        await driver.delete(resumed)
        await driver.delete_snapshot(snapshot)
        await driver.close()
        return elapsed, _checksum(outcome), MemoryMeasurements()

    case = "resume_first_operation" if first_operation else "resume_ready"
    return await _measure(case, factory.name, profile, "warm", config, sample)


async def measure_burst_create(
    factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        driver = factory()
        sessions: list[ValidationSession] = []
        requests = tuple(
            profile.create_request(f"benchmark-burst-{index}")
            for index in range(config.burst_concurrency)
        )
        try:
            started = time.perf_counter_ns()
            outcomes = await asyncio.gather(
                *(driver.create(request) for request in requests),
                return_exceptions=True,
            )
            elapsed = time.perf_counter_ns() - started
            first_error: BaseException | None = None
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    if first_error is None:
                        first_error = outcome
                else:
                    sessions.append(outcome)
            if first_error is not None:
                raise first_error
            states = await asyncio.gather(*(session.state() for session in sessions))
            return (
                elapsed,
                _checksum(tuple(state.revision for state in states)),
                MemoryMeasurements(),
            )
        finally:
            primary = sys.exception()
            cleanup_errors = await _cleanup_driver_sessions(driver, tuple(sessions))
            if cleanup_errors:
                if primary is None:
                    raise cleanup_errors[0]
                for error in cleanup_errors:
                    primary.add_note(f"secondary benchmark cleanup failure: {error}")

    return await _measure("burst_create", factory.name, profile, "warm", config, sample)


async def measure_memory(
    factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        gc.collect()
        tracemalloc.start()
        baseline_current, _ = tracemalloc.get_traced_memory()
        driver = factory()
        sessions: list[ValidationSession] = []
        session_count = config.burst_concurrency if profile.name == "empty" else 1
        try:
            requests = tuple(
                profile.create_request(f"benchmark-memory-{index}")
                for index in range(session_count)
            )
            started = time.perf_counter_ns()
            outcomes = await asyncio.gather(
                *(driver.create(request) for request in requests),
                return_exceptions=True,
            )
            elapsed = time.perf_counter_ns() - started
            del requests
            first_error: BaseException | None = None
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    if first_error is None:
                        first_error = outcome
                else:
                    sessions.append(outcome)
            if first_error is not None:
                raise first_error
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            states = await asyncio.gather(*(session.state() for session in sessions))
            return (
                elapsed,
                _checksum(tuple(state.revision for state in states)),
                MemoryMeasurements(
                    python_retained_bytes=max(0, current - baseline_current),
                    python_peak_bytes=max(0, peak - baseline_current),
                ),
            )
        finally:
            primary = sys.exception()
            tracemalloc.stop()
            cleanup_errors = await _cleanup_driver_sessions(driver, tuple(sessions))
            if cleanup_errors:
                if primary is None:
                    raise cleanup_errors[0]
                for error in cleanup_errors:
                    primary.add_note(f"secondary benchmark cleanup failure: {error}")

    return await _measure("memory", factory.name, profile, "warm", config, sample)


async def measure_stateful_scenario(
    factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    if profile.name != "empty":
        raise ValueError("stateful scenario uses the versioned empty reference profile")

    async def sample() -> tuple[int, str, MemoryMeasurements]:
        started = time.perf_counter_ns()
        trace = await run_stateful_reference_scenario(factory())
        elapsed = time.perf_counter_ns() - started
        return elapsed, trace.correctness_checksum, MemoryMeasurements()

    return await _measure("stateful_scenario", factory.name, profile, "warm", config, sample)


async def measure_adapter_overhead(
    adapter_factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
    *,
    case: str,
    baseline_factory: DriverFactory = DIRECT_DRIVER,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        baseline_ns, baseline_checksum = await _create_elapsed(baseline_factory, profile)
        adapter_ns, adapter_checksum = await _create_elapsed(adapter_factory, profile)
        elapsed = max(0, adapter_ns - baseline_ns)
        return (
            elapsed,
            _checksum((baseline_checksum, adapter_checksum)),
            MemoryMeasurements(),
        )

    return await _measure(case, adapter_factory.name, profile, "paired", config, sample)


async def measure_cold_process_ready(
    driver_name: str,
    profile: WorkloadProfile,
    config: CaseConfig,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        elapsed, payload = await asyncio.to_thread(
            _run_cold_worker,
            driver_name,
            profile.name,
        )
        return elapsed, str(payload["correctness_checksum"]), MemoryMeasurements()

    return await _measure(
        "cold_process_ready",
        driver_name,
        profile,
        "cold_process",
        config,
        sample,
    )


async def _measure(
    case: str,
    driver: str,
    profile: WorkloadProfile,
    timing_mode: TimingMode,
    config: CaseConfig,
    sample: Callable[[], Awaitable[tuple[int, str, MemoryMeasurements]]],
    *,
    dimensions: WorkloadDimensions | None = None,
) -> BenchmarkCaseResult:
    warmups: list[int] = []
    samples: list[int] = []
    failures: list[BenchmarkFailure] = []
    checksums: list[str] = []
    memory_values: list[MemoryMeasurements] = []
    for _ in range(config.warmups):
        try:
            elapsed, checksum, memory = await sample()
        except Exception as error:
            failures.append(BenchmarkFailure(type(error).__name__, str(error)))
            break
        warmups.append(elapsed)
    minimum_duration_ns = int(config.minimum_duration_seconds * 1_000_000_000)
    while not failures and (len(samples) < config.samples or sum(samples) < minimum_duration_ns):
        if len(samples) >= config.maximum_samples:
            failures.append(
                BenchmarkFailure(
                    "minimum_duration_not_met",
                    (
                        f"case did not reach {config.minimum_duration_seconds} seconds "
                        f"within {config.maximum_samples} samples"
                    ),
                )
            )
            break
        try:
            elapsed, checksum, memory = await sample()
        except Exception as error:
            failures.append(BenchmarkFailure(type(error).__name__, str(error)))
            break
        samples.append(elapsed)
        checksums.append(checksum)
        memory_values.append(memory)
    try:
        correctness_checksum = consistent_correctness_checksum(tuple(checksums))
    except ValueError as error:
        failures.append(BenchmarkFailure("correctness_mismatch", str(error)))
        correctness_checksum = ""
    return BenchmarkCaseResult(
        case=case,
        driver=driver,
        profile=profile.name,
        profile_version=profile.version,
        timing_mode=timing_mode,
        dimensions=profile.dimensions if dimensions is None else dimensions,
        warmup_samples_ns=tuple(warmups),
        samples_ns=tuple(samples),
        failures=tuple(failures),
        statistics=calculate_statistics(tuple(samples)),
        memory=MemoryMeasurements(
            python_retained_bytes=_maximum(
                tuple(value.python_retained_bytes for value in memory_values)
            ),
            python_peak_bytes=_maximum(tuple(value.python_peak_bytes for value in memory_values)),
            process_rss_bytes=_maximum(tuple(value.process_rss_bytes for value in memory_values)),
        ),
        correctness_checksum=correctness_checksum,
    )


async def _seed_workspace(
    factory: WorkspaceFactory,
    profile: WorkloadProfile,
) -> tuple[WorkspaceBenchmarkPort, WorkspaceStats]:
    workspace = factory()
    files = profile.files()
    await _write_profile_files(workspace, files)
    stats = await workspace.stats()
    _require_profile_stats(profile, stats)
    return workspace, stats


async def _write_profile_files(
    workspace: WorkspaceBenchmarkPort,
    files: tuple[ValidationFile, ...],
) -> None:
    for file in files:
        await workspace.write(
            WorkspaceWriteRequest(
                path=workspace.resolve_path(file.path),
                content=file.content,
                precondition=AnyCurrentState(),
                create_parents=True,
            )
        )


def _require_profile_stats(profile: WorkloadProfile, stats: WorkspaceStats) -> None:
    if stats.total_bytes != profile.dimensions.total_bytes:
        raise RuntimeError(
            f"profile {profile.name} produced {stats.total_bytes} bytes; "
            f"expected {profile.dimensions.total_bytes}"
        )
    expected_nodes = _profile_node_count(profile)
    if stats.node_count != expected_nodes:
        raise RuntimeError(
            f"profile {profile.name} produced {stats.node_count} nodes; expected {expected_nodes}"
        )


def _profile_node_count(profile: WorkloadProfile) -> int:
    return 1 + profile.directory_count + profile.file_count


def _operation_dimensions(
    profile: WorkloadProfile,
    *,
    file_count: int,
    byte_count: int,
) -> WorkloadDimensions:
    return replace(
        profile.dimensions,
        operation_file_count=file_count,
        operation_bytes=byte_count,
    )


def _workspace_checksum(stats: WorkspaceStats) -> str:
    return _checksum(
        (
            stats.total_bytes,
            stats.node_count,
            stats.revision.value,
            stats.root_hash.value,
        )
    )


async def _create_elapsed(
    factory: DriverFactory,
    profile: WorkloadProfile,
) -> tuple[int, str]:
    driver = factory()
    request = profile.create_request("benchmark-overhead")
    started = time.perf_counter_ns()
    session = await driver.create(request)
    elapsed = time.perf_counter_ns() - started
    state = await session.state()
    await driver.delete(session)
    await driver.close()
    return elapsed, _checksum((state.revision, state.cwd, state.approved_environment))


async def _cleanup_driver_sessions(
    driver: ProductValidationDriver,
    sessions: tuple[ValidationSession, ...],
) -> tuple[BaseException, ...]:
    async def cleanup() -> tuple[BaseException, ...]:
        results = await asyncio.gather(
            *(driver.delete(session) for session in sessions),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        try:
            await driver.close()
        except BaseException as error:
            errors.append(error)
        return tuple(errors)

    cleanup_task = asyncio.create_task(cleanup())
    try:
        errors = await asyncio.shield(cleanup_task)
    except asyncio.CancelledError as cancellation:
        errors = await cleanup_task
        for error in errors:
            cancellation.add_note(f"secondary benchmark cleanup failure: {error}")
        raise
    return errors


def _maximum(values: tuple[int | None, ...]) -> int | None:
    present = tuple(value for value in values if value is not None)
    return max(present) if present else None


def _checksum(value: object) -> str:
    return sha256(
        json.dumps(value, default=str, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _run_cold_worker(driver_name: str, profile_name: str) -> tuple[int, dict[str, object]]:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.cold_worker",
            driver_name,
            profile_name,
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=_COLD_WORKER_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "cold worker failed")
    payload: object = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise TypeError("cold worker returned a non-object result")
    typed_payload = cast(dict[str, object], payload)
    ready_ns = typed_payload.get("ready_ns")
    if isinstance(ready_ns, bool) or not isinstance(ready_ns, int):
        raise TypeError("cold worker did not return an integer ready timestamp")
    return ready_ns - started, typed_payload
