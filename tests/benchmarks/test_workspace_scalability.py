from __future__ import annotations

import pytest
from benchmarks.cases import (
    CaseConfig,
    WorkspaceFactory,
    measure_workspace_overwrite,
)
from benchmarks.profiles import SCALABILITY_PROFILE_NAMES, load_profile
from benchmarks.runner import run_workspace_scalability_suite
from benchmarks.schema import SCHEMA_VERSION

from mem_sandbox.core import Revision
from mem_sandbox.workspace import (
    ContentHash,
    CopyPathRequest,
    SandboxPath,
    WorkspaceBinaryResult,
    WorkspaceMutation,
    WorkspaceSnapshotData,
    WorkspaceStats,
    WorkspaceWriteRequest,
)


class RecordingWorkspace:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._files: dict[str, bytes] = {}
        self._revision = 0

    def resolve_path(
        self,
        value: str,
        *,
        cwd: SandboxPath | None = None,
    ) -> SandboxPath:
        return SandboxPath.resolve(value, cwd=cwd)

    async def stats(self) -> WorkspaceStats:
        return self._stats()

    async def read_bytes(self, path: SandboxPath) -> WorkspaceBinaryResult:
        self._events.append("read")
        content = self._files[path.value]
        return WorkspaceBinaryResult(
            path=path,
            content=content,
            content_hash=ContentHash.from_bytes(content),
            revision=Revision(self._revision),
        )

    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation:
        self._events.append("seed" if request.create_parents else "overwrite")
        previous = self._files.get(request.path.value)
        self._files[request.path.value] = request.content
        self._revision += 1
        return WorkspaceMutation(
            path=request.path,
            created=previous is None,
            changed=True,
            previous_hash=ContentHash.from_bytes(previous) if previous is not None else None,
            current_hash=ContentHash.from_bytes(request.content),
            stats=self._stats(),
        )

    async def copy(self, request: CopyPathRequest) -> WorkspaceMutation:
        self._events.append("copy")
        source_prefix = request.source.value + "/"
        copied = {
            request.destination.value + path.removeprefix(request.source.value): content
            for path, content in self._files.items()
            if path.startswith(source_prefix)
        }
        self._files.update(copied)
        self._revision += 1
        stats = self._stats()
        return WorkspaceMutation(
            path=request.destination,
            created=True,
            changed=True,
            previous_hash=None,
            current_hash=stats.root_hash,
            stats=stats,
        )

    async def export(self) -> WorkspaceSnapshotData:
        self._events.append("snapshot")
        encoded = b"\n".join(
            path.encode("utf-8") + b"\0" + content for path, content in sorted(self._files.items())
        )
        stats = self._stats()
        return WorkspaceSnapshotData(
            encoded=encoded,
            schema_version=1,
            integrity_hash=ContentHash.from_bytes(encoded),
            workspace_revision=stats.revision,
            root_hash=stats.root_hash,
        )

    def _stats(self) -> WorkspaceStats:
        directories = {SandboxPath.root().value}
        for value in self._files:
            parent = SandboxPath.resolve(value).parent
            while True:
                directories.add(parent.value)
                if parent.is_root:
                    break
                parent = parent.parent
        identity = b"\n".join(
            path.encode("utf-8") + b"\0" + content for path, content in sorted(self._files.items())
        )
        return WorkspaceStats(
            total_bytes=sum(len(content) for content in self._files.values()),
            node_count=len(directories) + len(self._files),
            revision=Revision(self._revision),
            root_hash=ContentHash.from_bytes(identity),
        )


def _factory(events: list[str]) -> WorkspaceFactory:
    return WorkspaceFactory("recording_workspace", lambda: RecordingWorkspace(events))


def test_scalability_profiles_control_bytes_and_nodes_independently() -> None:
    active = load_profile("active_project")
    content_heavy = load_profile("content_heavy")
    node_heavy = load_profile("node_heavy")

    assert content_heavy.dimensions.file_count == active.dimensions.file_count
    assert content_heavy.dimensions.directory_count == active.dimensions.directory_count
    assert content_heavy.dimensions.total_bytes == active.dimensions.total_bytes * 16
    assert node_heavy.dimensions.total_bytes == active.dimensions.total_bytes
    assert node_heavy.dimensions.file_count == active.dimensions.file_count * 4
    assert node_heavy.dimensions.directory_count == active.dimensions.directory_count * 4

    seeded = active.file(64)
    replacement = active.file(64, variant="overwrite")
    assert seeded.path == replacement.path
    assert len(seeded.content) == len(replacement.content) == 4096
    assert seeded.content != replacement.content
    assert active.create_request("first") == active.create_request("first")
    assert SCALABILITY_PROFILE_NAMES == (
        "active_project",
        "quota_edge",
        "content_heavy",
        "node_heavy",
    )


@pytest.mark.asyncio
async def test_workspace_overwrite_times_only_the_committed_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    timestamps = iter((100, 160))

    def read_clock() -> int:
        events.append("clock")
        return next(timestamps)

    monkeypatch.setattr("benchmarks.cases.time.perf_counter_ns", read_clock)

    result = await measure_workspace_overwrite(
        _factory(events),
        load_profile("small_project"),
        CaseConfig(warmups=0, samples=1),
    )

    assert result.case == "workspace_overwrite_hot"
    assert result.driver == "recording_workspace"
    assert result.samples_ns == (60,)
    assert result.dimensions.operation_file_count == 1
    assert result.dimensions.operation_bytes == 1024
    assert events[-3:] == ["clock", "overwrite", "clock"]


@pytest.mark.asyncio
async def test_scalability_suite_records_operation_and_snapshot_dimensions() -> None:
    artifact = await run_workspace_scalability_suite(
        CaseConfig(warmups=0, samples=1),
        profiles=(load_profile("small_project"),),
        factory=_factory([]),
    )

    assert artifact.schema_version == SCHEMA_VERSION == 2
    assert {case.case for case in artifact.cases} == {
        "workspace_seed",
        "workspace_read_hot",
        "workspace_overwrite_hot",
        "workspace_copy_directory",
        "workspace_snapshot_encode",
        "workspace_retained_memory",
    }
    assert all(case.statistics.count == 1 for case in artifact.cases)
    assert all(not case.failures for case in artifact.cases)
    assert all(case.correctness_checksum for case in artifact.cases)

    snapshot = next(case for case in artifact.cases if case.case == "workspace_snapshot_encode")
    assert snapshot.dimensions.operation_file_count == 16
    assert snapshot.dimensions.operation_bytes == 16 * 1024
    assert snapshot.dimensions.snapshot_bytes is not None
    assert snapshot.dimensions.snapshot_bytes > snapshot.dimensions.total_bytes

    memory = next(case for case in artifact.cases if case.case == "workspace_retained_memory")
    assert memory.memory.python_retained_bytes is not None
    assert memory.memory.python_retained_bytes > 0
