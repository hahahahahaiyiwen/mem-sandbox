from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import (
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType]
)
from tests.property.async_machine import AsyncRuleBasedStateMachine
from tests.property.strategies import binary_payloads

from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    ContentHashMustEqual,
    CopyPathRequest,
    DestinationWithinSourceError,
    ExpectedFileHash,
    FileSizeLimitExceededError,
    MakeDirectoryRequest,
    MemoryWorkspace,
    MovePathRequest,
    NodeKind,
    NodeLimitExceededError,
    NotADirectoryError,
    NotAFileError,
    PathAlreadyExistsError,
    RemovePathRequest,
    SandboxPath,
    StaleContentError,
    WorkspaceAppendRequest,
    WorkspaceLimits,
    WorkspacePatchRequest,
    WorkspaceSizeLimitExceededError,
    WorkspaceWriteRequest,
)

PathParts = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublicRecord:
    path: str
    kind: str
    size_bytes: int
    content_hash: str
    revision: int
    content: bytes | None


@dataclass(frozen=True, slots=True)
class PublicProjection:
    total_bytes: int
    node_count: int
    revision: int
    root_hash: str
    records: tuple[PublicRecord, ...]


DIRECTORY_PATHS: tuple[PathParts, ...] = (
    ("alpha",),
    ("alpha", "nested"),
    ("beta",),
    ("beta", "nested"),
)
FILE_PATHS: tuple[PathParts, ...] = (
    ("first",),
    ("second",),
    ("alpha", "first"),
    ("alpha", "nested", "second"),
    ("beta", "first"),
)
TRANSFER_DESTINATIONS: tuple[PathParts, ...] = (
    ("first",),
    ("second",),
    ("alpha",),
    ("beta",),
    ("copied",),
    ("moved",),
    ("gamma", "nested"),
    ("alpha", "copy"),
    ("beta", "nested", "copy"),
)


def _path(parts: PathParts) -> SandboxPath:
    return SandboxPath.resolve(f"/workspace/{'/'.join(parts)}")


def _prefixes(parts: PathParts) -> tuple[PathParts, ...]:
    return tuple(parts[:index] for index in range(1, len(parts) + 1))


def _contains(parent: PathParts, child: PathParts) -> bool:
    return len(child) >= len(parent) and child[: len(parent)] == parent


def _overlaps(first: PathParts, second: PathParts) -> bool:
    return _contains(first, second) or _contains(second, first)


def _remove_subtree(
    directories: set[PathParts],
    files: dict[PathParts, bytes],
    root: PathParts,
) -> None:
    directories.difference_update(
        path for path in tuple(directories) if path and _contains(root, path)
    )
    for path in tuple(files):
        if _contains(root, path):
            del files[path]


def _transfer_model(
    directories: set[PathParts],
    files: dict[PathParts, bytes],
    source: PathParts,
    destination: PathParts,
    *,
    copy_source: bool,
) -> tuple[set[PathParts], dict[PathParts, bytes]]:
    next_directories = set(directories)
    next_files = dict(files)
    source_directories = {path for path in directories if path and _contains(source, path)}
    source_files = {path: content for path, content in files.items() if _contains(source, path)}

    _remove_subtree(next_directories, next_files, destination)
    if not copy_source:
        _remove_subtree(next_directories, next_files, source)

    next_directories.update(_prefixes(destination)[:-1])
    next_directories.update(destination + path[len(source) :] for path in source_directories)
    next_files.update(
        {destination + path[len(source) :]: content for path, content in source_files.items()}
    )
    return next_directories, next_files


def _patchable_line(content: bytes) -> str | None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.endswith("\n") or text.count("\n") != 1:
        return None
    line = text[:-1]
    return line if line.isascii() and line.isalpha() else None


def _model_hash(
    path: PathParts,
    directories: set[PathParts],
    files: dict[PathParts, bytes],
) -> ContentHash:
    if path in files:
        return ContentHash.from_bytes(files[path])

    children = sorted(
        child
        for child in directories | set(files)
        if len(child) == len(path) + 1 and child[: len(path)] == path
    )
    digest = sha256()
    digest.update(b"mem-sandbox-directory-v1\x00")
    for child in children:
        name_bytes = child[-1].encode("utf-8")
        digest.update(b"F" if child in files else b"D")
        digest.update(len(name_bytes).to_bytes(4, "big"))
        digest.update(name_bytes)
        digest.update(bytes.fromhex(_model_hash(child, directories, files).value))
    return ContentHash(digest.hexdigest())


