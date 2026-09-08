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


@dataclass(frozen=True, slots=True)
class WorkloadProfile:
    name: str
    version: int
    files: tuple[ValidationFile, ...]
    dimensions: WorkloadDimensions

    def create_request(self, owner_id: str) -> ValidationCreateRequest:
        return ValidationCreateRequest(owner_id=owner_id, files=self.files)


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
        return WorkloadProfile(name, 1, (), WorkloadDimensions(0, 0, 0, 0, 0))
    files = tuple(
        ValidationFile(
            path=(f"/workspace/dir-{index % directory_count:02d}/file-{index:04d}.txt"),
            content=_content(name, index, file_bytes),
        )
        for index in range(file_count)
    )
    return WorkloadProfile(
        name=name,
        version=1,
        files=files,
        dimensions=WorkloadDimensions(
            file_count=file_count,
            directory_count=directory_count,
            total_bytes=file_count * file_bytes,
            largest_file_bytes=file_bytes,
            max_depth=2,
        ),
    )


def _content(profile: str, index: int, size: int) -> bytes:
    seed = sha256(f"{profile}:{index}".encode("ascii")).hexdigest().encode("ascii")
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
}
