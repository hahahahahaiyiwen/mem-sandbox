"""Versioned machine-readable benchmark result schema."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from benchmarks.profiles import WorkloadDimensions

SCHEMA_VERSION = 1
type TimingMode = Literal["warm", "cold_process", "paired"]


@dataclass(frozen=True, slots=True)
class BenchmarkFailure:
    category: str
    message: str


@dataclass(frozen=True, slots=True)
class SampleStatistics:
    count: int
    minimum_ns: int | None
    median_ns: float | None
    p95_ns: int | None
    p99_ns: int | None
    mean_ns: float | None
    standard_deviation_ns: float | None


@dataclass(frozen=True, slots=True)
class MemoryMeasurements:
    python_peak_bytes: int | None = None
    process_rss_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkCaseResult:
    case: str
    driver: str
    profile: str
    profile_version: int
    timing_mode: TimingMode
    dimensions: WorkloadDimensions
    warmup_samples_ns: tuple[int, ...]
    samples_ns: tuple[int, ...]
    failures: tuple[BenchmarkFailure, ...]
    statistics: SampleStatistics
    memory: MemoryMeasurements
    correctness_checksum: str


@dataclass(frozen=True, slots=True)
class EnvironmentFingerprint:
    python_implementation: str
    python_version: str
    operating_system: str
    architecture: str
    cpu_model: str
    logical_cpu_count: int | None
    available_memory_bytes: int | None
    runner_identity: str | None
    power_configuration: str | None
    garbage_collector_enabled: bool
    network_involved: bool
    external_provider_involved: bool


@dataclass(frozen=True, slots=True)
class BenchmarkRunMetadata:
    source_commit: str | None
    source_tree_hash: str
    working_tree_dirty: bool
    package_version: str
    framework_versions: dict[str, str]
    started_at: str
    tier: str
    process_count: int
    concurrency: int
    warmups: int
    samples: int
    minimum_case_duration_seconds: float


@dataclass(frozen=True, slots=True)
class BenchmarkRunArtifact:
    schema_version: int
    metadata: BenchmarkRunMetadata
    environment: EnvironmentFingerprint
    cases: tuple[BenchmarkCaseResult, ...]

    def to_json(self) -> str:
        return json.dumps(
            dataclasses.asdict(self),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )


def calculate_statistics(samples: tuple[int, ...]) -> SampleStatistics:
    if not samples:
        return SampleStatistics(0, None, None, None, None, None, None)
    return SampleStatistics(
        count=len(samples),
        minimum_ns=min(samples),
        median_ns=statistics.median(samples),
        p95_ns=_nearest_rank(samples, 0.95) if len(samples) >= 100 else None,
        p99_ns=_nearest_rank(samples, 0.99) if len(samples) >= 1000 else None,
        mean_ns=statistics.mean(samples),
        standard_deviation_ns=statistics.pstdev(samples),
    )


def consistent_correctness_checksum(checksums: tuple[str, ...]) -> str:
    if not checksums:
        return ""
    expected = checksums[0]
    if any(checksum != expected for checksum in checksums[1:]):
        raise ValueError("successful samples produced different correctness checksums")
    return expected


def build_environment_fingerprint() -> EnvironmentFingerprint:
    import gc

    return EnvironmentFingerprint(
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        operating_system=platform.platform(),
        architecture=platform.machine(),
        cpu_model=platform.processor() or platform.machine(),
        logical_cpu_count=os.cpu_count(),
        available_memory_bytes=_available_memory_bytes(),
        runner_identity=os.environ.get("MEM_SANDBOX_BENCHMARK_RUNNER"),
        power_configuration=os.environ.get("MEM_SANDBOX_POWER_CONFIGURATION"),
        garbage_collector_enabled=gc.isenabled(),
        network_involved=False,
        external_provider_involved=False,
    )


def build_run_metadata(
    *,
    tier: str,
    warmups: int,
    samples: int,
    minimum_case_duration_seconds: float,
    process_count: int = 1,
    concurrency: int = 1,
) -> BenchmarkRunMetadata:
    versions = {"openai-agents": _distribution_version("openai-agents")}
    return BenchmarkRunMetadata(
        source_commit=_source_commit(),
        source_tree_hash=_source_tree_hash(),
        working_tree_dirty=_working_tree_dirty(),
        package_version=_distribution_version("mem-sandbox"),
        framework_versions=versions,
        started_at=datetime.now(UTC).isoformat(),
        tier=tier,
        process_count=process_count,
        concurrency=concurrency,
        warmups=warmups,
        samples=samples,
        minimum_case_duration_seconds=minimum_case_duration_seconds,
    )


def write_artifact(path: Path, artifact: BenchmarkRunArtifact) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.to_json() + "\n", encoding="utf-8", newline="\n")


def _nearest_rank(samples: tuple[int, ...], quantile: float) -> int:
    ordered = sorted(samples)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _source_commit() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _working_tree_dirty() -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode != 0 or bool(completed.stdout.strip())


def _source_tree_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    candidates = [
        *(root / "benchmarks").rglob("*.py"),
        *(root / "src").rglob("*.py"),
        root / "pyproject.toml",
        root / "uv.lock",
    ]
    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        if "benchmarks/results" in path.relative_to(root).as_posix():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _available_memory_bytes() -> int | None:
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # pyright: ignore[reportAttributeAccessIssue]
            return int(status.available_physical)
        return None
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None
    return int(pages * page_size)
