"""Atomic in-memory implementation of the MemSandbox workspace."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass

from mem_sandbox.core.identifiers import Revision
from mem_sandbox.workspace.errors import (
    DestinationWithinSourceError,
    DirectoryNotEmptyError,
    FileEncodingError,
    FileSizeLimitExceededError,
    InvalidPatchError,
    InvalidRangeError,
    NodeLimitExceededError,
    NotADirectoryError,
    NotAFileError,
    PathAlreadyExistsError,
    PathNotFoundError,
    PreparedRestoreInvalid,
    ReadLimitExceededError,
    RestoreCandidateMismatch,
    RootModificationError,
    SamePathError,
    SnapshotCorruptError,
    StaleContentError,
    WorkspaceSizeLimitExceededError,
)
from mem_sandbox.workspace.hashing import hash_directory
from mem_sandbox.workspace.models import (
    AnyCurrentState,
    ContentHash,
    ContentHashMustEqual,
    CopyPathRequest,
    MakeDirectoryRequest,
    MovePathRequest,
    NodeKind,
    PatchedFile,
    PathMustNotExist,
    PreparedWorkspaceRestore,
    RemovePathRequest,
    WorkspaceAppendRequest,
    WorkspaceBinaryResult,
    WorkspaceEntry,
    WorkspaceLimits,
    WorkspaceMutation,
    WorkspacePatchRequest,
    WorkspacePatchResult,
    WorkspaceRangeRequest,
    WorkspaceRangeResult,
    WorkspaceSnapshotData,
    WorkspaceStats,
    WorkspaceTextResult,
    WorkspaceWriteRequest,
)
from mem_sandbox.workspace.patching import apply_file_patch, parse_unified_diff
from mem_sandbox.workspace.paths import SandboxPath
from mem_sandbox.workspace.snapshot_codec import (
    DecodedWorkspaceSnapshot,
    JsonWorkspaceSnapshotCodec,
    WorkspaceSnapshotEntry,
)
from mem_sandbox.workspace.text import split_normalized_lines


@dataclass(slots=True)
class _FileNode:
    content: bytes


@dataclass(slots=True)
class _DirectoryNode:
    children: dict[str, _Node]


type _Node = _FileNode | _DirectoryNode


@dataclass(frozen=True, slots=True)
class _WorkspaceState:
    root: _DirectoryNode
    stats: WorkspaceStats


class MemoryWorkspace:
    """A deterministic virtual filesystem protected by one async state lock."""

    def __init__(self, limits: WorkspaceLimits | None = None) -> None:
        self._limits = limits or WorkspaceLimits()
        self._state_lock = asyncio.Lock()
        self._snapshot_codec = JsonWorkspaceSnapshotCodec()
        self._restore_token = object()
        root = _DirectoryNode(children={})
        self._state = self._state_for_root(root, Revision.initial())

    @property
    def limits(self) -> WorkspaceLimits:
        """Return immutable configured limits."""
        return self._limits

    def resolve_path(
        self,
        value: str,
        *,
        cwd: SandboxPath | None = None,
    ) -> SandboxPath:
        """Resolve a path using this workspace's configured limits."""
        return SandboxPath.resolve(
            value,
            cwd=cwd,
            max_path_bytes=self._limits.max_path_bytes,
            max_segment_bytes=self._limits.max_segment_bytes,
        )

    async def stats(self) -> WorkspaceStats:
        """Return committed counters and hashes."""
        async with self._state_lock:
            return self._state.stats

    async def stat(self, path: SandboxPath) -> WorkspaceEntry:
        """Return immutable metadata for one node."""
        self._validate_path(path)
        async with self._state_lock:
            node = _get_node(self._state.root, path)
            return _entry(path, node, self._state.stats.revision)

    async def list(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]:
        """List direct children in stable lexical order."""
        self._validate_path(path)
        async with self._state_lock:
            node = _get_node(self._state.root, path)
            if not isinstance(node, _DirectoryNode):
                raise NotADirectoryError(f"{path} is not a directory")
            revision = self._state.stats.revision
            return tuple(
                _entry(path.join(name), child, revision)
                for name, child in sorted(node.children.items())
            )

    async def read_bytes(self, path: SandboxPath) -> WorkspaceBinaryResult:
        """Read one complete file as immutable bytes."""
        self._validate_path(path)
        async with self._state_lock:
            node = _require_file(_get_node(self._state.root, path), path)
            return WorkspaceBinaryResult(
                path=path,
                content=node.content,
                content_hash=ContentHash.from_bytes(node.content),
                revision=self._state.stats.revision,
            )

    async def read_text(self, path: SandboxPath) -> WorkspaceTextResult:
        """Read one complete UTF-8 file."""
        self._validate_path(path)
        async with self._state_lock:
            node = _require_file(_get_node(self._state.root, path), path)
            content = _decode_utf8(path, node.content)
            return WorkspaceTextResult(
                path=path,
                content=content,
                content_hash=ContentHash.from_bytes(node.content),
                revision=self._state.stats.revision,
            )

    async def read_range(self, request: WorkspaceRangeRequest) -> WorkspaceRangeResult:
        """Read one bounded one-based inclusive UTF-8 line range."""
        self._validate_path(request.path)
        async with self._state_lock:
            node = _require_file(_get_node(self._state.root, request.path), request.path)
            text = _decode_utf8(request.path, node.content)
            lines, _ = split_normalized_lines(text)
            total_lines = len(lines)

            if total_lines == 0:
                return WorkspaceRangeResult(
                    path=request.path,
                    content="",
                    start_line=1,
                    end_line=0,
                    total_lines=0,
                    content_hash=ContentHash.from_bytes(node.content),
                    revision=self._state.stats.revision,
                )
            if request.start_line > total_lines:
                raise InvalidRangeError(
                    f"start line {request.start_line} is outside a {total_lines}-line file"
                )

            end_line = min(request.end_line or total_lines, total_lines)
            line_count = end_line - request.start_line + 1
            if line_count > self._limits.max_read_lines:
                raise ReadLimitExceededError(
                    f"range contains {line_count} lines; limit is "
                    f"{self._limits.max_read_lines} lines"
                )

            content = "\n".join(lines[request.start_line - 1 : end_line])
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > self._limits.max_read_bytes:
                raise ReadLimitExceededError(
                    f"range contains {content_bytes} bytes; limit is "
                    f"{self._limits.max_read_bytes} bytes"
                )

            return WorkspaceRangeResult(
                path=request.path,
                content=content,
                start_line=request.start_line,
                end_line=end_line,
                total_lines=total_lines,
                content_hash=ContentHash.from_bytes(node.content),
                revision=self._state.stats.revision,
            )

    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation:
        """Create one directory atomically."""
        self._validate_path(request.path)
        if request.path.is_root:
            raise PathAlreadyExistsError(f"{request.path} already exists")

        async with self._state_lock:
            if _try_get_node(self._state.root, request.path) is not None:
                raise PathAlreadyExistsError(f"{request.path} already exists")

            root = copy.deepcopy(self._state.root)
            parent = _ensure_parent(root, request.path, request.create_parents)
            parent.children[request.path.name] = _DirectoryNode(children={})
            next_state = self._commit(root)
            current_hash = _node_hash(_get_node(next_state.root, request.path))
            return WorkspaceMutation(
                path=request.path,
                created=True,
                changed=True,
                previous_hash=None,
                current_hash=current_hash,
                stats=next_state.stats,
            )

    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation:
        """Create or replace one complete file atomically."""
        self._validate_path(request.path)
        if request.path.is_root:
            raise NotAFileError(f"{request.path} is not a file")
        if len(request.content) > self._limits.max_file_bytes:
            raise FileSizeLimitExceededError(
                f"file contains {len(request.content)} bytes; limit is "
                f"{self._limits.max_file_bytes} bytes"
            )

        async with self._state_lock:
            existing = _try_get_node(self._state.root, request.path)
            previous_hash = _validate_write_precondition(
                request.path,
                existing,
                request.precondition,
            )

            root = copy.deepcopy(self._state.root)
            parent = _ensure_parent(root, request.path, request.create_parents)
            parent.children[request.path.name] = _FileNode(content=request.content)
            next_state = self._commit(root)
            current_hash = ContentHash.from_bytes(request.content)
            return WorkspaceMutation(
                path=request.path,
                created=existing is None,
                changed=True,
                previous_hash=previous_hash,
                current_hash=current_hash,
                stats=next_state.stats,
            )

    async def append(self, request: WorkspaceAppendRequest) -> WorkspaceMutation:
        """Append bytes through one atomic workspace mutation."""
        self._validate_path(request.path)
        if request.path.is_root:
            raise NotAFileError(f"{request.path} is not a file")

        async with self._state_lock:
            existing = _try_get_node(self._state.root, request.path)
            previous_hash = _validate_write_precondition(
                request.path,
                existing,
                request.precondition,
            )
            previous_content = existing.content if isinstance(existing, _FileNode) else b""
            content = previous_content + request.content
            if len(content) > self._limits.max_file_bytes:
                raise FileSizeLimitExceededError(
                    f"file contains {len(content)} bytes; limit is "
                    f"{self._limits.max_file_bytes} bytes"
                )

            root = copy.deepcopy(self._state.root)
            parent = _ensure_parent(root, request.path, request.create_parents)
            parent.children[request.path.name] = _FileNode(content=content)
            next_state = self._commit(root)
            return WorkspaceMutation(
                path=request.path,
                created=existing is None,
                changed=True,
                previous_hash=previous_hash,
                current_hash=ContentHash.from_bytes(content),
                stats=next_state.stats,
            )

    async def remove(self, request: RemovePathRequest) -> WorkspaceMutation:
        """Remove one file or directory atomically."""
        self._validate_path(request.path)
        if request.path.is_root:
            raise RootModificationError("the workspace root cannot be removed")

        async with self._state_lock:
            existing = _try_get_node(self._state.root, request.path)
            if existing is None:
                if request.missing_ok:
                    return WorkspaceMutation(
                        path=request.path,
                        created=False,
                        changed=False,
                        previous_hash=None,
                        current_hash=None,
                        stats=self._state.stats,
                    )
                raise PathNotFoundError(f"{request.path} does not exist")
            if isinstance(existing, _DirectoryNode) and existing.children and not request.recursive:
                raise DirectoryNotEmptyError(f"{request.path} is not empty")

            previous_hash = _node_hash(existing)
            root = copy.deepcopy(self._state.root)
            parent = _get_parent(root, request.path)
            del parent.children[request.path.name]
            next_state = self._commit(root)
            return WorkspaceMutation(
                path=request.path,
                created=False,
                changed=True,
                previous_hash=previous_hash,
                current_hash=None,
                stats=next_state.stats,
            )

    async def copy(self, request: CopyPathRequest) -> WorkspaceMutation:
        """Copy one complete subtree atomically."""
        self._validate_path(request.source)
        self._validate_path(request.destination)
        _validate_transfer_paths(request.source, request.destination)

        async with self._state_lock:
            source = _get_node(self._state.root, request.source)
            existing = _try_get_node(self._state.root, request.destination)
            if existing is not None and not request.overwrite:
                raise PathAlreadyExistsError(f"{request.destination} already exists")

            previous_hash = _node_hash(existing) if existing is not None else None
            root = copy.deepcopy(self._state.root)
            parent = _ensure_parent(root, request.destination, request.create_parents)
            parent.children[request.destination.name] = copy.deepcopy(source)
            next_state = self._commit(root)
            return WorkspaceMutation(
                path=request.destination,
                created=existing is None,
                changed=True,
                previous_hash=previous_hash,
                current_hash=_node_hash(_get_node(next_state.root, request.destination)),
                stats=next_state.stats,
            )

    async def move(self, request: MovePathRequest) -> WorkspaceMutation:
        """Move one complete subtree atomically."""
        self._validate_path(request.source)
        self._validate_path(request.destination)
        _validate_transfer_paths(request.source, request.destination)

        async with self._state_lock:
            _get_node(self._state.root, request.source)
            existing = _try_get_node(self._state.root, request.destination)
            if existing is not None and not request.overwrite:
                raise PathAlreadyExistsError(f"{request.destination} already exists")

            previous_hash = _node_hash(existing) if existing is not None else None
            root = copy.deepcopy(self._state.root)
            source_node = _get_node(root, request.source)
            destination_parent = _ensure_parent(
                root,
                request.destination,
                request.create_parents,
            )
            source_parent = _get_parent(root, request.source)
            del source_parent.children[request.source.name]
            destination_parent.children[request.destination.name] = source_node
            next_state = self._commit(root)
            return WorkspaceMutation(
                path=request.destination,
                created=existing is None,
                changed=True,
                previous_hash=previous_hash,
                current_hash=_node_hash(_get_node(next_state.root, request.destination)),
                stats=next_state.stats,
            )

    async def patch(self, request: WorkspacePatchRequest) -> WorkspacePatchResult:
        """Apply a constrained multi-file unified diff atomically."""
        try:
            patch_bytes = len(request.patch.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise InvalidPatchError("patch must be valid UTF-8 text") from error
        if patch_bytes > self._limits.max_patch_bytes:
            raise InvalidPatchError(
                f"patch contains {patch_bytes} bytes; limit is {self._limits.max_patch_bytes} bytes"
            )

        file_patches = parse_unified_diff(
            request.patch,
            lambda value: self.resolve_path(value, cwd=SandboxPath.root()),
        )
        patched_paths = {file_patch.path for file_patch in file_patches}
        expected_hashes = {item.path: item.content_hash for item in request.expected_hashes}
        unknown_preconditions = set(expected_hashes) - patched_paths
        if unknown_preconditions:
            raise InvalidPatchError("expected hash supplied for a path not present in the patch")

        async with self._state_lock:
            updates: list[tuple[SandboxPath, bytes, ContentHash, ContentHash]] = []
            for file_patch in file_patches:
                node = _require_file(
                    _get_node(self._state.root, file_patch.path),
                    file_patch.path,
                )
                previous_hash = ContentHash.from_bytes(node.content)
                expected_hash = expected_hashes.get(file_patch.path)
                if expected_hash is not None and expected_hash != previous_hash:
                    raise StaleContentError(
                        f"{file_patch.path} content hash does not match the expected hash"
                    )
                patched_text = apply_file_patch(
                    _decode_utf8(file_patch.path, node.content),
                    file_patch,
                )
                patched_content = patched_text.encode("utf-8")
                if len(patched_content) > self._limits.max_file_bytes:
                    raise FileSizeLimitExceededError(
                        f"{file_patch.path} contains {len(patched_content)} bytes; "
                        f"limit is {self._limits.max_file_bytes} bytes"
                    )
                updates.append(
                    (
                        file_patch.path,
                        patched_content,
                        previous_hash,
                        ContentHash.from_bytes(patched_content),
                    )
                )

            root = copy.deepcopy(self._state.root)
            for path, content, _, _ in updates:
                parent = _get_parent(root, path)
                parent.children[path.name] = _FileNode(content=content)
            next_state = self._commit(root)
            return WorkspacePatchResult(
                files=tuple(
                    PatchedFile(
                        path=path,
                        previous_hash=previous_hash,
                        current_hash=current_hash,
                    )
                    for path, _, previous_hash, current_hash in updates
                ),
                stats=next_state.stats,
            )

    async def export(self) -> WorkspaceSnapshotData:
        """Export one deterministic point-in-time workspace snapshot."""
        async with self._state_lock:
            entries = _snapshot_entries(self._state.root, SandboxPath.root())
            stats = self._state.stats
        return self._snapshot_codec.encode(entries, stats, self._limits)

    async def prepare_restore(
        self,
        data: WorkspaceSnapshotData,
        *,
        required_directory: SandboxPath,
    ) -> PreparedWorkspaceRestore:
        """Validate complete snapshot state without mutating the live workspace."""
        required_directory = self._validate_path(required_directory)
        decoded = self._snapshot_codec.decode(
            data,
            self._limits,
            lambda value: self.resolve_path(value),
        )
        root = _root_from_snapshot(decoded)
        candidate = self._state_for_root(root, decoded.stats.revision)
        if candidate.stats != decoded.stats:
            raise SnapshotCorruptError(
                "snapshot counters or hashes do not match reconstructed state"
            )
        required_node = _get_node(candidate.root, required_directory)
        if not isinstance(required_node, _DirectoryNode):
            raise NotADirectoryError(f"{required_directory} is not a directory")
        return PreparedWorkspaceRestore(self._restore_token, candidate)

    async def commit_restore(self, candidate: object) -> None:
        """Publish one workspace-bound prepared candidate atomically."""
        if not isinstance(candidate, PreparedWorkspaceRestore):
            raise PreparedRestoreInvalid("candidate must be a PreparedWorkspaceRestore")
        if not candidate.belongs_to(self._restore_token):
            raise RestoreCandidateMismatch("restore candidate belongs to another workspace")
        state = candidate.prepared_state()
        if not isinstance(state, _WorkspaceState):
            raise PreparedRestoreInvalid("restore candidate contains invalid workspace state")
        async with self._state_lock:
            self._state = state

    async def restore(self, data: WorkspaceSnapshotData) -> None:
        """Prepare and atomically replace workspace state from a snapshot."""
        candidate = await self.prepare_restore(
            data,
            required_directory=SandboxPath.root(),
        )
        await self.commit_restore(candidate)

    def _commit(self, root: _DirectoryNode) -> _WorkspaceState:
        next_state = self._state_for_root(root, self._state.stats.revision.next())
        self._state = next_state
        return next_state

    def _validate_path(self, path: object) -> SandboxPath:
        if not isinstance(path, SandboxPath):
            raise TypeError("path must be a SandboxPath")
        self.resolve_path(path.value)
        return path

    def _state_for_root(
        self,
        root: _DirectoryNode,
        revision: Revision,
    ) -> _WorkspaceState:
        total_bytes, node_count, root_hash = _measure_node(
            root,
            SandboxPath.root(),
            self._limits,
        )
        if total_bytes > self._limits.max_total_bytes:
            raise WorkspaceSizeLimitExceededError(
                f"workspace contains {total_bytes} bytes; limit is "
                f"{self._limits.max_total_bytes} bytes"
            )
        if node_count > self._limits.max_nodes:
            raise NodeLimitExceededError(
                f"workspace contains {node_count} nodes; limit is {self._limits.max_nodes} nodes"
            )
        return _WorkspaceState(
            root=root,
            stats=WorkspaceStats(
                total_bytes=total_bytes,
                node_count=node_count,
                revision=revision,
                root_hash=root_hash,
            ),
        )


def _validate_write_precondition(
    path: SandboxPath,
    existing: _Node | None,
    precondition: AnyCurrentState | PathMustNotExist | ContentHashMustEqual,
) -> ContentHash | None:
    if isinstance(existing, _DirectoryNode):
        raise NotAFileError(f"{path} is not a file")

    if isinstance(precondition, PathMustNotExist):
        if existing is not None:
            raise PathAlreadyExistsError(f"{path} already exists")
        return None

    if isinstance(precondition, ContentHashMustEqual):
        if existing is None:
            raise PathNotFoundError(f"{path} does not exist")
        current_hash = ContentHash.from_bytes(existing.content)
        if current_hash != precondition.expected_hash:
            raise StaleContentError(f"{path} content hash does not match the expected hash")
        return current_hash

    return ContentHash.from_bytes(existing.content) if existing is not None else None


def _validate_transfer_paths(source: SandboxPath, destination: SandboxPath) -> None:
    if source.is_root or destination.is_root:
        raise RootModificationError("the workspace root cannot be copied or moved")
    if source == destination:
        raise SamePathError("source and destination must differ")
    if source.is_ancestor_of(destination) or destination.is_ancestor_of(source):
        raise DestinationWithinSourceError("source and destination paths must not overlap")


def _get_node(root: _DirectoryNode, path: SandboxPath) -> _Node:
    node = _try_get_node(root, path)
    if node is None:
        raise PathNotFoundError(f"{path} does not exist")
    return node


def _try_get_node(root: _DirectoryNode, path: SandboxPath) -> _Node | None:
    node: _Node = root
    for segment in path.parts:
        if not isinstance(node, _DirectoryNode):
            raise NotADirectoryError(f"{path.parent} is not a directory")
        child = node.children.get(segment)
        if child is None:
            return None
        node = child
    return node


def _get_parent(root: _DirectoryNode, path: SandboxPath) -> _DirectoryNode:
    parent = _get_node(root, path.parent)
    if not isinstance(parent, _DirectoryNode):
        raise NotADirectoryError(f"{path.parent} is not a directory")
    return parent


def _ensure_parent(
    root: _DirectoryNode,
    path: SandboxPath,
    create_parents: bool,
) -> _DirectoryNode:
    node = root
    traversed = SandboxPath.root()
    for segment in path.parts[:-1]:
        traversed = traversed.join(segment)
        child = node.children.get(segment)
        if child is None:
            if not create_parents:
                raise PathNotFoundError(f"parent directory {traversed} does not exist")
            child = _DirectoryNode(children={})
            node.children[segment] = child
        if not isinstance(child, _DirectoryNode):
            raise NotADirectoryError(f"{traversed} is not a directory")
        node = child
    return node


def _require_file(node: _Node, path: SandboxPath) -> _FileNode:
    if not isinstance(node, _FileNode):
        raise NotAFileError(f"{path} is not a file")
    return node


def _decode_utf8(path: SandboxPath, content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FileEncodingError(f"{path} is not valid UTF-8") from error


def _entry(path: SandboxPath, node: _Node, revision: Revision) -> WorkspaceEntry:
    return WorkspaceEntry(
        path=path,
        kind=NodeKind.FILE if isinstance(node, _FileNode) else NodeKind.DIRECTORY,
        size_bytes=len(node.content) if isinstance(node, _FileNode) else 0,
        content_hash=_node_hash(node),
        revision=revision,
    )


def _measure_node(
    node: _Node,
    path: SandboxPath,
    limits: WorkspaceLimits,
) -> tuple[int, int, ContentHash]:
    if isinstance(node, _FileNode):
        size = len(node.content)
        if size > limits.max_file_bytes:
            raise FileSizeLimitExceededError(
                f"{path} contains {size} bytes; limit is {limits.max_file_bytes} bytes"
            )
        return size, 1, ContentHash.from_bytes(node.content)

    total_bytes = 0
    node_count = 1
    child_hashes: list[tuple[str, NodeKind, ContentHash]] = []
    for name, child in sorted(node.children.items()):
        child_path = path.join(name)
        child_bytes, child_nodes, child_hash = _measure_node(child, child_path, limits)
        total_bytes += child_bytes
        node_count += child_nodes
        child_hashes.append(
            (
                name,
                NodeKind.FILE if isinstance(child, _FileNode) else NodeKind.DIRECTORY,
                child_hash,
            )
        )
    return total_bytes, node_count, hash_directory(child_hashes)


def _node_hash(node: _Node) -> ContentHash:
    if isinstance(node, _FileNode):
        return ContentHash.from_bytes(node.content)
    child_hashes = [
        (
            name,
            NodeKind.FILE if isinstance(child, _FileNode) else NodeKind.DIRECTORY,
            _node_hash(child),
        )
        for name, child in sorted(node.children.items())
    ]
    return hash_directory(child_hashes)


def _snapshot_entries(
    directory: _DirectoryNode,
    path: SandboxPath,
) -> tuple[WorkspaceSnapshotEntry, ...]:
    entries: list[WorkspaceSnapshotEntry] = []
    for name, child in sorted(directory.children.items()):
        child_path = path.join(name)
        if isinstance(child, _FileNode):
            entries.append(
                WorkspaceSnapshotEntry(
                    path=child_path,
                    kind=NodeKind.FILE,
                    content=child.content,
                )
            )
        else:
            entries.append(
                WorkspaceSnapshotEntry(
                    path=child_path,
                    kind=NodeKind.DIRECTORY,
                )
            )
            entries.extend(_snapshot_entries(child, child_path))
    return tuple(entries)


def _root_from_snapshot(decoded: DecodedWorkspaceSnapshot) -> _DirectoryNode:
    root = _DirectoryNode(children={})
    for entry in decoded.entries:
        parent = _get_parent(root, entry.path)
        if entry.kind is NodeKind.DIRECTORY:
            parent.children[entry.path.name] = _DirectoryNode(children={})
        else:
            assert entry.content is not None
            parent.children[entry.path.name] = _FileNode(content=entry.content)
    return root
