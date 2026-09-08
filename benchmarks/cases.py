"""Provisioning, lifecycle, memory, and adapter-overhead benchmark cases."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from benchmarks.drivers import (
    DirectValidationDriver,
    OpenAICapabilityValidationDriver,
    OpenAISandboxValidationDriver,
)
from benchmarks.profiles import WorkloadProfile
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
    ValidationSession,
    run_stateful_reference_scenario,
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


async def measure_create_ready(
    factory: DriverFactory,
    profile: WorkloadProfile,
    config: CaseConfig,
    *,
    case: str,
) -> BenchmarkCaseResult:
    async def sample() -> tuple[int, str, MemoryMeasurements]:
        driver = factory()
        started = time.perf_counter_ns()
        session = await driver.create(profile.create_request("benchmark-create"))
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
        started = time.perf_counter_ns()
        session = await driver.create(profile.create_request("benchmark-first-operation"))
        if profile.files:
            outcome = await session.read_file(profile.files[0].path)
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
            if profile.files:
                outcome: object = await resumed.read_file(profile.files[0].path)
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
        try:
            started = time.perf_counter_ns()
            outcomes = await asyncio.gather(
                *(
                    driver.create(profile.create_request(f"benchmark-burst-{index}"))
                    for index in range(config.burst_concurrency)
                ),
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
        driver = factory()
        sessions: list[ValidationSession] = []
        session_count = config.burst_concurrency if profile.name == "empty" else 1
        tracemalloc.start()
        try:
            started = time.perf_counter_ns()
            outcomes = await asyncio.gather(
                *(
                    driver.create(profile.create_request(f"benchmark-memory-{index}"))
                    for index in range(session_count)
                ),
                return_exceptions=True,
            )
            elapsed = time.perf_counter_ns() - started
            _, peak = tracemalloc.get_traced_memory()
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
                MemoryMeasurements(python_peak_bytes=peak),
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
        dimensions=profile.dimensions,
        warmup_samples_ns=tuple(warmups),
        samples_ns=tuple(samples),
        failures=tuple(failures),
        statistics=calculate_statistics(tuple(samples)),
        memory=MemoryMeasurements(
            python_peak_bytes=_maximum(tuple(value.python_peak_bytes for value in memory_values)),
            process_rss_bytes=_maximum(tuple(value.process_rss_bytes for value in memory_values)),
        ),
        correctness_checksum=correctness_checksum,
    )


async def _create_elapsed(
    factory: DriverFactory,
    profile: WorkloadProfile,
) -> tuple[int, str]:
    driver = factory()
    started = time.perf_counter_ns()
    session = await driver.create(profile.create_request("benchmark-overhead"))
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