async def _public_projection(workspace: MemoryWorkspace) -> PublicProjection:
    stats = await workspace.stats()
    records: list[PublicRecord] = []

    async def visit(directory: SandboxPath) -> None:
        entries = await workspace.list(directory)
        assert [entry.path.name for entry in entries] == sorted(
            entry.path.name for entry in entries
        )
        for entry in entries:
            content = (
                (await workspace.read_bytes(entry.path)).content
                if entry.kind is NodeKind.FILE
                else None
            )
            records.append(
                PublicRecord(
                    path=entry.path.value,
                    kind=entry.kind.value,
                    size_bytes=entry.size_bytes,
                    content_hash=entry.content_hash.value,
                    revision=entry.revision.value,
                    content=content,
                )
            )
            if entry.kind is NodeKind.DIRECTORY:
                await visit(entry.path)

    await visit(SandboxPath.root())
    return PublicProjection(
        total_bytes=stats.total_bytes,
        node_count=stats.node_count,
        revision=stats.revision.value,
        root_hash=stats.root_hash.value,
        records=tuple(records),
    )


class MemoryWorkspaceStateMachine(AsyncRuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.workspace = MemoryWorkspace(
            WorkspaceLimits(
                max_file_bytes=8,
                max_total_bytes=16,
                max_nodes=8,
                max_path_bytes=64,
                max_segment_bytes=16,
                max_read_bytes=16,
                max_read_lines=8,
                max_patch_bytes=64,
                max_snapshot_bytes=4_096,
            )
        )
        patch_path = ("patchable",)
        self.run_async(
            self.workspace.write(
                WorkspaceWriteRequest(
                    _path(patch_path),
                    b"old\n",
                    AnyCurrentState(),
                )
            )
        )
        self.directories: set[PathParts] = {()}
        self.files: dict[PathParts, bytes] = {patch_path: b"old\n"}
        self.revision = 1

    def _node_count(self) -> int:
        return len(self.directories) + len(self.files)

    def _total_bytes(self) -> int:
        return sum(len(content) for content in self.files.values())

    def _has_file_ancestor(self, parts: PathParts) -> bool:
        return any(prefix in self.files for prefix in _prefixes(parts)[:-1])

    def _assert_atomic_failure(
        self,
        operation: Coroutine[Any, Any, object],
        error_type: type[BaseException],
    ) -> None:
        before = self.run_async(self.workspace.export())
        with pytest.raises(error_type):
            self.run_async(operation)
        assert self.run_async(self.workspace.export()) == before

    @rule(index=st.integers(min_value=0, max_value=len(DIRECTORY_PATHS) - 1))
    def mkdir(self, index: int) -> None:
        parts = DIRECTORY_PATHS[index]
        operation = self.workspace.mkdir(MakeDirectoryRequest(_path(parts), create_parents=True))
        if parts in self.directories or parts in self.files:
            self._assert_atomic_failure(operation, PathAlreadyExistsError)
            return
        if self._has_file_ancestor(parts):
            self._assert_atomic_failure(operation, NotADirectoryError)
            return

        additions = tuple(prefix for prefix in _prefixes(parts) if prefix not in self.directories)
        if self._node_count() + len(additions) > self.workspace.limits.max_nodes:
            self._assert_atomic_failure(operation, NodeLimitExceededError)
            return

        mutation = self.run_async(operation)
        self.directories.update(additions)
        self.revision += 1
        assert mutation.changed
        assert mutation.stats.revision.value == self.revision

    @rule(
        index=st.integers(min_value=0, max_value=len(FILE_PATHS) - 1),
        content=binary_payloads(max_size=9),
    )
    def write(self, index: int, content: bytes) -> None:
        parts = FILE_PATHS[index]
        operation = self.workspace.write(
            WorkspaceWriteRequest(
                _path(parts),
                content,
                AnyCurrentState(),
                create_parents=True,
            )
        )
        if len(content) > self.workspace.limits.max_file_bytes:
            self._assert_atomic_failure(operation, FileSizeLimitExceededError)
            return
        if self._has_file_ancestor(parts):
            self._assert_atomic_failure(operation, NotADirectoryError)
            return
        if parts in self.directories:
            self._assert_atomic_failure(operation, NotAFileError)
            return

        parent_additions = tuple(
            prefix for prefix in _prefixes(parts)[:-1] if prefix not in self.directories
        )
        added_file = parts not in self.files
        prospective_nodes = self._node_count() + len(parent_additions) + int(added_file)
        prospective_bytes = self._total_bytes() - len(self.files.get(parts, b"")) + len(content)
        if prospective_bytes > self.workspace.limits.max_total_bytes:
            self._assert_atomic_failure(operation, WorkspaceSizeLimitExceededError)
            return
        if prospective_nodes > self.workspace.limits.max_nodes:
            self._assert_atomic_failure(operation, NodeLimitExceededError)
            return

        mutation = self.run_async(operation)
        self.directories.update(parent_additions)
        self.files[parts] = content
        self.revision += 1
        assert mutation.changed
        assert mutation.stats.revision.value == self.revision

    @precondition(lambda self: bool(self.files))
    @rule(content=binary_payloads(max_size=9))
    def append(self, content: bytes) -> None:
        parts = sorted(self.files)[0]
        previous = self.files[parts]
        operation = self.workspace.append(
            WorkspaceAppendRequest(_path(parts), content, AnyCurrentState())
        )
        combined = previous + content
        if len(combined) > self.workspace.limits.max_file_bytes:
            self._assert_atomic_failure(operation, FileSizeLimitExceededError)
            return
        prospective_bytes = self._total_bytes() + len(content)
        if prospective_bytes > self.workspace.limits.max_total_bytes:
            self._assert_atomic_failure(operation, WorkspaceSizeLimitExceededError)
            return

        mutation = self.run_async(operation)
        self.files[parts] = combined
        self.revision += 1
        assert mutation.stats.revision.value == self.revision

    @precondition(lambda self: self._node_count() > 1)
    @rule(
        operation_kind=st.sampled_from(("copy", "move")),
        source_index=st.integers(min_value=0, max_value=8),
        destination_index=st.integers(min_value=0, max_value=8),
        overwrite=st.booleans(),
    )
    def transfer(
        self,
        operation_kind: str,
        source_index: int,
        destination_index: int,
        overwrite: bool,
    ) -> None:
        sources = sorted((self.directories - {()}) | set(self.files))
        source = sources[source_index % len(sources)]
        destinations = tuple(
            destination
            for destination in TRANSFER_DESTINATIONS
            if not _overlaps(source, destination)
        )
        if not destinations:
            return
        destination = destinations[destination_index % len(destinations)]
        source_hash = _model_hash(source, self.directories, self.files)
        previous_hash = (
            _model_hash(destination, self.directories, self.files)
            if destination in self.directories or destination in self.files
            else None
        )
        operation = (
            self.workspace.copy(
                CopyPathRequest(
                    _path(source),
                    _path(destination),
                    overwrite=overwrite,
                    create_parents=True,
                )
            )
            if operation_kind == "copy"
            else self.workspace.move(
                MovePathRequest(
                    _path(source),
                    _path(destination),
                    overwrite=overwrite,
                    create_parents=True,
                )
            )
        )

        destination_exists = destination in self.directories or destination in self.files
        if destination_exists and not overwrite:
            self._assert_atomic_failure(operation, PathAlreadyExistsError)
            return
        if self._has_file_ancestor(destination):
            self._assert_atomic_failure(operation, NotADirectoryError)
            return

        next_directories, next_files = _transfer_model(
            self.directories,
            self.files,
            source,
            destination,
            copy_source=operation_kind == "copy",
        )
        prospective_bytes = sum(len(content) for content in next_files.values())
        prospective_nodes = len(next_directories) + len(next_files)
        if prospective_bytes > self.workspace.limits.max_total_bytes:
            self._assert_atomic_failure(operation, WorkspaceSizeLimitExceededError)
            return
        if prospective_nodes > self.workspace.limits.max_nodes:
            self._assert_atomic_failure(operation, NodeLimitExceededError)
            return

        mutation = self.run_async(operation)
        self.directories = next_directories
        self.files = next_files
        self.revision += 1
        assert mutation.changed
        assert mutation.created is (previous_hash is None)
        assert mutation.previous_hash == previous_hash
        assert mutation.current_hash == source_hash
        assert mutation.stats.revision.value == self.revision

    @precondition(lambda self: bool(self.directories - {()}))
    @rule()
    def overlapping_transfer_is_atomic(self) -> None:
        source = sorted(self.directories - {()})[0]
        descendant = (*source, "inside")
        self._assert_atomic_failure(
            self.workspace.copy(
                CopyPathRequest(_path(source), _path(descendant), create_parents=True)
            ),
            DestinationWithinSourceError,
        )

    @precondition(lambda self: _patchable_line(self.files.get(("patchable",), b"")) is not None)
    @rule(replacement=st.sampled_from(("new", "x", "é")))
    def patch_text_file(self, replacement: str) -> None:
        parts = ("patchable",)
        previous = self.files[parts]
        old_line = _patchable_line(previous)
        assert old_line is not None
        if old_line == replacement:
            return
        patch = f"--- a/{parts[0]}\n+++ b/{parts[0]}\n@@ -1 +1 @@\n-{old_line}\n+{replacement}\n"
        next_content = f"{replacement}\n".encode()
        prospective_bytes = self._total_bytes() - len(previous) + len(next_content)
        operation = self.workspace.patch(
            WorkspacePatchRequest(
                patch,
                (ExpectedFileHash(_path(parts), ContentHash.from_bytes(previous)),),
            )
        )
        if prospective_bytes > self.workspace.limits.max_total_bytes:
            self._assert_atomic_failure(operation, WorkspaceSizeLimitExceededError)
            return

        result = self.run_async(operation)
        self.files[parts] = next_content
        self.revision += 1
        assert result.stats.revision.value == self.revision
        assert result.files[0].previous_hash == ContentHash.from_bytes(previous)
        assert result.files[0].current_hash == ContentHash.from_bytes(next_content)

    @rule(
        kind=st.sampled_from(("directory", "file")),
        index=st.integers(min_value=0, max_value=4),
    )
    def remove_if_present(self, kind: str, index: int) -> None:
        candidates = sorted(self.directories - {()}) if kind == "directory" else sorted(self.files)
        if not candidates:
            return
        parts = candidates[index % len(candidates)]
        mutation = self.run_async(
            self.workspace.remove(RemovePathRequest(_path(parts), recursive=True))
        )
        self.files = {
            path: content
            for path, content in self.files.items()
            if path != parts and path[: len(parts)] != parts
        }
        self.directories = {
            path
            for path in self.directories
            if path == () or (path != parts and path[: len(parts)] != parts)
        }
        self.revision += 1
        assert mutation.changed
        assert mutation.stats.revision.value == self.revision

    @rule()
    def missing_remove_is_unchanged(self) -> None:
        before = self.run_async(_public_projection(self.workspace))
        mutation = self.run_async(
            self.workspace.remove(
                RemovePathRequest(
                    SandboxPath.resolve("/workspace/always-missing"),
                    missing_ok=True,
                )
            )
        )
        assert not mutation.changed
        assert self.run_async(_public_projection(self.workspace)) == before

    @precondition(lambda self: bool(self.files))
    @rule()
    def stale_write_is_atomic(self) -> None:
        parts = sorted(self.files)[0]
        stale_hash = ContentHash.from_bytes(self.files[parts] + b"\x00")
        self._assert_atomic_failure(
            self.workspace.write(
                WorkspaceWriteRequest(
                    _path(parts),
                    b"stale",
                    ContentHashMustEqual(stale_hash),
                )
            ),
            StaleContentError,
        )

    @rule()
    def oversized_write_is_atomic(self) -> None:
        self._assert_atomic_failure(
            self.workspace.write(
                WorkspaceWriteRequest(
                    SandboxPath.resolve("/workspace/oversized"),
                    b"x" * 9,
                    AnyCurrentState(),
                )
            ),
            FileSizeLimitExceededError,
        )

    @invariant()
    def public_projection_matches_the_model(self) -> None:
        projection = self.run_async(_public_projection(self.workspace))
        assert projection.total_bytes == self._total_bytes()
        assert projection.node_count == self._node_count()
        assert projection.revision == self.revision
        assert projection.root_hash == _model_hash((), self.directories, self.files).value

        record_values = {record.path: record for record in projection.records}
        expected_paths = {_path(parts).value for parts in self.directories - {()}} | {
            _path(parts).value for parts in self.files
        }
        assert set(record_values) == expected_paths
        for parts, content in self.files.items():
            record = record_values[_path(parts).value]
            assert record.kind == NodeKind.FILE.value
            assert record.size_bytes == len(content)
            assert record.content_hash == ContentHash.from_bytes(content).value
            assert record.revision == self.revision
            assert record.content == content
        for parts in self.directories - {()}:
            record = record_values[_path(parts).value]
            assert record.kind == NodeKind.DIRECTORY.value
            assert record.size_bytes == 0
            assert (
                record.content_hash
                == _model_hash(
                    parts,
                    self.directories,
                    self.files,
                ).value
            )
            assert record.revision == self.revision
            assert record.content is None


def test_memory_workspace_state_machine() -> None:
    run_state_machine_as_test(MemoryWorkspaceStateMachine)
