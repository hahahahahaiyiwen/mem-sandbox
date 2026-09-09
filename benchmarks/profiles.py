"""Versioned deterministic benchmark workspace profiles."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from benchmarks.validation import ValidationCreateRequest, ValidationFile


@dataclass(frozen=True, slots=True)
class WorkloadDimensions:
    file_count: int
    directory_count: int
    total_bytes: int
    largest_file_bytes: int
    max_depth: int
    operation_file_count: int = 0
    operation_bytes: int = 0
    snapshot_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class WorkloadProfile:
    name: str
    version: int
    directory_count: int
    file_count: int
    file_bytes: int
    dimensions: WorkloadDimensions

    def create_request(self, owner_id: str) -> ValidationCreateRequest:
        return ValidationCreateRequest(owner_id=owner_id, files=self.files())

    def files(self) -> tuple[ValidationFile, ...]:
        return tuple(self.file(index) for index in range(self.file_count))

    def file(self, index: int, *, variant: str = "seed") -> ValidationFile:
        if index < 0 or index >= self.file_count:
            raise IndexError(f"file index {index} is outside profile {self.name}")
        if not variant:
            raise ValueError("content variant must not be empty")
        return ValidationFile(
            path=self.file_path(index),
            content=_content(self.name, self.version, variant, index, self.file_bytes),
        )

    def file_path(self, index: int) -> str:
        if index < 0 or index >= self.file_count:
            raise IndexError(f"file index {index} is outside profile {self.name}")
        return f"/workspace/dir-{index % self.directory_count:02d}/file-{index:04d}.txt"

    def directory_path(self, index: int) -> str:
        if index < 0 or index >= self.directory_count:
            raise IndexError(f"directory index {index} is outside profile {self.name}")
        return f"/workspace/dir-{index:02d}"


def load_profile(name: str) -> WorkloadProfile:
    try:
        return PROFILES[name]
    except KeyError as error:
        raise ValueError(f"unknown benchmark profile: {name}") from error


def _profile(
    name: str,
    *,
    directory_count: int,
    file_count: int,
    file_bytes: int,
) -> WorkloadProfile:
    if file_count == 0:
        if directory_count != 0 or file_bytes != 0:
            raise ValueError("empty profiles must have zero directories and file bytes")
        return WorkloadProfile(name, 1, 0, 0, 0, WorkloadDimensions(0, 0, 0, 0, 0))
    if directory_count <= 0:
        raise ValueError("non-empty profiles must have at least one directory")
    if file_bytes <= 0:
        raise ValueError("non-empty profiles must have positive file bytes")
    return WorkloadProfile(
        name=name,
        version=1,
        directory_count=directory_count,
        file_count=file_count,
        file_bytes=file_bytes,
        dimensions=WorkloadDimensions(
            file_count=file_count,
            directory_count=directory_count,
            total_bytes=file_count * file_bytes,
            largest_file_bytes=file_bytes,
            max_depth=2,
        ),
    )


def _content(profile: str, version: int, variant: str, index: int, size: int) -> bytes:
    identity = (
        f"{profile}:{index}" if variant == "seed" else f"{profile}:{version}:{variant}:{index}"
    )
    seed = sha256(identity.encode("ascii")).hexdigest().encode("ascii")
    return (seed * ((size + len(seed) - 1) // len(seed)))[:size]


PROFILES = {
    "empty": _profile("empty", directory_count=0, file_count=0, file_bytes=0),
    "small_project": _profile(
        "small_project",
        directory_count=4,
        file_count=16,
        file_bytes=1024,
    ),
    "active_project": _profile(
        "active_project",
        directory_count=16,
        file_count=128,
        file_bytes=4096,
    ),
    "quota_edge": _profile(
        "quota_edge",
        directory_count=32,
        file_count=256,
        file_bytes=32 * 1024,
    ),
    "content_heavy": _profile(
        "content_heavy",
        directory_count=16,
        file_count=128,
        file_bytes=64 * 1024,
    ),
    "node_heavy": _profile(
        "node_heavy",
        directory_count=64,
        file_count=512,
        file_bytes=1024,
    ),
}

SCALABILITY_PROFILE_NAMES = (
    "active_project",
    "quota_edge",
    "content_heavy",
    "node_heavy",
)
